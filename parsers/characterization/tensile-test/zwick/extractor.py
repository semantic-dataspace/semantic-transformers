"""
Extractor for Zwick/Roell tensile test CSV files.

File format
-----------
The Zwick software exports a tab-separated file with two sections:

  Rows 1–20   Metadata block: each row is  "label" \\t value [\\t "unit"]
              The label is always a quoted German string.
              The value is either a quoted string or a bare number.

  Row 21      Column headers  (quoted, tab-separated)
  Row 22      Column units    (quoted, tab-separated)
  Rows 23+    Numeric data    (bare floats, tab-separated)

Output
------
Produces an ExtractionResult where:
  simplified_json  maps the metadata to the tensile-test/TTO simplified schema
  timeseries       is a pandas DataFrame with the original German column names
  column_iris      maps each column to its TTO class IRI  (from column_mapping.json)
  column_units     maps each column to its QUDT unit IRI  (from column_mapping.json)

Adapting to file variants
-------------------------
If your Zwick software version or machine template produces a different number
of metadata rows, uses different labels, or is localised to another language,
point the extractor at a YAML config file instead of changing Python code:

    ZwickExtractor.from_config("my_parser_config.yaml")

The config file supports these keys (all optional):

    metadata_rows: 15               # rows before the column-header row
    strain_rate_label: null         # set to null to skip
    meta_field_map:
      Temperature: [temperature, float]
      Norm:        [test_standard, str]

Alternatively, pass the same values directly as keyword arguments:

    ZwickExtractor(metadata_rows=15, strain_rate_label=None)

For a completely different file structure, copy this file and override
_parse_metadata() and _parse_timeseries().  The Transformer and the schema
transform do not need to change.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Optional

import yaml

import pandas as pd

from semantic_transformers import ExtractionResult

# Default number of leading rows that are metadata before the column-header row.
_DEFAULT_METADATA_ROWS = 20

# Default mapping of metadata labels to (simplified_json_field, dtype).
# Only fields that have a direct equivalent in the TTO simplified schema are
# listed here.  All other metadata fields are ignored for the RDF pipeline
# (they remain readable in the raw file).
_DEFAULT_META_FIELD_MAP: dict[str, tuple[str, str]] = {
    "Prüfnorm":            ("test_standard",   "str"),
    "Temperatur":          ("temperature",     "float"),
    "Prüfgeschwindigkeit": ("strain_rate",     "float"),
}

# Label whose *unit* column is used as the strain_rate_unit.
_STRAIN_RATE_LABEL = "Prüfgeschwindigkeit"


class ZwickExtractor:
    """
    Reads a Zwick/Roell tensile test export and returns an ExtractionResult
    compatible with the ``characterization/tensile-test/TTO`` schema.

    Parameters
    ----------
    column_mapping_path:
        Path to the ``column_mapping.json`` file that lives next to this
        extractor.  Pass ``None`` to use the default file next to this module.
    metadata_rows:
        Number of leading rows that form the metadata block before the
        column-header row.  Default: 20.  Adjust for software versions or
        machine variants that produce a shorter or longer header block.
    meta_field_map:
        Mapping of metadata label strings to ``(simplified_json_field, dtype)``
        tuples.  Overrides the default German-label map when provided.  Useful
        when the exported labels differ (e.g. localised to another language or
        a custom template).
    strain_rate_label:
        The metadata label whose unit column is carried into the simplified
        JSON as ``strain_rate_unit``.  Default: ``"Prüfgeschwindigkeit"``.
        Set to ``None`` to skip.
    """

    def __init__(
        self,
        column_mapping_path: Optional[Path] = None,
        *,
        metadata_rows: int = _DEFAULT_METADATA_ROWS,
        meta_field_map: Optional[dict[str, tuple[str, str]]] = None,
        strain_rate_label: Optional[str] = _STRAIN_RATE_LABEL,
    ) -> None:
        if column_mapping_path is None:
            column_mapping_path = Path(__file__).parent / "column_mapping.json"

        mapping = json.loads(column_mapping_path.read_text(encoding="utf-8"))
        self._col_iris:  dict[str, str] = {m["key"]: m["iri"]      for m in mapping}
        self._col_units: dict[str, str] = {m["key"]: m["unit_iri"] for m in mapping}

        self._metadata_rows    = metadata_rows
        self._meta_field_map   = meta_field_map if meta_field_map is not None else _DEFAULT_META_FIELD_MAP
        self._strain_rate_label = strain_rate_label

    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        column_mapping_path: Optional[Path] = None,
    ) -> "ZwickExtractor":
        """
        Create a ZwickExtractor from a YAML config file.

        Supported config keys (all optional)
        -------------------------------------
        metadata_rows:    int   — rows before the column-header row
        strain_rate_label: str | null
        meta_field_map:   dict  — label → [json_field, dtype]

        Example
        -------
        metadata_rows: 15
        strain_rate_label: null
        meta_field_map:
          Temperature: [temperature, float]
          Norm: [test_standard, str]
        """
        cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}

        meta_field_map = None
        if "meta_field_map" in cfg:
            meta_field_map = {k: tuple(v) for k, v in cfg["meta_field_map"].items()}

        return cls(
            column_mapping_path = column_mapping_path,
            metadata_rows       = cfg.get("metadata_rows", _DEFAULT_METADATA_ROWS),
            meta_field_map      = meta_field_map,
            strain_rate_label   = cfg.get("strain_rate_label", _STRAIN_RATE_LABEL),
        )

    # ------------------------------------------------------------------

    def extract(self, path: Path) -> ExtractionResult:
        rows = list(
            csv.reader(
                path.open(encoding="utf-8"),
                delimiter = "\t",
                quotechar = '"',
            )
        )

        meta_raw   = self._parse_metadata(rows)
        simplified = self._build_simplified_json(meta_raw, path)
        ts, headers = self._parse_timeseries(rows)

        col_iris  = {h: self._col_iris[h]  for h in headers if h in self._col_iris}
        col_units = {h: self._col_units[h] for h in headers if h in self._col_units}

        return ExtractionResult(
            simplified_json = simplified,
            timeseries      = ts,
            column_iris     = col_iris,
            column_units    = col_units,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_metadata(self, rows: list[list[str]]) -> dict[str, tuple[str, str]]:
        """Return {label: (value_str, unit_str)} for the metadata block."""
        result: dict[str, tuple[str, str]] = {}
        for row in rows[:self._metadata_rows]:
            if not row:
                continue
            label = row[0].strip()
            value = row[1].strip() if len(row) > 1 else ""
            unit  = row[2].strip() if len(row) > 2 else ""
            result[label] = (value, unit)
        return result

    def _build_simplified_json(
        self,
        meta: dict[str, tuple[str, str]],
        path: Path,
    ) -> dict:
        simplified: dict = {}

        # Derive test_name from the file stem (user can override via transformer.run).
        simplified["test_name"] = path.stem

        for csv_label, (json_field, dtype) in self._meta_field_map.items():
            if csv_label not in meta:
                continue
            value_str, unit_str = meta[csv_label]
            if not value_str:
                continue
            if dtype == "float":
                try:
                    simplified[json_field] = float(value_str)
                except ValueError:
                    pass
            else:
                simplified[json_field] = value_str

        # Carry the testing-rate unit separately so the transform can use it.
        if self._strain_rate_label and self._strain_rate_label in meta:
            unit_str = meta[self._strain_rate_label][1]
            simplified["strain_rate_unit"] = unit_str or "mm/s"

        return simplified

    def _parse_timeseries(
        self,
        rows: list[list[str]],
    ) -> tuple[pd.DataFrame | None, list[str]]:
        """Parse the column-header row, unit row, and numeric data rows."""
        if len(rows) <= self._metadata_rows + 2:
            return None, []

        headers   = [h.strip() for h in rows[self._metadata_rows]]
        data_rows = rows[self._metadata_rows + 2:]   # skip the units row

        records: list[list[float]] = []
        for row in data_rows:
            try:
                records.append([float(v) for v in row])
            except (ValueError, TypeError):
                continue

        if not records:
            return None, headers

        return pd.DataFrame(records, columns=headers), headers
