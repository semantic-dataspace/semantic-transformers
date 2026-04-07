"""
Extractor protocol and result type.

An Extractor reads a machine file (any format, any internal structure) and
returns an ExtractionResult with two outputs:

  simplified_json   — a plain dict matching the target schema's example.input.json
                      format, ready to be fed into the JSONata transform.

  timeseries        — a pandas DataFrame of the raw measurement columns, or None
                      if the file contains no time-series data.

  column_iris       — maps each DataFrame column name to an ontology class IRI.
                      Only the descriptor goes into the knowledge graph; the
                      numeric values stay in the DataFrame.

  column_units      — maps each DataFrame column name to a QUDT unit IRI.

Extractors are schema- and machine-specific: one extractor per (machine model,
schema) combination.  They live in the schema-store repository alongside the
schema they serve, not in this library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd


@dataclass
class ExtractionResult:
    """Normalised output produced by any Extractor."""

    # Flat dict matching the target schema's example.input.json.
    # Feed this directly into the JSONata transform.
    simplified_json: dict

    # Raw time-series data.  None when the file has no tabular measurements.
    timeseries: pd.DataFrame | None = None

    # Column name → ontology class IRI (e.g. "https://w3id.org/pmd/tto/TestTime")
    column_iris: dict[str, str] = field(default_factory=dict)

    # Column name → QUDT unit IRI (e.g. "http://qudt.org/vocab/unit/SEC")
    column_units: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class Extractor(Protocol):
    """Any callable object that reads a file and returns an ExtractionResult."""

    def extract(self, path: Path) -> ExtractionResult: ...
