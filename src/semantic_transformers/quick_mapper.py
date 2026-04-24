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
    # root_type is optional (defaults to csvw:Table)
    root_type: "http://www.w3.org/ns/csvw#Table"

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
          unit_from_file: true  # read unit string from the row's third column
          # the string is looked up in the built-in alias table and stored as
          # obo:IAO_0000039 <IRI> when matched, or rdfs:label "string" as a fallback.
          # result.oold_doc["unit_resolutions"] shows what was resolved.

    # column annotations (only annotated columns get ontology triples)
    columns:
      Force:
        iri:  "https://w3id.org/pmd/tto/TTO_0000023"
        unit: "N"               # plain string — resolved to QUDT N automatically
      Extension:
        iri:  "https://w3id.org/pmd/tto/TTO_0000005"
        unit_from_file: true    # read unit string from the file's units row
        # requires skip_after_header >= 1 (the units row must be the skipped row)
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
_CSVW  = rdflib.Namespace("http://www.w3.org/ns/csvw#")
_DCT   = rdflib.Namespace("http://purl.org/dc/terms/")
_IAO   = rdflib.Namespace("http://purl.obolibrary.org/obo/IAO_")
_RDFS  = rdflib.RDFS
_RDF   = rdflib.RDF
_XSD   = rdflib.XSD

_DEFAULT_ROOT_TYPE = "http://www.w3.org/ns/csvw#Table"
_DEFAULT_BASE      = "https://example.org/datasets/"

# Default column-descriptor pattern — mirrors _DEFAULT_TIMESERIES_PATTERN in
# transformer.py.  Override per-mapping via a top-level ``column_pattern`` key.
_DEFAULT_COLUMN_PATTERN: dict = {
    "column_predicate":    "http://www.w3.org/ns/csvw#column",
    "column_type":         "http://www.w3.org/ns/csvw#Column",
    "column_name_predicate": "http://www.w3.org/ns/csvw#name",
    "unit_predicate":      "http://purl.obolibrary.org/obo/IAO_0000039",
}

# ---------------------------------------------------------------------------
# Built-in unit alias table
# ---------------------------------------------------------------------------
# Maps unit strings commonly written by lab instruments to their QUDT IRIs.
# Used when unit_column: true is set on a metadata field.  User-defined
# unit_aliases in the mapping config take priority over these entries.
# Covers QUDT v2.1 vocabulary; extend via unit_aliases for anything missing.

_QUDT_BASE = "http://qudt.org/vocab/unit/"

_BUILTIN_UNIT_ALIASES: dict[str, str] = {
    # Length
    "m":        _QUDT_BASE + "M",
    "cm":       _QUDT_BASE + "CentiM",
    "mm":       _QUDT_BASE + "MilliM",
    "µm":       _QUDT_BASE + "MicroM",
    "um":       _QUDT_BASE + "MicroM",       # ASCII fallback for µm
    # Velocity
    "m/s":      _QUDT_BASE + "M-PER-SEC",
    "mm/s":     _QUDT_BASE + "MilliM-PER-SEC",
    "mm/min":   _QUDT_BASE + "MilliM-PER-MIN",
    # Force
    "N":        _QUDT_BASE + "N",
    "kN":       _QUDT_BASE + "KiloN",
    "mN":       _QUDT_BASE + "MilliN",
    # Stress / pressure
    "Pa":       _QUDT_BASE + "PA",
    "kPa":      _QUDT_BASE + "KiloPA",
    "MPa":      _QUDT_BASE + "MegaPA",
    "GPa":      _QUDT_BASE + "GigaPA",
    "bar":      _QUDT_BASE + "BAR",
    "mbar":     _QUDT_BASE + "MilliBAR",
    # Time
    "s":        _QUDT_BASE + "SEC",
    "min":      _QUDT_BASE + "MIN",
    "h":        _QUDT_BASE + "HR",
    # Temperature
    "°C":       _QUDT_BASE + "DEG_C",
    "K":        _QUDT_BASE + "K",
    # Mass
    "kg":       _QUDT_BASE + "KiloGM",
    "g":        _QUDT_BASE + "GM",
    # Dimensionless
    "%":        _QUDT_BASE + "PERCENT",
    # Energy
    "J":        _QUDT_BASE + "J",
    "kJ":       _QUDT_BASE + "KiloJ",
    # Power
    "W":        _QUDT_BASE + "W",
    "kW":       _QUDT_BASE + "KiloW",
    # Torque
    "Nm":       _QUDT_BASE + "N-M",
    "N·m":      _QUDT_BASE + "N-M",
}


