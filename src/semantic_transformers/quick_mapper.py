"""
QuickMapper: turn any tabular file into RDF with a simple mapping config.

No schema, no JSONata transform, no custom parser required.  The user
provides a YAML config that names the columns and points each one at an
ontology class IRI and an optional QUDT unit.  Everything else is automatic.

Supported file formats
----------------------
    .csv                   Comma-separated values
    .tsv  /  .tab          Tab-separated values
    .txt                   Auto-sniffed (separator detected from content)
    .xlsx  /  .xls         Excel workbook (requires openpyxl)
    .parquet               Apache Parquet (requires pyarrow or fastparquet)
    .json                  JSON (array of records or any orient supported by pandas)

Mapping config format
---------------------
    # root_type is optional (defaults to dcat:Dataset)
    root_type: "http://www.w3.org/ns/dcat#Dataset"

    # label is optional (defaults to the file stem)
    label: "Hardness profile, sample 42"

    # file reading options (all optional)
    file:
      format:            auto   # auto | csv | tsv | excel | parquet | json
      separator:         ","    # csv/tsv/txt only; sniffed when omitted
      skip_rows:         0      # rows to skip before the header row
      skip_after_header: 0      # rows to skip right after the header (e.g. a units row)
      header_row:        0      # which row (after skipping) contains column names
      encoding:          utf-8
      sheet:             0      # Excel only: sheet name or 0-based index

    # metadata extraction — for files that have a header block before the data
    metadata:
      rows: 20           # number of leading metadata rows
      fields:
        "My label":
          property: "https://example.org/vocab/myProp"    # ontology property IRI
          # value becomes a plain literal (string or float)

        "Label with known unit":
          property: "https://example.org/vocab/temperature"
          unit: "http://qudt.org/vocab/unit/DEG_C"        # QUDT unit IRI
          # value and unit are stored as a value/unit pair

        "Label with file unit":
          property: "https://example.org/vocab/speed"
          unit_column: true     # read unit string from the row's third column
          # value and unit are stored as a value/unit pair

    # column annotations (only annotated columns get ontology triples)
    columns:
      Force:
        iri:  "https://w3id.org/pmd/tto/StandardForce"
        unit: "http://qudt.org/vocab/unit/N"        # optional
      Temperature:
        iri:  "https://example.org/vocab/Temperature"

Usage
-----
    from semantic_transformers import QuickMapper

    mapper = QuickMapper("mapping.yaml")
    result = mapper.run("my_data.xlsx")

    print(result.graph.serialize(format="turtle"))
    print(result.dataframe.head())
"""

from __future__ import annotations

import csv as _csv
import io
from pathlib import Path
from typing import Union

import rdflib
import yaml

from .transformer import TransformResult

# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------
_DCAT  = rdflib.Namespace("http://www.w3.org/ns/dcat#")
_DCT   = rdflib.Namespace("http://purl.org/dc/terms/")
_QUDT  = rdflib.Namespace("http://qudt.org/schema/qudt/")
_RDFS  = rdflib.RDFS
_RDF   = rdflib.RDF
_XSD   = rdflib.XSD

_DEFAULT_ROOT_TYPE = "http://www.w3.org/ns/dcat#Dataset"
_DEFAULT_BASE      = "https://example.org/datasets/"


