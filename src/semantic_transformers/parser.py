"""
Parser protocol and result type.

A Parser reads a machine file (any format, any internal structure) and
returns a ParseResult with two outputs:

  simplified_json:  a plain dict matching the target schema's example.input.json
                    format, ready to be fed into the JSONata transform.

  timeseries:       a pandas DataFrame of the raw measurement columns, or None
                    if the file contains no time-series data.

  column_iris:      maps each DataFrame column name to an ontology class IRI.
                    Only the descriptor goes into the knowledge graph; the
                    numeric values stay in the DataFrame.

  column_units:     maps each DataFrame column name to a QUDT unit IRI.

Parsers are schema- and machine-specific: one parser per (machine model,
schema) combination.  They live in the parsers/ directory alongside the
schema they serve, not in this library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd


@dataclass
class ParseResult:
    """Normalised output produced by any Parser."""

    # Flat dict matching the target schema's example.input.json.
    # Feed this directly into the JSONata transform.
    simplified_json: dict

    # Raw time-series data.  None when the file has no tabular measurements.
    timeseries: pd.DataFrame | None = None

    # Column name → ontology class IRI, or None when no class applies.
    # (e.g. "https://w3id.org/pmd/tto/TTO_0000005" for extension columns)
    column_iris: dict[str, str | None] = field(default_factory=dict)

    # Column name → QUDT unit IRI (e.g. "http://qudt.org/vocab/unit/SEC")
    column_units: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class Parser(Protocol):
    """Any callable object that reads a file and returns a ParseResult."""

    def parse(self, path: Path) -> ParseResult: ...


class SchemaAwareParser:
    """
    Optional mixin for parsers that can use the input schema for type coercion.

    Implement this alongside the Parser protocol when your parser needs to cast
    field values to the types declared in ``schema.simplified.json``.
    ``Transformer`` calls ``configure(schema)`` automatically after construction
    whenever an ``input_schema`` is available, so the user never needs to pass
    the schema path to the parser directly.

    Example
    -------
    class MyParser(SchemaAwareParser):
        def configure(self, schema: dict) -> None:
            self._field_types = {
                name: prop.get("type", "string")
                for name, prop in schema.get("properties", {}).items()
            }

        def parse(self, path: Path) -> ParseResult:
            ...
    """

    def configure(self, schema: dict) -> None:
        """Receive the loaded input schema dict from Transformer."""
        ...
