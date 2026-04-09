"""
semantic-transformers
=====================
Converters and machine-file parsers for semantic schema pipelines.

Public API
----------
    ParseResult:     normalised parser output (simplified_json + DataFrame)
    Parser:          protocol that all parsers must satisfy
    Transformer:     runs parsing → JSONata transform → RDF
    TransformResult: everything produced by Transformer.run()
    QuickMapper:     turns any tabular file into RDF with a simple YAML mapping
"""

from .parser import Parser, ParseResult
from .transformer import Transformer, TransformResult
from .quick_mapper import QuickMapper

__all__ = [
    "Parser", "ParseResult",
    "Transformer", "TransformResult",
    "QuickMapper",
]