class QuickMapper:
    """
    Converts any tabular file into an RDF graph using a lightweight YAML
    mapping config.  Returns a :class:`TransformResult` so it is a drop-in
    companion to :class:`Transformer`.

    Parameters
    ----------
    mapping:
        Path to a YAML mapping file, or a plain dict with the same structure.
    """

    def __init__(self, mapping: Union[str, Path, dict]) -> None:
        if isinstance(mapping, dict):
            self._config: dict = mapping
        else:
            self._config = yaml.safe_load(
                Path(mapping).read_text(encoding="utf-8")
            )

    # ------------------------------------------------------------------
    def run(self, file_path: Union[str, Path], **overrides) -> TransformResult:
        """
        Convert *file_path* to RDF.

        Keyword arguments override the corresponding top-level keys in the
        mapping config (e.g. ``label="Custom name"``).

        Returns
        -------
        TransformResult
            Same type as :meth:`Transformer.run`: graph, oold_doc, dataframe,
            column_iris, column_units.
        """
        path     = Path(file_path)
        config   = {**self._config, **overrides}
        file_cfg = dict(config.get("file", {}))

        # Sniff separator once; share between metadata read and data read.
        if "separator" not in file_cfg and _detect_format(path) in ("csv", "tsv", "txt"):
            file_cfg["separator"] = _sniff_separator(path, file_cfg.get("encoding", "utf-8"))

        # ── 1. Extract metadata rows (if configured) ──────────────────
        metadata_cfg = config.get("metadata", {})
        meta_fields  = metadata_cfg.get("fields", {})
        meta_raw: dict[str, tuple[str, str]] = {}
        if meta_fields:
            n_meta = metadata_cfg.get("rows", 0)
            if n_meta:
                meta_sep = metadata_cfg.get("separator") or file_cfg.get("separator") or "\t"
                meta_enc = metadata_cfg.get("encoding") or file_cfg.get("encoding", "utf-8")
                meta_raw = self._extract_metadata_raw(path, n_meta, meta_sep, meta_enc)

        # ── 2. Read the file into a DataFrame ────────────────────────
        df = self._read_file(path, file_cfg)

        # ── 3. Collect column annotations ────────────────────────────
        columns_cfg  = config.get("columns", {})
        column_iris  = {
            col: cfg["iri"]
            for col, cfg in columns_cfg.items()
            if "iri" in cfg
        }
        column_units = {
            col: cfg["unit"]
            for col, cfg in columns_cfg.items()
            if "unit" in cfg
        }

        # ── 4. Build the RDF graph ────────────────────────────────────
        root_type  = config.get("root_type", _DEFAULT_ROOT_TYPE)
        label      = config.get("label", path.stem)
        base       = config.get("base", _DEFAULT_BASE)
        dataset_id = rdflib.URIRef(base + path.stem)

        g   = rdflib.Dataset()
        ctx = g.default_graph

        ctx.add((dataset_id, _RDF.type,   rdflib.URIRef(root_type)))
        ctx.add((dataset_id, _RDFS.label, rdflib.Literal(label)))
        ctx.add((dataset_id, _DCT.title,  rdflib.Literal(label)))
        ctx.add((dataset_id, _DCT.source, rdflib.Literal(str(path.name))))

        # Metadata triples
        extracted_meta: dict = {}
        if meta_fields and meta_raw:
            extracted_meta = self._add_metadata_triples(ctx, dataset_id, meta_raw, meta_fields)

        # Column descriptor triples
        for col_name, col_iri in column_iris.items():
            safe    = col_name.replace(" ", "_")
            col_uri = rdflib.URIRef(str(dataset_id) + "/" + safe)

            ctx.add((dataset_id, _DCAT.distribution, col_uri))
            ctx.add((col_uri,    _RDF.type,           rdflib.URIRef(col_iri)))
            ctx.add((col_uri,    _RDFS.label,         rdflib.Literal(col_name)))

            unit_iri = column_units.get(col_name)
            if unit_iri:
                ctx.add((col_uri, _QUDT.hasUnit, rdflib.URIRef(unit_iri)))

        # ── 5. Build a lightweight summary doc ───────────────────────
        oold_doc = {
            "id":       str(dataset_id),
            "type":     root_type,
            "label":    label,
            "source":   str(path.name),
            "metadata": extracted_meta,
            "columns":  {
                col: {"iri": iri, **({"unit": column_units[col]} if col in column_units else {})}
                for col, iri in column_iris.items()
            },
        }

        return TransformResult(
            graph        = g,
            oold_doc     = oold_doc,
            dataframe    = df,
            column_iris  = column_iris,
            column_units = column_units,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_metadata_raw(
        self, path: Path, n_rows: int, sep: str, enc: str
    ) -> dict[str, tuple[str, str]]:
        """Read first n_rows and return {label: (value_str, unit_str)}."""
        result: dict[str, tuple[str, str]] = {}
        with path.open(encoding=enc) as fh:
            reader = _csv.reader(fh, delimiter=sep, quotechar='"')
            for i, row in enumerate(reader):
                if i >= n_rows:
                    break
                if not row:
                    continue
                label = row[0].strip()
                value = row[1].strip() if len(row) > 1 else ""
                unit  = row[2].strip() if len(row) > 2 else ""
                result[label] = (value, unit)
        return result

    def _add_metadata_triples(
        self,
        ctx: rdflib.Graph,
        root: rdflib.URIRef,
        meta_raw: dict[str, tuple[str, str]],
        fields_cfg: dict,
    ) -> dict:
        """
        Add metadata triples to ctx.  Returns extracted {label: ...} for oold_doc.

        - Plain value  → ``<root> <property> <literal>``
        - Value + unit → ``<root> <property> [rdf:value <v>; qudt:hasUnit <unit_iri>]``
                      or ``<root> <property> [rdf:value <v>; qudt:unit "unit_str"]``
        """
        extracted: dict = {}
        for label, field_cfg in fields_cfg.items():
            if label not in meta_raw:
                continue
            value_str, file_unit = meta_raw[label]
            if not value_str:
                continue

            pred_iri = field_cfg.get("property") or field_cfg.get("iri")
            if not pred_iri:
                continue
            pred = rdflib.URIRef(pred_iri)

            try:
                lit = rdflib.Literal(float(value_str))
            except ValueError:
                lit = rdflib.Literal(value_str)

            unit_iri      = field_cfg.get("unit")
            use_file_unit = field_cfg.get("unit_column", False) and file_unit

            if unit_iri or use_file_unit:
                bn = rdflib.BNode()
                ctx.add((root, pred, bn))
                ctx.add((bn, _RDF.value, lit))
                if unit_iri:
                    ctx.add((bn, _QUDT.hasUnit, rdflib.URIRef(unit_iri)))
                else:
                    ctx.add((bn, _QUDT.unit, rdflib.Literal(file_unit)))
                extracted[label] = {"value": value_str, "unit": unit_iri or file_unit}
            else:
                ctx.add((root, pred, lit))
                extracted[label] = {"value": value_str}

        return extracted

    def _read_file(self, path: Path, file_cfg: dict):
        """Read *path* into a pandas DataFrame using *file_cfg* hints."""
        import pandas as pd

        fmt = file_cfg.get("format", "auto")
        if fmt == "auto":
            fmt = _detect_format(path)

        skip   = file_cfg.get("skip_rows",  0)
        header = file_cfg.get("header_row", 0)
        enc    = file_cfg.get("encoding",   "utf-8")

        if fmt in ("csv", "tsv", "txt"):
            sep = file_cfg.get("separator")
            if sep is None:
                sep = _sniff_separator(path, enc)

            skip_after = file_cfg.get("skip_after_header", 0)
            if skip_after:
                # Build an explicit skip list: metadata rows + unit rows after header.
                skip_list = list(range(int(skip))) + [
                    int(skip) + int(header) + i + 1 for i in range(int(skip_after))
                ]
                return pd.read_csv(path, sep=sep, skiprows=skip_list, header=0, encoding=enc)

            return pd.read_csv(
                path,
                sep      = sep,
                skiprows = skip,
                header   = header,
                encoding = enc,
            )

        if fmt == "excel":
            sheet = file_cfg.get("sheet", 0)
            return pd.read_excel(
                path,
                sheet_name = sheet,
                skiprows   = skip,
                header     = header,
            )

        if fmt == "parquet":
            return pd.read_parquet(path)

        if fmt == "json":
            orient = file_cfg.get("orient", None)
            return pd.read_json(path, orient=orient)

        raise ValueError(
            f"Unsupported format '{fmt}'. "
            "Supported: csv, tsv, txt, excel, parquet, json."
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    mapping = {
        ".csv":     "csv",
        ".tsv":     "tsv",
        ".tab":     "tsv",
        ".txt":     "txt",
        ".xlsx":    "excel",
        ".xls":     "excel",
        ".xlsm":    "excel",
        ".parquet": "parquet",
        ".json":    "json",
    }
    return mapping.get(suffix, "csv")


def _sniff_separator(path: Path, encoding: str) -> str:
    """Read the first 4 KB and ask csv.Sniffer to detect the delimiter."""
    try:
        sample = path.read_bytes()[:4096].decode(encoding, errors="replace")
        dialect = _csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except _csv.Error:
        return ","  # safe fallback
