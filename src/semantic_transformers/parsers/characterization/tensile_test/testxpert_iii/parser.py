"""
Parser for Zwick/Roell testXpert III tensile test exports.

File format
-----------
The testXpert III software exports a tab-separated file with two sections:

  Rows 1–N    Metadata block: each row is  "label" \\t value [\\t "unit"]
              The label is a quoted string (language depends on the software locale).
              The value is either a quoted string or a bare number.

  Row N+1     Column headers  (quoted, tab-separated)
  Row N+2     Column units    (quoted, tab-separated)
  Rows N+3+   Numeric data    (bare floats, tab-separated)

This base class contains no locale-specific strings.  The metadata label
strings, unit-field mapping, and column_mapping.json all vary by export
language and are provided by locale subclasses (e.g. ``de.TestXpertIIIParser``).

Output
------
Produces a ParseResult where:
  simplified_json  maps the metadata to the tensile-test/TTO simplified schema
  timeseries       is a pandas DataFrame with the original column names
  column_iris      maps each column to its TTO class IRI, or None for columns without a TTO v3.0.0 class  (from column_mapping.json)
  column_units     maps each column to its QUDT unit IRI  (from column_mapping.json)

Schema-driven type coercion
---------------------------
TestXpertIIIParser implements SchemaAwareParser so that when it is used with a
Transformer that has an input_schema (or semantic_schema), the Transformer
automatically calls configure(schema) to share the loaded schema dict.
The parser then reads the ``type`` for each field and casts accordingly
(``"number"`` / ``"integer"`` → float/int, ``"string"`` → str).

Adapting to file variants
-------------------------
If your testXpert III export has a different metadata row count, different
label names, or a different language, pass the locale-specific values as
constructor arguments or via a YAML config file:

    TestXpertIIIParser.from_config("my_parser_config.yaml")

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

_DEFAULT_METADATA_ROWS = 20


class TestXpertIIIParser(SchemaAwareParser):
    """
    Reads a Zwick/Roell testXpert III tensile test export and returns a
    ParseResult compatible with the ``characterization/tensile-test/TTO`` schema.

    This base class is locale-agnostic.  Use a locale subclass (e.g.
    ``testxpert_iii.de.TestXpertIIIParser``) for a ready-to-use configuration,
    or pass *meta_field_map*, *unit_field_map*, and *column_mapping_path*
    directly to configure a custom locale.

    When used with ``Transformer``, the Transformer automatically calls
    ``configure(schema)`` to share the loaded input schema; no schema path
    needs to be passed to this parser directly.

    Parameters
    ----------
    column_mapping_path:
        Path to a ``column_mapping.json`` file mapping column names to
        ontology class IRIs and QUDT unit IRIs.  Required unless a locale
        subclass provides a default.
    metadata_rows:
        Number of leading rows that form the metadata block before the
        column-header row.  Default: 20.
    meta_field_map:
        Mapping of metadata label strings to simplified JSON field names.
    unit_field_map:
        Mapping from CSV label to ``(simplified_field_name, fallback_unit)``.
        For each listed label the unit column of that metadata row is read and
        stored in the simplified JSON under *simplified_field_name*.
    date_label:
        Metadata label whose value is an Excel serial-number date.  When set,
        the value is converted to an ISO 8601 string and stored as
        ``test_date`` in the simplified JSON.
    """

    def __init__(
        self,
        column_mapping_path: Optional[Path] = None,
        *,
        metadata_rows: int = _DEFAULT_METADATA_ROWS,
        meta_field_map: Optional[dict[str, str]] = None,
        unit_field_map: Optional[dict[str, tuple[str, str]]] = None,
        date_label: Optional[str] = None,
    ) -> None:
        if column_mapping_path is None:
            raise ValueError(
                "column_mapping_path is required for the base TestXpertIIIParser. "
                "Use a locale subclass (e.g. testxpert_iii.de.TestXpertIIIParser) "
                "or pass column_mapping_path explicitly."
            )

        mapping = json.loads(column_mapping_path.read_text(encoding="utf-8"))
        # iri may be null for columns that have no applicable ontology class.
        self._col_iris:  dict[str, str | None] = {m["key"]: m.get("iri")      for m in mapping}
        self._col_units: dict[str, str]         = {m["key"]: m["unit_iri"]     for m in mapping if m.get("unit_iri")}

        self._metadata_rows  = metadata_rows
        self._meta_field_map = meta_field_map if meta_field_map is not None else {}
        self._unit_field_map = unit_field_map if unit_field_map is not None else {}
        self._date_label     = date_label
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
    ) -> "TestXpertIIIParser":
        """
        Create a parser from a YAML config file.

        Supported config keys (all optional)
        -------------------------------------
        metadata_rows (int):      rows before the column-header row
        meta_field_map (dict):    label → field_name
        unit_field_map (dict):    label → {field: name, fallback: unit}

        Locale subclasses may support additional keys (e.g. ``strain_rate_label``).
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

        kwargs: dict = dict(
            column_mapping_path = column_mapping_path,
            metadata_rows       = cfg.get("metadata_rows", _DEFAULT_METADATA_ROWS),
            meta_field_map      = meta_field_map,
            unit_field_map      = unit_field_map,
        )
        # Pass these only when explicitly set in the config so locale subclass
        # defaults are not overwritten by a missing key.
        for key in ("strain_rate_label", "date_label"):
            if key in cfg:
                kwargs[key] = cfg[key]

        return cls(**kwargs)

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
        Convert an Excel date serial number to an ISO 8601 datetime string.

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

        if self._date_label and self._date_label in meta:
            iso_dt = self._excel_serial_to_iso(meta[self._date_label][0].strip())
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
