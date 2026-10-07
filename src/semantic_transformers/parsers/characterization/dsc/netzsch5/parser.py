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

Header extraction
-----------------
All #KEY: VALUE header fields are parsed and stored in simplified_json.
For DATA COMPARE exports (two files side-by-side), only the first column
of values is used (the sample file; the second column is the sapphire reference).

Scalar results
--------------
result_unit in the simplified_json results array uses vocabulary labels from
the measurement-unit vocabulary service (e.g. "Degree Celsius (°C)") — the
SDK resolves these to QUDT IRIs automatically. No hardcoded unit codes.
Scalar results (onset/peak temperature, enthalpy, etc.) are NOT extracted
automatically — they come from analysis software (NETZSCH Proteus).
Add them to the simplified_json before calling Transformer.run(), or pass
them as overrides.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
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
# Low-level parsing helpers
# ---------------------------------------------------------------------------

def _normalize_key(k: str) -> str:
    """Replace NETZSCH5 non-standard special-char bytes with Unicode equivalents."""
    return k.replace("\x91", "µ").replace("\x9b", "°")


def _parse_header(path: Path) -> dict[str, str]:
    """
    Parse #KEY: VALUE metadata lines. Stops at ## data-section marker.

    For DATA COMPARE exports (two side-by-side value columns per key),
    only the first value column is retained (sample file; sapphire is second).
    Non-standard NETZSCH5 byte sequences for µ and ° are normalized in keys.
    """
    metadata: dict[str, str] = {}
    with path.open(encoding="latin-1") as f:
        for line in f:
            line = line.rstrip()
            if line.startswith("##"):
                break
            if line.startswith("#"):
                m = re.match(r"^#+([^:]+):\s+(.+)$", line)
                if m:
                    raw_key = m.group(1).strip()
                    key = _normalize_key(raw_key)
                    # DATA COMPARE: two tab-separated values per key; keep first.
                    value = m.group(2).split("\t")[0].strip()
                    metadata[key] = value
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


def _parse_range(range_str: str) -> dict[str, float | None]:
    """
    Parse '25,0°C/50,0(K/min)/550,0°C' → {start_temp, heating_rate, end_temp}.

    Returns floats, not strings.
    """
    clean = (range_str
             .replace(",", ".")
             .replace("\x9bC", "")
             .replace("°C", "")
             .replace("(K/min)", ""))
    parts = clean.split("/")
    result: dict[str, float | None] = {}
    try:
        if len(parts) >= 1:
            result["start_temp"] = float(parts[0].strip())
        if len(parts) >= 2:
            result["heating_rate"] = float(parts[1].strip())
        if len(parts) >= 3:
            result["end_temp"] = float(parts[2].strip())
    except (ValueError, IndexError):
        pass
    return result


def _parse_datetime(dt_str: str) -> str | None:
    """
    Parse NETZSCH5 datetime 'DD.MM.YYYY HH:MM:SS (UTC±N)' to ISO 8601.

    Returns None if the format doesn't match.
    """
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4}) (\d{2}):(\d{2}):(\d{2}) \(UTC([+-]\d+)\)", dt_str)
    if not m:
        return None
    day, month, year, hour, minute, second, tz_off = m.groups()
    try:
        tz = timezone(timedelta(hours=int(tz_off)))
        dt = datetime(int(year), int(month), int(day),
                      int(hour), int(minute), int(second), tzinfo=tz)
        return dt.isoformat()
    except (ValueError, OverflowError):
        return None


def _parse_float(s: str) -> float | None:
    """Parse a string to float; return None on failure."""
    try:
        return float(s.replace(",", "."))
    except (ValueError, AttributeError):
        return None


