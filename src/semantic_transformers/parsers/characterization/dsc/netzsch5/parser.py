"""
NETZSCH5 DSC heat-flow file parser for the dataset/dsc/PMDCo semantic schema.

Reads NETZSCH Proteus ASCII export files (FORMAT=NETZSCH5, MTYPE=DSC) and
returns a ParseResult compatible with the semantic_transformers Transformer API.

File format
-----------
Header:    #KEY: VALUE lines (Latin-1 encoded, non-standard µ/° bytes)
Data marker: ## followed by tab-separated column headers
Data:      Tab-separated numeric rows

Non-standard character encoding:
  ° = \\x9b  (not the standard \\xb0)
  µ = \\x91  (not the standard \\xb5)
Files must be opened with encoding="latin-1".

Column naming
-------------
DATA COMPARE exports contain two identically named DSC/(µV/mg) columns
(sample + reference). The parser renames them to _sample and _reference
so downstream consumers have unique keys.

Unit labels
-----------
result_unit in the simplified_json results array uses vocabulary labels from
the measurement-unit vocabulary service (e.g. "Degree Celsius (°C)") — the
SDK resolves these to QUDT IRIs automatically. No hardcoded unit codes.

Parser class
------------
NETZSCH5DSCDatasetParser
    Implements the semantic_transformers Parser protocol for one NETZSCH5
    DSC heat-flow file.
    Produces a ParseResult matching the dataset/dsc/PMDCo simplified schema.
    Scalar results (onset/peak temperature, enthalpy, etc.) are NOT extracted
    automatically — they come from analysis software (NETZSCH Proteus).
    Add them to the simplified_json before calling Transformer.run(), or pass
    them as overrides.
"""

from __future__ import annotations

import re
from io import StringIO
from pathlib import Path

import pandas as pd

from semantic_transformers.parser import ParseResult


# ---------------------------------------------------------------------------
# DSC ontology and QUDT IRIs for timeseries columns
# ---------------------------------------------------------------------------

_DSC_NS   = "https://w3id.org/dsms/DSCMeasurement/"
_QUDT_NS  = "http://qudt.org/vocab/unit/"

# NETZSCH5 uses non-standard Latin-1 byte sequences for special characters.
_TEMP_COL_RAW   = "Temp./\x9bC"        # ° = \x9b in NETZSCH5
_DSC_COL_RAW    = "DSC/(\x91V/mg)"     # µ = \x91 in NETZSCH5

# Canonical names after renaming the duplicate DSC column
_TEMP_COL        = "Temp./°C"
_DSC_SAMPLE_COL  = "DSC/(µV/mg)_sample"
_DSC_REF_COL     = "DSC/(µV/mg)_reference"

COLUMN_IRIS: dict[str, str] = {
    _TEMP_COL:        _DSC_NS + "Temperature",
    _DSC_SAMPLE_COL:  _DSC_NS + "DSCHeatFlow",
    _DSC_REF_COL:     _DSC_NS + "DSCHeatFlow",
}

COLUMN_UNITS: dict[str, str] = {
    _TEMP_COL:        _QUDT_NS + "DEG_C",
    _DSC_SAMPLE_COL:  _QUDT_NS + "MicroV-PER-MilliGM",
    _DSC_REF_COL:     _QUDT_NS + "MicroV-PER-MilliGM",
}

_KNOWN_MANUFACTURERS = [
    "NETZSCH", "TA Instruments", "Mettler-Toledo", "Mettler",
    "Perkin-Elmer", "PerkinElmer", "Setaram", "Linseis", "Hitachi",
]


# ---------------------------------------------------------------------------
# Low-level parsing helpers (ported from dsc_post_processing.py)
# ---------------------------------------------------------------------------

def _parse_header(path: Path) -> dict[str, str]:
    """Parse #KEY: VALUE metadata lines. Stops at ## data-section marker."""
    metadata: dict[str, str] = {}
    with path.open(encoding="latin-1") as f:
        for line in f:
            line = line.rstrip()
            if line.startswith("##"):
                break
            if line.startswith("#"):
                m = re.match(r"^#+([^:]+):\s+(.+)$", line)
                if m:
                    # DATA COMPARE stores two tab-separated values per key; keep first.
                    metadata[m.group(1).strip()] = m.group(2).split("\t")[0].strip()
    return metadata


def _parse_timeseries(path: Path) -> tuple[list[str], pd.DataFrame]:
    """
    Parse the ## data section.

    Returns (column_names, dataframe). The duplicate DSC column is renamed:
    first occurrence → _sample, second → _reference.
    """
    column_names: list[str] = []
    data_lines:   list[str] = []
    in_data = False

    with path.open(encoding="latin-1") as f:
        for line in f:
            line = line.rstrip()
            if line.startswith("##"):
                header = line.lstrip("#").strip()
                column_names = [c.strip() for c in header.split("\t")]
                in_data = True
                continue
            if in_data and line.strip():
                data_lines.append(line.strip())

    # Rename duplicate DSC columns
    if column_names.count(_DSC_COL_RAW) == 2:
        seen = False
        renamed: list[str] = []
        for c in column_names:
            if c == _DSC_COL_RAW and not seen:
                renamed.append(_DSC_SAMPLE_COL)
                seen = True
            elif c == _DSC_COL_RAW:
                renamed.append(_DSC_REF_COL)
            elif c == _TEMP_COL_RAW:
                renamed.append(_TEMP_COL)
            else:
                renamed.append(c)
        column_names = renamed
    elif _TEMP_COL_RAW in column_names:
        column_names = [_TEMP_COL if c == _TEMP_COL_RAW else c for c in column_names]

    if not data_lines or not column_names:
        return column_names, pd.DataFrame()

    raw = "\t".join(column_names) + "\n" + "\n".join(data_lines)
    try:
        df = pd.read_csv(StringIO(raw), sep="\t")
    except Exception:
        return column_names, pd.DataFrame()

    return column_names, df