def _resolve_unit(unit_str: str) -> str | None:
    """Return a QUDT IRI for *unit_str* from :data:`_BUILTIN_UNIT_ALIASES`, or None."""
    return _BUILTIN_UNIT_ALIASES.get(unit_str.strip())


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
        self._col_pattern: dict = {
            **_DEFAULT_COLUMN_PATTERN,
            **self._config.get("column_pattern", {}),
        }

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

        # If any column requests unit_from_file, read the units row now
        file_col_units: dict[str, str] = {}
        if any(cfg.get("unit_from_file") for cfg in columns_cfg.values()):
            skip_after = int(file_cfg.get("skip_after_header", 0))
            if skip_after and _detect_format(path) in ("csv", "tsv", "txt"):
                file_col_units = self._extract_column_units_row(path, file_cfg)

        column_iris: dict[str, str | None] = {
            col: cfg.get("iri")
            for col, cfg in columns_cfg.items()
            if "iri" in cfg
        }
        column_units: dict[str, str] = {}
        col_unit_resolutions: dict[str, str | None] = {}
        for col, cfg in columns_cfg.items():
            if "unit" in cfg:
                u = cfg["unit"]
                if u and not u.startswith("http"):
                    u = _resolve_unit(u) or u
                column_units[col] = u
            elif cfg.get("unit_from_file"):
                file_unit_str = file_col_units.get(col, "")
                if file_unit_str:
                    resolved = _resolve_unit(file_unit_str)
                    col_unit_resolutions[file_unit_str] = resolved
                    column_units[col] = resolved if resolved else file_unit_str

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
        all_unit_resolutions: dict[str, str | None] = dict(col_unit_resolutions)
        if meta_fields and meta_raw:
            extracted_meta, meta_unit_resolutions = self._add_metadata_triples(
                ctx, dataset_id, meta_raw, meta_fields
            )
            all_unit_resolutions.update(meta_unit_resolutions)

        # Column descriptor triples — pattern driven by self._col_pattern
        cp        = self._col_pattern
        col_pred  = rdflib.URIRef(cp["column_predicate"])
        col_type  = rdflib.URIRef(cp["column_type"])
        name_pred = rdflib.URIRef(cp["column_name_predicate"])
        unit_pred = rdflib.URIRef(cp["unit_predicate"])

        all_annotated = set(column_iris.keys()) | set(column_units.keys())
        for col_name in all_annotated:
            col_iri = column_iris.get(col_name)
            safe    = col_name.replace(" ", "_")
            col_uri = rdflib.URIRef(str(dataset_id) + "/" + safe)

            ctx.add((dataset_id, col_pred,    col_uri))
            ctx.add((col_uri,    _RDF.type,   col_type))
            if col_iri:
                ctx.add((col_uri, _RDF.type,  rdflib.URIRef(col_iri)))
            ctx.add((col_uri,    _RDFS.label, rdflib.Literal(col_name)))
            ctx.add((col_uri,    name_pred,   rdflib.Literal(col_name)))

            unit_iri = column_units.get(col_name)
            if unit_iri and unit_iri.startswith("http"):
                ctx.add((col_uri, unit_pred, rdflib.URIRef(unit_iri)))

        # ── 5. Build a lightweight summary doc ───────────────────────
        oold_doc: dict = {
            "id":       str(dataset_id),
            "type":     root_type,
            "label":    label,
            "source":   str(path.name),
            "metadata": extracted_meta,
            "columns":  {
                col: {
                    **({"iri": column_iris[col]} if col in column_iris else {}),
                    **({"unit": column_units[col]} if col in column_units else {}),
                }
                for col in all_annotated
            },
        }
        if all_unit_resolutions:
            oold_doc["unit_resolutions"] = all_unit_resolutions
            self._print_unit_resolutions(all_unit_resolutions, path)

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

    def _extract_column_units_row(self, path: Path, file_cfg: dict) -> dict[str, str]:
        """
        Read the units row (first row after the column-names header) and return
        a dict mapping column name → unit string.

        This row is the one that ``skip_after_header: 1`` normally skips.  Only
        supported for text formats (CSV / TSV / TXT).
        """
        import pandas as pd

        skip   = int(file_cfg.get("skip_rows", 0))
        header = int(file_cfg.get("header_row", 0))
        enc    = file_cfg.get("encoding", "utf-8")
        sep    = file_cfg.get("separator") or _sniff_separator(path, enc)
        try:
            df = pd.read_csv(
                path,
                sep=sep,
                skiprows=skip,
                header=header,
                nrows=1,
                encoding=enc,
            )
            return {
                col: str(val).strip()
                for col, val in df.iloc[0].items()
                if str(val).strip() and str(val).strip().lower() != "nan"
            }
        except Exception:
            return {}

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
    ) -> tuple[dict, dict[str, str | None]]:
        """
        Add metadata triples to ctx.

        Returns ``(extracted, unit_resolutions)`` where ``extracted`` is the
        ``{label: ...}`` dict for oold_doc and ``unit_resolutions`` maps each
        unit string read from the file (unit_from_file: true) to its resolved QUDT
        IRI, or None when no match was found in the built-in alias table.

        - Plain value  → ``<root> <property> <literal>``
        - Value + unit → ``<root> <property> [rdf:value <v>; obo:IAO_0000039 <IRI>]``
          or  ``<root> <property> [rdf:value <v>; rdfs:label "string"]`` if unresolved.
        """
        extracted: dict = {}
        unit_resolutions: dict[str, str | None] = {}
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
            use_file_unit = field_cfg.get("unit_from_file", False) and file_unit

            # Resolve plain-string unit values (e.g. "°C", "mm") to QUDT IRIs
            if unit_iri and not unit_iri.startswith("http"):
                unit_iri = _resolve_unit(unit_iri) or unit_iri

            if unit_iri or use_file_unit:
                bn = rdflib.BNode()
                ctx.add((root, pred, bn))
                ctx.add((bn, _RDF.value, lit))
                stored_unit: str
                if unit_iri:
                    stored_unit = unit_iri
                    if unit_iri.startswith("http"):
                        ctx.add((bn, _IAO["0000039"], rdflib.URIRef(unit_iri)))
                    else:
                        ctx.add((bn, _RDFS.label, rdflib.Literal(unit_iri)))
                else:
                    resolved = _resolve_unit(file_unit)
                    unit_resolutions[file_unit] = resolved
                    if resolved:
                        stored_unit = resolved
                        ctx.add((bn, _IAO["0000039"], rdflib.URIRef(resolved)))
                    else:
                        stored_unit = file_unit
                        ctx.add((bn, _RDFS.label, rdflib.Literal(file_unit)))
                extracted[label] = {"value": value_str, "unit": stored_unit}
            else:
                ctx.add((root, pred, lit))
                extracted[label] = {"value": value_str}

        return extracted, unit_resolutions

    @staticmethod
    def _print_unit_resolutions(
        unit_resolutions: dict[str, str | None], path: Path
    ) -> None:
        print(f"QuickMapper: unit resolution for '{path.name}':")
        for unit_str, iri in unit_resolutions.items():
            if iri:
                print(f"  resolved   {unit_str!r:15}  ->  {iri}")
            else:
                print(f"  not found  {unit_str!r:15}  ->  stored as plain string")

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
