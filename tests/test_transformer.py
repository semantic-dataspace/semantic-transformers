"""
Unit tests for the Transformer class.

Uses a minimal inline schema (transform + context written to a tmp directory)
and a hand-rolled mock extractor so these tests have no dependency on
semantic-schemas or any real instrument file.
"""
import json
from pathlib import Path

import pandas as pd
import pytest
import rdflib

from semantic_transformers import Transformer, TransformResult, ExtractionResult


# ---------------------------------------------------------------------------
# Minimal schema fixtures
# ---------------------------------------------------------------------------

# A JSONata transform that maps two simplified fields to an OO-LD document.
_TRANSFORM = """{
    "id": "test-run-1",
    "type": "TestProcess",
    "label": test_name,
    "temperature": temperature
}"""

# A minimal OO-LD context that makes the document above parseable as JSON-LD.
_CONTEXT_YAML = """\
'@context':
  '@base': 'https://example.org/'
  id: '@id'
  type: '@type'
  TestProcess: 'https://example.org/vocab/TestProcess'
  label: 'http://www.w3.org/2000/01/rdf-schema#label'
  temperature: 'https://example.org/vocab/temperature'
"""


@pytest.fixture()
def schema_dir(tmp_path):
    """Write minimal transform and context files and return the schema root."""
    simplified = tmp_path / "simplified"
    simplified.mkdir()
    (simplified / "transform.jsonata").write_text(_TRANSFORM, encoding="utf-8")

    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "schema.oold.yaml").write_text(_CONTEXT_YAML, encoding="utf-8")

    return tmp_path


@pytest.fixture()
def mock_extraction():
    """A hand-rolled ExtractionResult with known values."""
    return ExtractionResult(
        simplified_json={"test_name": "mock-run", "temperature": 23.0},
        timeseries=pd.DataFrame({"force": [0.0, 100.0, 200.0], "extension": [0.0, 1.0, 2.0]}),
        column_iris={"force": "https://example.org/vocab/Force"},
        column_units={"force": "http://qudt.org/vocab/unit/N"},
    )


class MockExtractor:
    def __init__(self, result: ExtractionResult):
        self._result = result

    def extract(self, path: Path) -> ExtractionResult:
        return self._result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_transformer_returns_transform_result(schema_dir, mock_extraction):
    transformer = Transformer(
        extractor=MockExtractor(mock_extraction),
        transform=schema_dir / "simplified" / "transform.jsonata",
        context=schema_dir / "specs" / "schema.oold.yaml",
    )
    result = transformer.run("ignored.csv")

    assert isinstance(result, TransformResult)


def test_transformer_oold_doc_contains_transform_output(schema_dir, mock_extraction):
    transformer = Transformer(
        extractor=MockExtractor(mock_extraction),
        transform=schema_dir / "simplified" / "transform.jsonata",
        context=schema_dir / "specs" / "schema.oold.yaml",
    )
    result = transformer.run("ignored.csv")

    assert result.oold_doc["label"] == "mock-run"
    assert result.oold_doc["temperature"] == 23.0


def test_transformer_override_wins_over_extractor(schema_dir, mock_extraction):
    transformer = Transformer(
        extractor=MockExtractor(mock_extraction),
        transform=schema_dir / "simplified" / "transform.jsonata",
        context=schema_dir / "specs" / "schema.oold.yaml",
    )
    result = transformer.run("ignored.csv", test_name="overridden")

    assert result.oold_doc["label"] == "overridden"


def test_transformer_graph_is_non_empty(schema_dir, mock_extraction):
    transformer = Transformer(
        extractor=MockExtractor(mock_extraction),
        transform=schema_dir / "simplified" / "transform.jsonata",
        context=schema_dir / "specs" / "schema.oold.yaml",
    )
    result = transformer.run("ignored.csv")

    flat = rdflib.Graph()
    for s, p, o, _ in result.graph.quads():
        flat.add((s, p, o))

    assert len(flat) > 0


def test_transformer_graph_contains_expected_type(schema_dir, mock_extraction):
    transformer = Transformer(
        extractor=MockExtractor(mock_extraction),
        transform=schema_dir / "simplified" / "transform.jsonata",
        context=schema_dir / "specs" / "schema.oold.yaml",
    )
    result = transformer.run("ignored.csv")

    flat = rdflib.Graph()
    for s, p, o, _ in result.graph.quads():
        flat.add((s, p, o))

    VOCAB = rdflib.Namespace("https://example.org/vocab/")
    typed_subjects = list(flat.subjects(rdflib.RDF.type, VOCAB.TestProcess))
    assert len(typed_subjects) == 1


def test_transformer_dataframe_is_passed_through(schema_dir, mock_extraction):
    transformer = Transformer(
        extractor=MockExtractor(mock_extraction),
        transform=schema_dir / "simplified" / "transform.jsonata",
        context=schema_dir / "specs" / "schema.oold.yaml",
    )
    result = transformer.run("ignored.csv")

    assert result.dataframe is not None
    assert list(result.dataframe.columns) == ["force", "extension"]
    assert len(result.dataframe) == 3


def test_transformer_column_metadata_passed_through(schema_dir, mock_extraction):
    transformer = Transformer(
        extractor=MockExtractor(mock_extraction),
        transform=schema_dir / "simplified" / "transform.jsonata",
        context=schema_dir / "specs" / "schema.oold.yaml",
    )
    result = transformer.run("ignored.csv")

    assert result.column_iris == {"force": "https://example.org/vocab/Force"}
    assert result.column_units == {"force": "http://qudt.org/vocab/unit/N"}


def test_transformer_none_timeseries_is_allowed(schema_dir):
    no_ts = ExtractionResult(
        simplified_json={"test_name": "no-ts", "temperature": 20.0},
        timeseries=None,
    )
    transformer = Transformer(
        extractor=MockExtractor(no_ts),
        transform=schema_dir / "simplified" / "transform.jsonata",
        context=schema_dir / "specs" / "schema.oold.yaml",
    )
    result = transformer.run("ignored.csv")

    assert result.dataframe is None
