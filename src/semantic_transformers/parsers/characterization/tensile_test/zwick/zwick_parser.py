"""
Parser for Zwick/Roell tensile test CSV files.

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
Produces a ParseResult where:
  simplified_json  maps the metadata to the tensile-test/TTO simplified schema
  timeseries       is a pandas DataFrame with the original German column names
  column_iris      maps each column to its TTO class IRI  (from column_mapping.json)
  column_units     maps each column to its QUDT unit IRI  (from column_mapping.json)

Schema-driven type coercion
---------------------------
ZwickParser implements SchemaAwareParser so that when it is used with a
Transformer that has an input_schema (or semantic_schema), the Transformer
automatically calls configure(schema) to share the loaded schema dict.
The parser then reads the ``type`` for each field and casts accordingly
(``"number"`` / ``"integer"`` → float/int, ``"string"`` → str).

This means adding a new numeric or string field to the schema requires no code
change in the parser; only a new entry in meta_field_map is needed.  No schema path
needs to be passed to the parser directly.

Adapting to file variants
-------------------------
If your Zwick software version or machine template produces a different number
of metadata rows, uses different labels, or is localised to another language,
point the parser at a YAML config file instead of changing Python code:

    ZwickParser.from_config("my_parser_config.yaml")

The config file supports these keys (all optional):

    metadata_rows: 15               # rows before the column-header row
    strain_rate_label: null         # set to null to skip
    meta_field_map:
      Temperature: temperature
      Norm:        test_standard

Alternatively, pass the same values directly as keyword arguments:

    ZwickParser(metadata_rows=15, strain_rate_label=None)

For a completely different file structure, copy this file and override
_parse_metadata() and _parse_timeseries().  The Transformer and the schema
transform do not need to change.
"""

from __future__ import annotations

import csv
import datetime
import json
from pathlib import Path
from typing import Optional

import yaml

import pandas as pd

from semantic_transformers import ParseResult
from semantic_transformers.parser import SchemaAwareParser

# Default number of leading rows that are metadata before the column-header row.
_DEFAULT_METADATA_ROWS = 20

# Default mapping of metadata labels to simplified JSON field names.
#
# Keys are the German metadata labels as exported by Zwick software.
# Values are field names defined in schema.simplified.json.
# Adding a new field to the schema only requires a new entry here; no other
# code changes are needed; the Transformer shares the loaded schema via
# configure() so types are always read from the current schema version.
_DEFAULT_META_FIELD_MAP: dict[str, str] = {
    "Prüfnorm":              "test_standard",
    "Temperatur":            "temperature",
    "Prüfgeschwindigkeit":   "strain_rate",
    "Messlänge Standardweg": "gauge_length",
    "Vorkraft":              "preload",
}

# Labels whose unit column is extracted into a companion field.
# Maps: CSV label → (simplified JSON unit field, fallback unit string)
_DEFAULT_UNIT_FIELD_MAP: dict[str, tuple[str, str]] = {
    "Prüfgeschwindigkeit":   ("strain_rate_unit",   "mm/s"),
    "Messlänge Standardweg": ("gauge_length_unit",  "mm"),
    "Vorkraft":              ("preload_unit",        "MPa"),
}

# Kept for backwards compatibility with from_config() callers that set strain_rate_label.
_STRAIN_RATE_LABEL = "Prüfgeschwindigkeit"