def _parse_int(s: str) -> int | None:
    """Parse a string to int; return None on failure."""
    try:
        return int(s)
    except (ValueError, AttributeError):
        return None


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
      - simplified_json: full instrument provenance and measurement conditions
        extracted from the file header (operator, lab, datetime, sample mass,
        crucible, atmosphere, calibration files, heating program, etc.)
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
          - simplified_json: full provenance + measurement conditions + file metadata,
            ready for Transformer.run() after adding results[]
          - timeseries: DataFrame (Temperature + DSC sample heat-flow columns)
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

        # ── Provenance ────────────────────────────────────────────────────────
        dt_raw = meta.get("DATE/TIME", "")
        dt_iso = _parse_datetime(dt_raw)
        if dt_iso:
            simplified["measurement_datetime"] = dt_iso
        elif dt_raw:
            simplified["measurement_datetime"] = dt_raw

        if meta.get("OPERATOR"):
            simplified["operator"]   = meta["OPERATOR"]
        if meta.get("LABORATORY"):
            simplified["laboratory"] = meta["LABORATORY"]
        if meta.get("PROJECT"):
            simplified["project_id"] = meta["PROJECT"]
        if meta.get("FILE"):
            simplified["source_file"] = meta["FILE"]
        if meta.get("IDENTITY"):
            simplified["identity"]   = meta["IDENTITY"]
        remark = meta.get("REMARK", "").strip()
        if remark:
            simplified["remark"]     = remark

        # ── Sample / specimen ────────────────────────────────────────────────
        if meta.get("SAMPLE"):
            simplified["sample_name"]   = meta["SAMPLE"]
        mass = _parse_float(meta.get("SAMPLE MASS /mg", ""))
        if mass is not None:
            simplified["sample_mass_mg"] = mass
        if meta.get("MATERIAL"):
            simplified["material"]      = meta["MATERIAL"]
        ref_sample = meta.get("REFERENCE", "").strip()
        if ref_sample:
            simplified["reference_sample"] = ref_sample
        ref_mass = _parse_float(meta.get("REFERENCE MASS /mg", ""))
        if ref_mass is not None:
            simplified["reference_mass_mg"] = ref_mass
        sc_mass = _parse_float(meta.get("SAMPLE CRUCIBLE MASS /mg", ""))
        if sc_mass is not None:
            simplified["sample_crucible_mass_mg"] = sc_mass
        rc_mass = _parse_float(meta.get("REFERENCE CRUCIBLE MASS /mg", ""))
        if rc_mass is not None:
            simplified["reference_crucible_mass_mg"] = rc_mass

        # ── Measurement conditions ────────────────────────────────────────────
        if meta.get("TYPE OF CRUCIBLE"):
            simplified["crucible_type"]  = meta["TYPE OF CRUCIBLE"]
        if meta.get("PURGE GAS 1"):
            simplified["purge_gas"]      = meta["PURGE GAS 1"]
        flow = _parse_float(meta.get("FLOW RATE 1 /(ml/min)", ""))
        if flow is not None:
            simplified["purge_gas_flow_rate_ml_min"] = flow
        if r.get("heating_rate") is not None:
            simplified["heating_rate_k_min"]     = r["heating_rate"]
        if r.get("start_temp") is not None:
            simplified["temperature_start_degC"] = r["start_temp"]
        if r.get("end_temp") is not None:
            simplified["temperature_end_degC"]   = r["end_temp"]
        if meta.get("SEGMENT"):
            simplified["segment"]        = meta["SEGMENT"]
        mrange = _parse_float(meta.get("M.RANGE /µV", ""))
        if mrange is not None:
            simplified["measurement_range_uV"] = mrange
        exo = _parse_int(meta.get("EXO", ""))
        if exo is not None:
            simplified["exo_sign"] = exo

        # ── Instrument & calibration ──────────────────────────────────────────
        if manufacturer:
            simplified["manufacturer"]    = manufacturer
        if model:
            simplified["instrument_model"] = model
        if meta.get("CORR. FILE"):
            simplified["baseline_correction_file"]     = meta["CORR. FILE"]
        if meta.get("TEMPCAL"):
            simplified["temperature_calibration_file"] = meta["TEMPCAL"]
        if meta.get("SENSITIVITY"):
            simplified["sensitivity_calibration_file"] = meta["SENSITIVITY"]
        if meta.get("CORR. CODE"):
            simplified["correction_code"] = meta["CORR. CODE"]
        tau = meta.get("TAU-R", "").strip()
        if tau and tau != "---":
            simplified["tau_r"] = tau

        # ── Timeseries ────────────────────────────────────────────────────────
        # Drop reference column from the timeseries delivered to the schema;
        # keep it in column_iris/column_units for IRI completeness.
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
