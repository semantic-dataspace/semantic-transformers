"""
Anton Paar NHT nanoindentation file parser + semantic_transformers Parser implementations.

File format
-----------
Anton Paar NHT3 exports TXT files with European decimal notation (comma = decimal).
Two formats are supported:

  Individual indent file: one indentation per file (from the 'individual files/' folder)
  Combined session file:  N indentation blocks concatenated (instrument session export)

Both formats use UTF-8 with Latin-1 fallback.

Segment IDs in the time-series:
  0 = pre-contact (approach)
  1 = initiation
  2 = loading
  3 = hold / dwell at peak
  4 = unloading
  5 = retraction

Parser classes
--------------
AntonPaarNHTIndentParser
    Implements the semantic_transformers Parser protocol for ONE individual TXT file.
    Produces a ParseResult matching the dataset/nanoindentation/PMDCo simplified schema.
    Supply parent_experiment_iri and specimen_iri as **overrides to Transformer.run().

AntonPaarNHTSessionParser
    Implements the semantic_transformers Parser protocol for a session directory (or
    combined TXT file).
    Produces a ParseResult matching the characterization/nanoindentation/PMDCo
    simplified schema (experiment-level summary; timeseries is None).
    Supply specimen_iri and experiment_name as **overrides to Transformer.run().
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from pathlib import Path

import pandas as pd

from semantic_transformers.parser import ParseResult


# ---------------------------------------------------------------------------
# EMMO / QUDT IRIs for load-displacement time-series columns
# ---------------------------------------------------------------------------

_NANOIND_NS = "https://w3id.org/emmo/domain/nanoindentation#"
_QUDT_UNIT  = "http://qudt.org/vocab/unit/"

COLUMN_IRIS: dict[str, str] = {
    "Time (s)":  _NANOIND_NS + "EMMO_376c214e-95d7-58c9-b25a-9e95d89e85ea",
    "Pd (um)":   _NANOIND_NS + "EMMO_ef4c77a5-c561-5edc-8bac-456c20e02ed1",
    "Fn (mN)":   _NANOIND_NS + "EMMO_9a42c0cf-f0b5-52cd-b56d-5aa83d63688b",
    "SegmentID": _NANOIND_NS + "EMMO_a45dfdb9-7459-4bb9-a712-8a24b85afd1d",
}

COLUMN_UNITS: dict[str, str] = {
    "Time (s)":  _QUDT_UNIT + "SEC",
    "Pd (um)":   _QUDT_UNIT + "MicroM",
    "Fn (mN)":   _QUDT_UNIT + "MilliN",
    "SegmentID": _QUDT_UNIT + "UNITLESS",
}

_CONTROL_MODE_MAP = {
    "constant strain rate loading": "Constant Strain Rate",
    "depth-controlled":             "Depth-controlled",
    "load-controlled":              "Load-controlled",
    "oscillating":                  "Oscillating (CSM)",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class IndentationResult:
    index: int
    x_pos: float | None = None
    y_pos: float | None = None
    z_pos: float | None = None
    timestamp: datetime | None = None

    hit: float | None = None       # indentation hardness [GPa]
    eit: float | None = None       # indentation elastic modulus [GPa]
    e_star: float | None = None    # reduced modulus (plane-strain) [GPa]
    er: float | None = None        # Oliver-Pharr Er [GPa]
    hvit: float | None = None      # Vickers equivalent [HV]

    fmax: float | None = None      # max force [mN]
    hmax: float | None = None      # max depth [µm]
    stiffness: float | None = None # contact stiffness S [mN/µm]
    hc: float | None = None        # contact depth [µm]
    hr: float | None = None        # residual depth [µm]
    hp: float | None = None        # plastic depth [µm]
    ap: float | None = None        # projected contact area [µm²]

    welast: float | None = None    # elastic work [µJ]
    wplast: float | None = None    # plastic work [µJ]
    wtotal: float | None = None    # total work [µJ]
    nit: float | None = None       # elastic work ratio [%]
    rit: float | None = None       # reverse indentation ratio [%]

    m: float | None = None         # loading exponent
    epsilon: float | None = None   # geometry factor
    r2: float | None = None        # unloading fit R²

    warnings: list[str] = field(default_factory=list)
    timeseries: pd.DataFrame = field(default_factory=pd.DataFrame)

    def is_valid(self) -> bool:
        """Return False if all scalar results are zero (failed indent)."""
        scalars = [self.hit, self.eit, self.hmax, self.fmax]
        return any(v is not None and v != 0.0 for v in scalars)


@dataclass
class NanoindentationSession:
    instrument_sn: str | None = None
    control_mode: str | None = None
    max_depth: float | None = None
    min_depth: float | None = None
    loading_rate: float | None = None
    hold_time: float | None = None
    acquisition_rate: float | None = None
    analysis_method: str | None = None
    unload_fit_range: str | None = None
    poisson_ratio: float | None = None
    date: str | None = None
    indentations: list[IndentationResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Low-level parsing helpers
# ---------------------------------------------------------------------------

def _parse_eu_float(s: str) -> float | None:
    s = s.strip().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


_RE_KV = re.compile(
    r"^\s*"
    r"(?P<key>[A-Za-z][A-Za-z0-9_*/ ()²µ%]+?)\s*=\s*"
    r"(?P<value>-?[\d,\.]+)"
    r"(?:\s+(?P<unit>[^\n]+))?$"
)
_RE_XYZ      = re.compile(r"^\s*([XYZ]) position:\s*([\d,\.]+)\s*mm", re.IGNORECASE | re.MULTILINE)
_RE_DATETIME = re.compile(r"Date:\s*(\d{2}\.\d{2}\.\d{4})\s*\nTime:\s*(\d{2}:\d{2}:\d{2})")
_RE_SERIAL   = re.compile(r"NHT\s+S/N:\s*(\d+)")
_RE_ACQ_RATE = re.compile(r"Acquisition rate\s*:\s*([\d,\.]+)\s*\[Hz\]")
_RE_CONTROL  = re.compile(r"(Constant strain rate loading|Depth-controlled|Load-controlled|Oscillating)", re.IGNORECASE)
_RE_MAX_DEPTH    = re.compile(r"Max depth\s*:\s*([\d,\.]+)\s*\S*m", re.IGNORECASE)
_RE_MIN_DEPTH    = re.compile(r"Min depth\s*:\s*([\d,\.]+)\s*\S*m", re.IGNORECASE)
_RE_LOADING_RATE = re.compile(r"loading rate/load\s*:\s*([\d,\.]+)\s*1/s", re.IGNORECASE)
_RE_PAUSE    = re.compile(r"Pause\s*:\s*([\d,\.]+)\s*s")
_RE_METHOD   = re.compile(r"Method\s*:\s*(.+)")
_RE_UNLOAD_FIT = re.compile(r"Unload Fit\s*\[([^\]]+)\]")
_RE_WARNING  = re.compile(r"^\s*(hc out of|warning|caution|calibrat)", re.IGNORECASE)
_RE_POISSON  = re.compile(r"Poisson'?s ratio.*?=\s*([\d,\.]+)")

_KEY_MAP = {
    "HIT": "hit", "EIT": "eit", "E*": "e_star", "Er": "er", "HVIT": "hvit",
    "Fmax": "fmax", "hmax": "hmax", "S": "stiffness", "hc": "hc", "hr": "hr",
    "hp": "hp", "Ap": "ap", "Welast": "welast", "Wplast": "wplast",
    "Wtotal": "wtotal", "nIT": "nit", "m": "m", "Epsilon": "epsilon", "R2": "r2",
}


def _parse_individual_block(text: str, index: int) -> IndentationResult:
    result = IndentationResult(index=index)
    warnings: list[str] = []

    for m in _RE_XYZ.finditer(text):
        axis, val = m.group(1).upper(), _parse_eu_float(m.group(2))
        if axis == "X":   result.x_pos = val
        elif axis == "Y": result.y_pos = val
        elif axis == "Z": result.z_pos = val

    m = _RE_DATETIME.search(text)
    if m:
        try:
            result.timestamp = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d.%m.%Y %H:%M:%S")
        except ValueError:
            pass

    m = _RE_SERIAL.search(text)
    if m:
        setattr(result, "_instrument_sn", m.group(1).strip())  # keep as string

    for pattern, attr in [
        (_RE_MAX_DEPTH,    "_max_depth"),
        (_RE_MIN_DEPTH,    "_min_depth"),
        (_RE_LOADING_RATE, "_loading_rate"),
        (_RE_PAUSE,        "_hold_time"),
    ]:
        m = pattern.search(text)
        if m:
            setattr(result, attr, _parse_eu_float(m.group(1)))

    m = _RE_ACQ_RATE.search(text)
    if m: setattr(result, "_acquisition_rate", _parse_eu_float(m.group(1)))

    m = _RE_CONTROL.search(text)
    if m: setattr(result, "_control_mode", m.group(1))

    m = _RE_METHOD.search(text)
    if m: setattr(result, "_analysis_method", m.group(1).strip())

    m = _RE_UNLOAD_FIT.search(text)
    if m: setattr(result, "_unload_fit_range", m.group(1).strip())

    m = _RE_POISSON.search(text)
    if m: setattr(result, "poisson_ratio", _parse_eu_float(m.group(1)))

    for line in text.splitlines():
        m = _RE_KV.match(line)
        if m:
            key = m.group("key").strip()
            if key in _KEY_MAP:
                setattr(result, _KEY_MAP[key], _parse_eu_float(m.group("value")))

        rit_m = re.search(r"RIT\s+[\d,\.]+/[\d,\.]+/[\d,\.]+\s*=\s*(-?[\d,\.]+)", line)
        if rit_m:
            result.rit = _parse_eu_float(rit_m.group(1))

        if _RE_WARNING.match(line) and (w := line.strip()):
            warnings.append(w)

    result.warnings = warnings

    ts_start = text.find("Measured values\n")
    if ts_start != -1:
        result.timeseries = _parse_timeseries(text[ts_start + len("Measured values\n"):])

    return result


def _parse_timeseries(text: str) -> pd.DataFrame:
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("Time"):
            header_idx = i
            break
    if header_idx is None:
        return pd.DataFrame()

    data_lines = []
    for line in lines[header_idx + 2:]:
        line = line.strip()
        if not line:
            continue
        if line.startswith("Indentation") or line.startswith("+") or line.startswith("Date:"):
            break
        data_lines.append(line.replace(",", "."))

    if not data_lines:
        return pd.DataFrame()

    raw = "\t".join(["Time (s)", "Pd (um)", "Fn (mN)", "FnRef (mN)", "SegmentID"]) + "\n"
    raw += "\n".join(data_lines)
    try:
        return pd.read_csv(StringIO(raw), sep="\t")
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Public parsing functions
# ---------------------------------------------------------------------------

def parse_individual_file(path: Path) -> tuple[NanoindentationSession, IndentationResult]:
    text = _read_text(path)
    m = re.search(r"#\s*(\d+)\.TXT$", path.name, re.IGNORECASE)
    index = int(m.group(1)) if m else 1
    result = _parse_individual_block(text, index)
    session = NanoindentationSession(
        instrument_sn=getattr(result, "_instrument_sn", None),
        control_mode=getattr(result, "_control_mode", None),
        max_depth=getattr(result, "_max_depth", None),
        min_depth=getattr(result, "_min_depth", None),
        loading_rate=getattr(result, "_loading_rate", None),
        hold_time=getattr(result, "_hold_time", None),
        acquisition_rate=getattr(result, "_acquisition_rate", None),
        analysis_method=getattr(result, "_analysis_method", None),
        unload_fit_range=getattr(result, "_unload_fit_range", None),
        poisson_ratio=getattr(result, "poisson_ratio", None),
        date=result.timestamp.date().isoformat() if result.timestamp else None,
        indentations=[result],
    )
    return session, result


def parse_individual_directory(directory: Path) -> NanoindentationSession:
    files = sorted(directory.glob("*.TXT"), key=lambda p: (
        int(m.group(1)) if (m := re.search(r"#\s*(\d+)\.TXT$", p.name, re.IGNORECASE)) else 0
    ))
    if not files:
        raise FileNotFoundError(f"No TXT files found in {directory}")
    session, _ = parse_individual_file(files[0])
    session.indentations = []
    for f in files:
        _, result = parse_individual_file(f)
        session.indentations.append(result)
    for r in session.indentations:
        if r.timestamp:
            session.date = r.timestamp.date().isoformat()
            break
    return session


def parse_combined_file(path: Path) -> NanoindentationSession:
    text = _read_text(path)
    raw_blocks = re.split(r"(?=^Indentation\s*$)", text, flags=re.MULTILINE)
    raw_blocks = [b for b in raw_blocks if b.strip().startswith("Indentation")]
    if not raw_blocks:
        raw_blocks = [text]
    session = NanoindentationSession()
    results: list[IndentationResult] = []
    for i, block in enumerate(raw_blocks, start=1):
        results.append(_parse_individual_block(block, index=i))
    if results:
        r0 = results[0]
        session.instrument_sn    = getattr(r0, "_instrument_sn", None)
        session.control_mode     = getattr(r0, "_control_mode", None)
        session.max_depth        = getattr(r0, "_max_depth", None)
        session.min_depth        = getattr(r0, "_min_depth", None)
        session.loading_rate     = getattr(r0, "_loading_rate", None)
        session.hold_time        = getattr(r0, "_hold_time", None)
        session.acquisition_rate = getattr(r0, "_acquisition_rate", None)
        session.analysis_method  = getattr(r0, "_analysis_method", None)
        session.unload_fit_range = getattr(r0, "_unload_fit_range", None)
        session.poisson_ratio    = getattr(r0, "poisson_ratio", None)
        if r0.timestamp:
            session.date = r0.timestamp.date().isoformat()
    session.indentations = results
    return session


# ---------------------------------------------------------------------------
# Parser protocol implementations
# ---------------------------------------------------------------------------

def _result_entries(result: IndentationResult) -> list[dict]:
    pairs: list[tuple[str, float | None, str]] = [
        ("Hardness HIT",                  result.hit,       "GPa"),
        ("Elastic Modulus EIT",           result.eit,       "GPa"),
        ("Reduced Modulus E*",            result.e_star,    "GPa"),
        ("Indentation Modulus Er",        result.er,        "GPa"),
        ("Vickers Equivalent HVIT",       result.hvit,      "-"),
        ("Maximum Force Fmax",            result.fmax,      "mN"),
        ("Maximum Depth hmax",            result.hmax,      "um"),
        ("Contact Depth hc",              result.hc,        "um"),
        ("Residual Depth hr",             result.hr,        "um"),
        ("Plastic Depth hp",              result.hp,        "um"),
        ("Contact Stiffness S",           result.stiffness, "-"),
        ("Projected Contact Area Ap",     result.ap,        "-"),
        ("Elastic Work Welast",           result.welast,    "uJ"),
        ("Plastic Work Wplast",           result.wplast,    "uJ"),
        ("Total Work Wtotal",             result.wtotal,    "uJ"),
        ("Elastic Work Ratio nIT",        result.nit,       "%"),
        ("Reverse Indentation Ratio RIT", result.rit,       "%"),
        ("Loading Exponent m",            result.m,         "-"),
        ("Geometry Factor Epsilon",       result.epsilon,   "-"),
        ("Fit Quality R2",                result.r2,        "-"),
    ]
    return [{"name": n, "value": v, "unit": u} for n, v, u in pairs if v is not None]


def _safe_mean(vals: list[float]) -> float | None:
    return statistics.mean(vals) if vals else None


def _safe_stdev(vals: list[float]) -> float | None:
    return statistics.stdev(vals) if len(vals) >= 2 else None


class AntonPaarNHTIndentParser:
    """
    Parser for a single Anton Paar NHT TXT file (one indentation).
    Produces ParseResult matching dataset/nanoindentation/PMDCo simplified schema.

    Supply parent_experiment_iri and specimen_iri as **overrides to Transformer.run().
    dataset_name defaults to "Indent #NNN" and can also be overridden.
    """

    def parse(self, path: Path) -> ParseResult:
        _session, result = parse_individual_file(path)

        simplified: dict = {
            "dataset_name": f"Indent #{result.index:03d}",
            "indent_index": result.index,
            "format":       "Anton Paar NHT TXT",
        }
        if result.timestamp:
            simplified["timestamp"] = result.timestamp.isoformat()
        if result.x_pos is not None:
            simplified["x_position"] = result.x_pos
        if result.y_pos is not None:
            simplified["y_position"] = result.y_pos
        if result.warnings:
            simplified["analysis_warnings"] = "\n".join(result.warnings)
        entries = _result_entries(result)
        if entries:
            simplified["results"] = entries

        ts = None
        if not result.timeseries.empty:
            ts = result.timeseries.drop(columns=["FnRef (mN)"], errors="ignore")

        return ParseResult(
            simplified_json=simplified,
            timeseries=ts,
            column_iris=COLUMN_IRIS,
            column_units=COLUMN_UNITS,
        )


class AntonPaarNHTSessionParser:
    """
    Parser for an Anton Paar NHT session: a directory of individual TXT files
    OR a combined session TXT file.
    Produces ParseResult matching characterization/nanoindentation/PMDCo simplified
    schema (experiment-level summary; timeseries is None).

    Supply specimen_iri and experiment_name as **overrides to Transformer.run().
    """

    def parse(self, path: Path) -> ParseResult:
        if path.is_dir():
            session = parse_individual_directory(path)
            name_default = path.name
        else:
            session = parse_combined_file(path)
            name_default = path.stem

        valid = [r for r in session.indentations if r.is_valid()]
        hits  = [r.hit  for r in valid if r.hit  is not None]
        eits  = [r.eit  for r in valid if r.eit  is not None]
        hmaxs = [r.hmax for r in valid if r.hmax is not None]

        summary: list[dict] = []
        for name, value, unit in [
            ("Mean Hardness HIT",            _safe_mean(hits),        "GPa"),
            ("Std Dev Hardness HIT",         _safe_stdev(hits),       "GPa"),
            ("Mean Elastic Modulus EIT",     _safe_mean(eits),        "GPa"),
            ("Std Dev Elastic Modulus EIT",  _safe_stdev(eits),       "GPa"),
            ("Mean Maximum Depth hmax",      _safe_mean(hmaxs),       "um"),
            ("Number of Valid Indentations", float(len(valid)),        "-"),
        ]:
            if value is not None:
                summary.append({"name": name, "value": value, "unit": unit})

        control_mode: str | None = None
        if session.control_mode:
            key = session.control_mode.lower().strip()
            control_mode = _CONTROL_MODE_MAP.get(key, session.control_mode)

        simplified: dict = {"experiment_name": name_default}
        if session.date:
            simplified["test_date"] = session.date + "T00:00:00"
        if session.instrument_sn:
            simplified["instrument_serial"] = session.instrument_sn
        if control_mode:
            simplified["control_mode"] = control_mode
        if session.analysis_method:
            simplified["analysis_method"] = session.analysis_method
        if session.max_depth is not None:
            simplified["max_depth"] = session.max_depth
        if session.loading_rate is not None:
            simplified["loading_rate"] = session.loading_rate
        if session.hold_time is not None:
            simplified["hold_time"] = session.hold_time
        if session.acquisition_rate is not None:
            simplified["acquisition_rate"] = session.acquisition_rate
        if session.poisson_ratio is not None:
            simplified["poisson_ratio"] = session.poisson_ratio
        if summary:
            simplified["summary_results"] = summary

        return ParseResult(
            simplified_json=simplified,
            timeseries=None,
            column_iris={},
            column_units={},
        )