class ZwickParser(SchemaAwareParser):
    """
    Reads a Zwick/Roell tensile test export and returns a ParseResult
    compatible with the ``characterization/tensile-test/TTO`` schema.

    When used with ``Transformer``, the Transformer automatically calls
    ``configure(schema)`` to share the loaded input schema; no schema path
    needs to be passed to this parser directly.

    Parameters
    ----------
    column_mapping_path:
        Path to the ``column_mapping.json`` file that lives next to this
        parser.  Pass ``None`` to use the default file next to this module.
    metadata_rows:
        Number of leading rows that form the metadata block before the
        column-header row.  Default: 20.
    meta_field_map:
        Mapping of metadata label strings to simplified JSON field names.
        Overrides the default German-label map when provided.
    unit_field_map:
        Mapping from CSV label to ``(simplified_field_name, fallback_unit)``.
        For each listed label the unit column of that metadata row is read and
        stored in the simplified JSON under *simplified_field_name*.  Overrides
        the default map when provided.
    strain_rate_label:
        Deprecated.  Use *unit_field_map* instead.  When provided and not
        present in *unit_field_map*, a ``("strain_rate_unit", "mm/s")`` entry
        is added automatically for backwards compatibility.
    """

    def __init__(
        self,
        column_mapping_path: Optional[Path] = None,
        *,
        metadata_rows: int = _DEFAULT_METADATA_ROWS,
        meta_field_map: Optional[dict[str, str]] = None,
        unit_field_map: Optional[dict[str, tuple[str, str]]] = None,
        strain_rate_label: Optional[str] = _STRAIN_RATE_LABEL,
    ) -> None:
        if column_mapping_path is None:
            column_mapping_path = Path(__file__).parent / "column_mapping.json"

        mapping = json.loads(column_mapping_path.read_text(encoding="utf-8"))
        self._col_iris:  dict[str, str] = {m["key"]: m["iri"]      for m in mapping}
        self._col_units: dict[str, str] = {m["key"]: m["unit_iri"] for m in mapping}

        self._metadata_rows  = metadata_rows
        self._meta_field_map = meta_field_map if meta_field_map is not None else _DEFAULT_META_FIELD_MAP

        if unit_field_map is not None:
            self._unit_field_map: dict[str, tuple[str, str]] = unit_field_map
        else:
            self._unit_field_map = dict(_DEFAULT_UNIT_FIELD_MAP)
            # Backwards-compat: honour a custom strain_rate_label if given.
            if strain_rate_label and strain_rate_label != _STRAIN_RATE_LABEL:
                self._unit_field_map[strain_rate_label] = ("strain_rate_unit", "mm/s")
            elif strain_rate_label is None:
                self._unit_field_map.pop(_STRAIN_RATE_LABEL, None)

        self._field_types: dict[str, str] = {}

    # ------------------------------------------------------------------

    def configure(self, schema: dict) -> None:
        """
        Receive the loaded input schema from Transformer.

        Called automatically by Transformer.__init__ when an input_schema or
        semantic_schema is provided.  Populates field type information used by
        _cast() so values are coerced to the types declared in the schema.
        """
        self._field_types = {
            name: prop.get("type", "string")
            for name, prop in schema.get("properties", {}).items()
        }

    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        column_mapping_path: Optional[Path] = None,
    ) -> "ZwickParser":
        """
        Create a ZwickParser from a YAML config file.

        Supported config keys (all optional)
        -------------------------------------
        metadata_rows (int):      rows before the column-header row
        strain_rate_label (str):  set to null to disable (deprecated, prefer unit_field_map)
        meta_field_map (dict):    label → field_name
        unit_field_map (dict):    label → {field: name, fallback: unit}

        Example
        -------
        metadata_rows: 15
        strain_rate_label: null
        meta_field_map:
          Temperature: temperature
          Norm: test_standard
        """
        cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}

        meta_field_map = None
        if "meta_field_map" in cfg:
            meta_field_map = dict(cfg["meta_field_map"])

        unit_field_map = None
        if "unit_field_map" in cfg:
            unit_field_map = {
                label: (entry["field"], entry.get("fallback", ""))
                for label, entry in cfg["unit_field_map"].items()
            }

        return cls(
            column_mapping_path = column_mapping_path,
            metadata_rows       = cfg.get("metadata_rows", _DEFAULT_METADATA_ROWS),
            meta_field_map      = meta_field_map,
            unit_field_map      = unit_field_map,
            strain_rate_label   = cfg.get("strain_rate_label", _STRAIN_RATE_LABEL),
        )

    # ------------------------------------------------------------------

    def parse(self, path: Path) -> ParseResult:
        with path.open(encoding="utf-8") as fh:
            rows = list(csv.reader(fh, delimiter="\t", quotechar='"'))

        meta_raw   = self._parse_metadata(rows)
        simplified = self._build_simplified_json(meta_raw, path)
        ts, headers = self._parse_timeseries(rows)

        col_iris  = {h: self._col_iris[h]  for h in headers if h in self._col_iris}
        col_units = {h: self._col_units[h] for h in headers if h in self._col_units}

        return ParseResult(
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

    @staticmethod
    def _excel_serial_to_iso(value_str: str) -> str | None:
        """
        Convert a Zwick/Excel date serial number to an ISO 8601 datetime string.

        Excel counts days from 1899-12-30 (the effective epoch after its
        off-by-one leap-year bug).  The fractional part encodes the time of day.
        Returns None if *value_str* cannot be parsed as a number.
        """
        try:
            serial = float(value_str)
        except (ValueError, TypeError):
            return None
        dt = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=serial)
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    def _cast(self, value_str: str, field_name: str) -> str | float | int:
        """
        Cast *value_str* to the type declared for *field_name* in the schema.
        Falls back to a heuristic (float if parseable, otherwise str) when
        configure() has not been called or the field is not listed.
        """
        field_type = self._field_types.get(field_name, "")
        if field_type in ("number", "integer"):
            try:
                return int(value_str) if field_type == "integer" else float(value_str)
            except ValueError:
                return value_str
        if field_type == "string":
            return value_str
        # No schema type known: try float, fall back to str.
        try:
            return float(value_str)
        except ValueError:
            return value_str

    def _build_simplified_json(
        self,
        meta: dict[str, tuple[str, str]],
        path: Path,
    ) -> dict:
        simplified: dict = {}

        # Derive test_name from the file stem (user can override via transformer.run).
        simplified["test_name"] = path.stem

        # Parse the test date (Datum/Uhrzeit) from its Excel serial-number format.
        if "Datum/Uhrzeit" in meta:
            date_str = meta["Datum/Uhrzeit"][0].strip()
            iso_dt = self._excel_serial_to_iso(date_str)
            if iso_dt:
                simplified["test_date"] = iso_dt

        for csv_label, json_field in self._meta_field_map.items():
            if csv_label not in meta:
                continue
            value_str, _unit_str = meta[csv_label]
            if not value_str:
                continue
            simplified[json_field] = self._cast(value_str, json_field)

        # For each field that carries a companion unit, read the unit column.
        for csv_label, (unit_field, fallback) in self._unit_field_map.items():
            if csv_label not in meta:
                continue
            unit_str = meta[csv_label][1]
            simplified[unit_field] = unit_str if unit_str else fallback

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
