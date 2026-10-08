"""
NETZSCH5 DSC heat-flow file parser for the dataset/dsc/PMDCo semantic schema.

Reads NETZSCH Proteus ASCII export files (FORMAT=NETZSCH5, MTYPE=DSC) and
returns a ParseResult compatible with the semantic_transformers Transformer API.

Supported export types
----------------------
DATA COMPARE  Tab-separated, POINT decimal. Two side-by-side DSC/(µV/mg) columns
              (sample + reference). Written when comparing two experiments in Proteus.
              Header values: #KEY:   TAB   VALUE1  TAB  VALUE2

ALL           Semicolon-separated, COMMA decimal. Single DSC/(mW/mg) column plus
              Time/min and Sensitivity columns. Written by "Export All" in Proteus.
              Header values: #KEY:   SPACE  ;VALUE

The export type is self-described in the header:
  #SEPARATOR: SEMICOLON  →  ALL export
  #SEPARATOR: TAB        →  DATA COMPARE export  (or header absent → default TAB)
  #DECIMAL:   COMMA      →  replace ',' with '.' when parsing numeric data

Non-standard character encoding
--------------------------------
  ° = \\x9b  (not the standard \\xb0)
  µ = \\x91  (not the standard \\xb5)
Files must be opened with encoding="latin-1".

Scalar results
--------------
Scalar results (onset/peak temperature, enthalpy, etc.) are NOT extracted
automatically — they come from NETZSCH Proteus analysis software.
Add them to simplified_json["results"] before calling Transformer.run():

    parser = NETZSCH5DSCDatasetParser()
    result = parser.parse(path)
    result.simplified_json["results"] = [
        {"name": "Onset Temperature", "value": 280.5, "unit": "Degree Celsius (°C)"},
        {"name": "Enthalpy of Transformation", "value": -45.3, "unit": "Joule per Gram (J/g)"},
    ]
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd

from semantic_transformers.parser import ParseResult


# ---------------------------------------------------------------------------
# DSC ontology and QUDT IRIs
# ---------------------------------------------------------------------------

_DSC_NS   = "https://w3id.org/dsms/DSCMeasurement/"
_QUDT_NS  = "http://qudt.org/vocab/unit/"

# NETZSCH5 uses non-standard Latin-1 byte sequences for special characters.
_TEMP_COL_RAW  = "Temp./\x9bC"        # ° = \x9b in NETZSCH5
_DSC_COL_RAW   = "DSC/(\x91V/mg)"     # µ = \x91 in NETZSCH5

# Canonical names — DATA COMPARE
_TEMP_COL       = "Temp./°C"
_DSC_SAMPLE_COL = "DSC/(µV/mg)_sample"
_DSC_REF_COL    = "DSC/(µV/mg)_reference"

# Raw column names — ALL export
_DSC_MW_COL_RAW    = "DSC/(mW/mg)"
_TIME_COL_RAW      = "Time/min"
_SENSIT_COL_RAW    = "Sensit./(uV/mW)"

# Human-readable names — DATA COMPARE (µV/mg, two columns)
_PRETTY_NAMES: dict[str, str] = {
    _TEMP_COL:        "Temperature (°C)",
    _DSC_SAMPLE_COL:  "DSC Heat Flow - Sample (µV/mg)",
    _DSC_REF_COL:     "DSC Heat Flow - Reference (µV/mg)",
}

# Human-readable names — ALL export (mW/mg, single column)
_PRETTY_NAMES_ALL: dict[str, str] = {
    _TEMP_COL:      "Temperature (°C)",
    _DSC_MW_COL_RAW: "DSC Heat Flow (mW/mg)",
    _TIME_COL_RAW:   "Time (min)",
}

# Semantic column mappings — DATA COMPARE
COLUMN_IRIS: dict[str, str] = {
    "Temperature (°C)":                  _DSC_NS + "Temperature",
    "DSC Heat Flow - Sample (µV/mg)":    _DSC_NS + "DSCHeatFlow",
    "DSC Heat Flow - Reference (µV/mg)": _DSC_NS + "DSCHeatFlow",
}
COLUMN_UNITS: dict[str, str] = {
    "Temperature (°C)":                  _QUDT_NS + "DEG_C",
    "DSC Heat Flow - Sample (µV/mg)":    _QUDT_NS + "MicroV-PER-MilliGM",
    "DSC Heat Flow - Reference (µV/mg)": _QUDT_NS + "MicroV-PER-MilliGM",
}

# Semantic column mappings — ALL export (mW/mg is calibrated heat flow, not raw µV)
COLUMN_IRIS_ALL: dict[str, str] = {
    "Temperature (°C)":      _DSC_NS + "Temperature",
    "DSC Heat Flow (mW/mg)": _DSC_NS + "DSCHeatFlow",
    "Time (min)":            _DSC_NS + "MeasurementTime",
}
COLUMN_UNITS_ALL: dict[str, str] = {
    "Temperature (°C)":      _QUDT_NS + "DEG_C",
    "DSC Heat Flow (mW/mg)": _QUDT_NS + "MilliW-PER-MilliGM",
    "Time (min)":            _QUDT_NS + "MIN",
}

_KNOWN_MANUFACTURERS = [
    "NETZSCH", "TA Instruments", "Mettler-Toledo", "Mettler",
    "Perkin-Elmer", "PerkinElmer", "Setaram", "Linseis", "Hitachi",
]


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _normalize_key(k: str) -> str:
    """Replace NETZSCH5 non-standard bytes with Unicode equivalents."""
    return k.replace("\x91", "µ").replace("\x9b", "°")


def _is_temp_col(name: str) -> bool:
    """Match the temperature column regardless of degree-sign encoding.

    NETZSCH Proteus writes the degree symbol differently depending on the
    export type and software version:
      DATA COMPARE TXT: 'Temp./\\x9bC'  (non-standard Latin-1 byte \\x9b)
      ALL CSV:          'Temp./ï¿½C'    (bytes \\xef\\xbf\\xbd decoded as Latin-1,
                                         i.e. UTF-8 replacement char U+FFFD)
    Both start with 'Temp./' — prefix match is robust across variants.
    """
    return name.startswith("Temp./")


def _first_token(raw_value: str, sep: str) -> str:
    """Return first non-empty stripped token after splitting raw_value by sep."""
    for part in raw_value.split(sep):
        stripped = part.strip()
        if stripped:
            return stripped
    return ""


def _detect_separator(path: Path) -> str:
    """
    Detect the header-value column separator from the #SEPARATOR field.

    NETZSCH5 header lines are self-describing:
      #SEPARATOR: ;SEMICOLON   →  ALL export, returns ';'
      #SEPARATOR: TAB          →  DATA COMPARE export, returns '\\t'
      (field absent)           →  DATA COMPARE export, returns '\\t'
    """
    try:
        with path.open(encoding="latin-1") as f:
            for line in f:
                if line.startswith("##"):
                    break
                if "#SEPARATOR" in line and "SEMICOLON" in line:
                    return ";"
    except OSError:
        pass
    return "\t"


def _parse_header(path: Path, sep: str = "\t") -> dict[str, str]:
    """
    Parse #KEY: VALUE metadata lines up to the ## data-section marker.

    Skips #* result-summary lines (present in ALL exports).
    Uses sep to split multi-value fields and extract the first non-empty token:
      - TAB: 'VALUE1\\tVALUE2' → 'VALUE1'  (DATA COMPARE, two files side-by-side)
      - ';':  ' ;VALUE'        → 'VALUE'   (ALL export, leading separator)
    """
    metadata: dict[str, str] = {}
    with path.open(encoding="latin-1") as f:
        for line in f:
            line = line.rstrip()
            if line.startswith("##"):
                break
            if line.startswith("#*"):
                continue
            if line.startswith("#"):
                m = re.match(r"^#+([^:]+):\s+(.+)$", line)
                if m:
                    key = _normalize_key(m.group(1).strip())
                    metadata[key] = _first_token(m.group(2), sep)
    return metadata


def _parse_timeseries(
    path: Path,
    sep: str = "\t",
    decimal: str = ".",
    export_type: str = "DATA COMPARE",
) -> tuple[list[str], pd.DataFrame]:
    """
    Parse the ## data section.

    sep:         column separator (';' for ALL, '\\t' for DATA COMPARE)
    decimal:     decimal character (',' for ALL, '.' for DATA COMPARE)
    export_type: controls column renaming and Sensitivity column removal

    DATA COMPARE: renames duplicate DSC/(µV/mg) columns to _sample / _reference.
    ALL:          drops the Sensitivity column; single DSC/(mW/mg) column kept.
    """
    column_names: list[str] = []
    data_lines:   list[str] = []
    in_data = False

    with path.open(encoding="latin-1") as f:
        for line in f:
            line = line.rstrip()
            if line.startswith("##"):
                header = line.lstrip("#").strip()
                column_names = [c.strip() for c in header.split(sep)]
                in_data = True
                continue
            if line.startswith("#"):
                continue
            if in_data and line.strip():
                if decimal == ",":
                    line = line.replace(",", ".")
                data_lines.append(line.strip())

    # Normalize temperature column name regardless of degree-sign encoding variant
    column_names = [_TEMP_COL if _is_temp_col(c) else c for c in column_names]

    if export_type == "DATA COMPARE":
        # Rename duplicate DSC columns: first = sample, second = reference
        if column_names.count(_DSC_COL_RAW) == 2:
            seen = False
            renamed: list[str] = []
            for c in column_names:
                if c == _DSC_COL_RAW and not seen:
                    renamed.append(_DSC_SAMPLE_COL)
                    seen = True
                elif c == _DSC_COL_RAW:
                    renamed.append(_DSC_REF_COL)
                else:
                    renamed.append(c)
            column_names = renamed

    if not data_lines or not column_names:
        return column_names, pd.DataFrame()

    raw = sep.join(column_names) + "\n" + "\n".join(data_lines)
    try:
        df = pd.read_csv(StringIO(raw), sep=sep, engine="python")
    except Exception:
        return column_names, pd.DataFrame()

    # ALL export: drop Sensitivity column (calibration factor, not a result)
    if export_type != "DATA COMPARE" and _SENSIT_COL_RAW in df.columns:
        df = df.drop(columns=[_SENSIT_COL_RAW])
        column_names = [c for c in column_names if c != _SENSIT_COL_RAW]

    return column_names, df


def _parse_range(range_str: str) -> dict[str, float | None]:
    """Parse '25,0°C/50,0(K/min)/550,0°C' → {start_temp, heating_rate, end_temp}.

    Handles three degree-sign variants produced by Proteus export types:
      '\\x9bC'   — DATA COMPARE TXT (non-standard Latin-1 byte)
      'ï¿½C'     — ALL CSV (UTF-8 replacement char \\xef\\xbf\\xbd decoded as Latin-1)
      '°C'       — standard Unicode degree sign (future-proof)
    """
    clean = (range_str
             .replace(",", ".")
             .replace("\x9bC", "")    # DATA COMPARE TXT
             .replace("ï¿½C", "")    # ALL CSV
             .replace("°C", "")       # standard Unicode
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
    """Parse NETZSCH5 datetime 'DD.MM.YYYY HH:MM:SS (UTC±N)' to ISO 8601."""
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
    try:
        return float(s.replace(",", "."))
    except (ValueError, AttributeError):
        return None


def _parse_int(s: str) -> int | None:
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
    sample   = meta.get("SAMPLE", "").strip()
    identity = meta.get("IDENTITY", "").strip()
    if sample and identity:
        return f"{sample} — {identity}"
    return sample or path.stem


def _parse_purge_mfc(value: str) -> tuple[str, float | None]:
    """
    Parse ALL-export PURGE 1 MFC value: 'AIR(80/20),250,3 ml/min' → ('AIR(80/20)', 3.0).

    Format: GasName,FlowSetting,ActualFlow ml/min
    Extracts gas name (first token) and actual flow rate (last 'N ml/min' token).
    """
    parts = value.split(",")
    gas = parts[0].strip() if parts else ""
    flow: float | None = None
    for part in reversed(parts[1:]):
        m = re.match(r"([\d.]+)\s+ml/min", part.strip())
        if m:
            flow = _parse_float(m.group(1))
            break
    return gas, flow


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class NETZSCH5DSCDatasetParser:
    """
    Parser for NETZSCH5 DSC heat-flow files (FORMAT=NETZSCH5, MTYPE=DSC).

    Handles both export formats produced by NETZSCH Proteus software:
      - DATA COMPARE (tab-separated, two DSC columns in µV/mg)
      - ALL export   (semicolon-separated, single DSC column in mW/mg)

    The format is auto-detected from the #SEPARATOR header field.
    """

    def can_parse(self, path: Path) -> bool:
        """Return True for NETZSCH5 DSC files (DATA COMPARE or ALL export)."""
        try:
            sep = _detect_separator(path)
            found_netzsch5 = False
            found_dsc = False
            with path.open(encoding="latin-1") as f:
                for line in f:
                    if line.startswith("##"):
                        break
                    if line.startswith("#*"):
                        continue
                    if "FORMAT" in line and "NETZSCH5" in line:
                        found_netzsch5 = True
                    if "MTYPE" in line:
                        m = re.match(r"^#+([^:]+):\s+(.+)$", line.rstrip())
                        if m:
                            mtype_val = _first_token(m.group(2), sep).upper()
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
        Parse a NETZSCH5 DSC file (DATA COMPARE or ALL export).

        Returns a ParseResult with simplified_json ready for Transformer.run()
        after adding results[] from the Proteus analysis report.
        """
        sep         = _detect_separator(path)
        decimal     = "," if sep == ";" else "."
        meta        = _parse_header(path, sep=sep)
        export_type = meta.get("EXPORTTYPE", "DATA COMPARE").upper()
        if export_type not in ("ALL", "DATA COMPARE"):
            export_type = "DATA COMPARE"

        column_names, df = _parse_timeseries(
            path, sep=sep, decimal=decimal, export_type=export_type
        )

        r = _parse_range(meta.get("RANGE", ""))
        manufacturer, model = _split_manufacturer(meta.get("INSTRUMENT", ""))

        simplified: dict = {
            "dataset_name": _dataset_name_from_meta(meta, path),
            "format": "NETZSCH DSC CSV" if sep == ";" else "NETZSCH DSC TXT",
        }

        # ── Provenance ────────────────────────────────────────────────────────
        dt_raw = meta.get("DATE/TIME", "")
        dt_iso = _parse_datetime(dt_raw)
        simplified["measurement_datetime"] = dt_iso if dt_iso else (dt_raw or None)

        for sj_key, meta_key in [
            ("operator",   "OPERATOR"),
            ("laboratory", "LABORATORY"),
            ("project_id", "PROJECT"),
            ("source_file", "FILE"),
            ("identity",   "IDENTITY"),
        ]:
            if meta.get(meta_key):
                simplified[sj_key] = meta[meta_key]

        remark = meta.get("REMARK", "").strip()
        if remark:
            simplified["remark"] = remark

        # ── Sample / specimen ─────────────────────────────────────────────────
        if meta.get("SAMPLE"):
            simplified["sample_name"] = meta["SAMPLE"]
        mass = _parse_float(meta.get("SAMPLE MASS /mg", ""))
        if mass is not None:
            simplified["sample_mass_mg"] = mass
        if meta.get("MATERIAL"):
            simplified["material"] = meta["MATERIAL"]
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
            simplified["crucible_type"] = meta["TYPE OF CRUCIBLE"]

        # Purge gas: DATA COMPARE has dedicated fields; ALL combines them in PURGE 1 MFC
        if export_type == "DATA COMPARE":
            if meta.get("PURGE GAS 1"):
                simplified["purge_gas"] = meta["PURGE GAS 1"]
            flow = _parse_float(meta.get("FLOW RATE 1 /(ml/min)", ""))
            if flow is not None:
                simplified["purge_gas_flow_rate_ml_min"] = flow
        else:
            mfc_val = meta.get("PURGE 1 MFC", "")
            if mfc_val:
                gas, flow = _parse_purge_mfc(mfc_val)
                if gas:
                    simplified["purge_gas"] = gas
                if flow is not None:
                    simplified["purge_gas_flow_rate_ml_min"] = flow

        if r.get("heating_rate") is not None:
            simplified["heating_rate_k_min"] = r["heating_rate"]
        if r.get("start_temp") is not None:
            simplified["temperature_start_degC"] = r["start_temp"]
        if r.get("end_temp") is not None:
            simplified["temperature_end_degC"] = r["end_temp"]
        if meta.get("SEGMENT"):
            simplified["segment"] = meta["SEGMENT"]

        # M.RANGE key uses µV in TXT, may appear as °V in some CSV encodings
        mrange_raw = meta.get("M.RANGE /µV", "") or meta.get("M.RANGE /°V", "")
        mrange = _parse_float(mrange_raw)
        if mrange is not None:
            simplified["measurement_range_uV"] = mrange

        exo = _parse_int(meta.get("EXO", ""))
        if exo is not None:
            simplified["exo_sign"] = exo

        # ── Instrument & calibration ──────────────────────────────────────────
        if manufacturer:
            simplified["manufacturer"] = manufacturer
        if model:
            simplified["instrument_model"] = model
        if meta.get("CORR. FILE"):
            simplified["baseline_correction_file"] = meta["CORR. FILE"]
        if meta.get("TEMPCAL"):
            simplified["temperature_calibration_file"] = meta["TEMPCAL"]
        if meta.get("SENSITIVITY"):
            simplified["sensitivity_calibration_file"] = meta["SENSITIVITY"]
        if meta.get("CORR. CODE"):
            simplified["correction_code"] = meta["CORR. CODE"]
        tau = meta.get("TAU-R", "").strip()
        if tau and tau != "---":
            simplified["tau_r"] = tau

        # ── Timeseries and column mappings ────────────────────────────────────
        if export_type == "DATA COMPARE":
            pretty_names = _PRETTY_NAMES
            col_iris     = COLUMN_IRIS
            col_units    = COLUMN_UNITS
        else:
            pretty_names = _PRETTY_NAMES_ALL
            col_iris     = COLUMN_IRIS_ALL
            col_units    = COLUMN_UNITS_ALL

        ts = df.rename(columns=pretty_names) if not df.empty else None

        return ParseResult(
            simplified_json=simplified,
            timeseries=ts,
            column_iris=col_iris,
            column_units=col_units,
        )
