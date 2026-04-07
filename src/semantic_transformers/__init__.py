"""
semantic-transformers
=====================
Converters and machine-file parsers for semantic schema pipelines.

Public API
----------
    ExtractionResult   — normalised extractor output (simplified_json + DataFrame)
    Extractor          — protocol that all extractors must satisfy
    Transformer        — runs extraction → JSONata transform → RDF
    TransformResult    — everything produced by Transformer.run() or QuickMapper.run()
    QuickMapper        — turns any tabular file into RDF with a simple YAML mapping
"""

from .extractor import Extractor, ExtractionResult
from .transformer import Transformer, TransformResult
from .quick_mapper import QuickMapper

__all__ = [
    "Extractor", "ExtractionResult",
    "Transformer", "TransformResult",
    "QuickMapper",
]