def _parse_range(range_str: str) -> dict[str, str]:
    """Parse '25,0°C/50,0(K/min)/550,0°C' → {start_temp, heating_rate, end_temp}."""
    clean = (range_str
             .replace(",", ".")
             .replace("\x9bC", "")
             .replace("°C", "")
             .replace("(K/min)", ""))
    parts = clean.split("/")
    result: dict[str, str] = {}
    if len(parts) >= 1:
        result["start_temp"] = parts[0].strip()
    if len(parts) >= 2:
        result["heating_rate"] = parts[1].strip()
    if len(parts) >= 3:
        result["end_temp"] = parts[2].strip()
    return result


def _split_manufacturer(instrument_str: str) -> tuple[str, str]:
    """Split 'NETZSCH DSC 404C' into ('NETZSCH', 'DSC 404C')."""
    for name in _KNOWN_MANUFACTURERS:
        if instrument_str.upper().startswith(name.upper()):
            return name, instrument_str[len(name):].strip()
    return "", instrument_str


def _dataset_name_from_meta(meta: dict[str, str], path: Path) -> str:
    """Derive a human-readable dataset name from metadata or filename."""
    sample = meta.get("SAMPLE", "").strip()
    identity = meta.get("IDENTITY", "").strip()
    if sample and identity:
        return f"{sample} — {identity}"
    if sample:
        return sample
    return path.stem


# ---------------------------------------------------------------------------
# Parser protocol implementation
# ---------------------------------------------------------------------------

class NETZSCH5DSCDatasetParser:
    """
    Parser for a single NETZSCH5 DSC heat-flow file (FORMAT=NETZSCH5, MTYPE=DSC).

    Produces a ParseResult matching the dataset/dsc/PMDCo simplified schema:
      - simplified_json: dataset_name, format, and file metadata
      - timeseries: DataFrame with Temperature and heat-flow columns
      - column_iris / column_units: DSC ontology + QUDT IRI mappings

    Scalar characterization results (onset temperature, enthalpy, etc.) are NOT
    extracted automatically — they come from analysis software post-processing.
    Populate simplified_json["results"] before calling Transformer.run(), or
    pass them as keyword overrides:

        parser = NETZSCH5DSCDatasetParser()
        result = parser.parse(path)
        result.simplified_json["results"] = [
            {"name": "Onset Temperature", "value": 280.5, "unit": "Degree Celsius (°C)"},
            {"name": "Enthalpy of Transformation", "value": -45.3, "unit": "Joule per Gram (J/g)"},
        ]
        transformer.run(result, specimen_iri="https://...")
    """

    def can_parse(self, path: Path) -> bool:
        """Return True if file is NETZSCH5 DSC format (not CP or other MTYPE)."""
        try:
            found_netzsch5 = False
            found_dsc = False
            with path.open(encoding="latin-1") as f:
                for line in f:
                    if line.startswith("##"):
                        break
                    if "FORMAT" in line and "NETZSCH5" in line:
                        found_netzsch5 = True
                    if "MTYPE" in line:
                        m = re.search(r"MTYPE:\s*(\S+)", line)
                        if m:
                            mtype_val = m.group(1).strip().upper()
                            if mtype_val == "DSC":
                                found_dsc = True
                            else:
                                return False
                    if found_netzsch5 and found_dsc:
                        return True
        except OSError:
            pass
        return found_netzsch5 and found_dsc

    def parse(self, path: Path) -> ParseResult:
        """
        Parse a NETZSCH5 DSC file.

        Returns a ParseResult with:
          - simplified_json: dataset_name, format, sample_name, instrument,
            heating_rate, temperature_range, purge_gas — ready for Transformer.run()
          - timeseries: DataFrame (Temperature, DSC sample + reference columns)
          - column_iris / column_units: semantic column mappings

        Add results[] to simplified_json before Transformer.run() to include
        scalar characterization results in the OO-LD output.
        """
        meta = _parse_header(path)
        column_names, df = _parse_timeseries(path)

        r = _parse_range(meta.get("RANGE", ""))
        manufacturer, model = _split_manufacturer(meta.get("INSTRUMENT", ""))

        simplified: dict = {
            "dataset_name": _dataset_name_from_meta(meta, path),
            "format":       "NETZSCH DSC TXT",
        }

        # Metadata fields stored as top-level keys for Transformer overrides
        if meta.get("SAMPLE"):
            simplified["sample_name"]   = meta["SAMPLE"]
        if manufacturer:
            simplified["manufacturer"]  = manufacturer
        if model:
            simplified["instrument_model"] = model
        if r.get("heating_rate"):
            simplified["heating_rate"]  = r["heating_rate"]
        if r.get("start_temp") and r.get("end_temp"):
            simplified["temperature_range"] = f"{r['start_temp']}–{r['end_temp']} °C"
        if meta.get("PURGE GAS 1"):
            simplified["purge_gas"]     = meta["PURGE GAS 1"]

        # Drop reference column from the timeseries delivered to data2rdf;
        # keep it in column_iris/column_units for completeness.
        if not df.empty and _DSC_REF_COL in df.columns:
            ts = df.drop(columns=[_DSC_REF_COL])
        else:
            ts = df if not df.empty else None

        return ParseResult(
            simplified_json=simplified,
            timeseries=ts,
            column_iris=COLUMN_IRIS,
            column_units=COLUMN_UNITS,
        )
