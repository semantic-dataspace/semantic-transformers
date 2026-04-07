"""
Tests for QuickMapper.

Uses only in-memory DataFrames and tmp_path fixtures — no external files
and no dependency on semantic-schemas.
"""
import importlib.util
import json
from pathlib import Path

_has_openpyxl = importlib.util.find_spec("openpyxl") is not None

import pandas as pd
import pytest
import rdflib

from semantic_transformers import QuickMapper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_DF = pd.DataFrame({
    "Force":       [0.0, 100.0, 200.0],
    "Extension":   [0.0, 1.0,   2.0],
    "Temperature": [22.0, 22.5, 23.0],
})

_MINIMAL_MAPPING = {
    "label": "test-run",
    "columns": {
        "Force": {
            "iri":  "https://w3id.org/pmd/tto/StandardForce",
            "unit": "http://qudt.org/vocab/unit/N",
        },
        "Extension": {
            "iri": "https://w3id.org/pmd/tto/Extension",
        },
    },
}


def _write_csv(tmp_path, df, name="data.csv", **kwargs) -> Path:
    p = tmp_path / name
    df.to_csv(p, index=False, **kwargs)
    return p


def _write_tsv(tmp_path, df, name="data.tsv") -> Path:
    p = tmp_path / name
    df.to_csv(p, index=False, sep="\t")
    return p


def _write_excel(tmp_path, df, name="data.xlsx") -> Path:
    p = tmp_path / name
    df.to_excel(p, index=False)
    return p


def _write_txt_tsv(tmp_path, df, name="data.txt") -> Path:
    """Tab-separated with .txt extension — exercises the sniffer."""
    p = tmp_path / name
    df.to_csv(p, index=False, sep="\t")
    return p


def _flat_graph(result) -> rdflib.Graph:
    g = rdflib.Graph()
    for s, p, o, _ in result.graph.quads():
        g.add((s, p, o))
    return g


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_accepts_dict(self):
        QuickMapper(_MINIMAL_MAPPING)

    def test_accepts_yaml_path(self, tmp_path):
        import yaml
        p = tmp_path / "m.yaml"
        p.write_text(yaml.dump(_MINIMAL_MAPPING), encoding="utf-8")
        QuickMapper(p)

    def test_accepts_string_path(self, tmp_path):
        import yaml
        p = tmp_path / "m.yaml"
        p.write_text(yaml.dump(_MINIMAL_MAPPING), encoding="utf-8")
        QuickMapper(str(p))


# ---------------------------------------------------------------------------
# File format support
# ---------------------------------------------------------------------------

class TestFileFormats:
    def test_csv(self, tmp_path):
        result = QuickMapper(_MINIMAL_MAPPING).run(_write_csv(tmp_path, _SAMPLE_DF))
        assert len(result.dataframe) == 3

    def test_tsv(self, tmp_path):
        result = QuickMapper(_MINIMAL_MAPPING).run(_write_tsv(tmp_path, _SAMPLE_DF))
        assert len(result.dataframe) == 3

    def test_txt_sniffed_as_tsv(self, tmp_path):
        result = QuickMapper(_MINIMAL_MAPPING).run(_write_txt_tsv(tmp_path, _SAMPLE_DF))
        assert len(result.dataframe) == 3

    @pytest.mark.skipif(not _has_openpyxl, reason="openpyxl not installed")
    def test_excel(self, tmp_path):
        result = QuickMapper(_MINIMAL_MAPPING).run(_write_excel(tmp_path, _SAMPLE_DF))
        assert len(result.dataframe) == 3

    def test_parquet(self, tmp_path):
        p = tmp_path / "data.parquet"
        _SAMPLE_DF.to_parquet(p, index=False)
        result = QuickMapper(_MINIMAL_MAPPING).run(p)
        assert len(result.dataframe) == 3

    def test_explicit_format_override(self, tmp_path):
        """A .txt file forced to csv format via the mapping config."""
        p = tmp_path / "data.txt"
        _SAMPLE_DF.to_csv(p, index=False)  # comma-separated despite .txt
        mapping = {**_MINIMAL_MAPPING, "file": {"format": "csv"}}
        result = QuickMapper(mapping).run(p)
        assert len(result.dataframe) == 3

    def test_json(self, tmp_path):
        p = tmp_path / "data.json"
        _SAMPLE_DF.to_json(p, orient="records")
        result = QuickMapper(_MINIMAL_MAPPING).run(p)
        assert len(result.dataframe) == 3

    def test_unsupported_format_raises(self, tmp_path):
        p = tmp_path / "data.txt"
        p.write_text("x")
        mapping = {**_MINIMAL_MAPPING, "file": {"format": "odf"}}
        with pytest.raises(ValueError, match="Unsupported format"):
            QuickMapper(mapping).run(p)


# ---------------------------------------------------------------------------
# TransformResult
# ---------------------------------------------------------------------------

class TestConversionResult:
    @pytest.fixture(scope="class")
    def result(self, tmp_path_factory):
        tmp = tmp_path_factory.mktemp("csv")
        return QuickMapper(_MINIMAL_MAPPING).run(_write_csv(tmp, _SAMPLE_DF))

    def test_dataframe_all_columns_present(self, result):
        assert set(_SAMPLE_DF.columns).issubset(set(result.dataframe.columns))

    def test_dataframe_row_count(self, result):
        assert len(result.dataframe) == 3

    def test_column_iris_only_mapped(self, result):
        assert set(result.column_iris.keys()) == {"Force", "Extension"}

    def test_column_units_only_columns_with_unit(self, result):
        assert set(result.column_units.keys()) == {"Force"}

    def test_oold_doc_has_label(self, result):
        assert result.oold_doc["label"] == "test-run"

    def test_oold_doc_has_columns(self, result):
        assert "Force" in result.oold_doc["columns"]


# ---------------------------------------------------------------------------
# RDF graph structure
# ---------------------------------------------------------------------------

class TestRDFGraph:
    @pytest.fixture(scope="class")
    def graph(self, tmp_path_factory):
        tmp = tmp_path_factory.mktemp("rdf")
        result = QuickMapper(_MINIMAL_MAPPING).run(_write_csv(tmp, _SAMPLE_DF))
        return _flat_graph(result)

    def test_graph_non_empty(self, graph):
        assert len(graph) > 0

    def test_root_node_typed_as_dcat_dataset(self, graph):
        DCAT = rdflib.Namespace("http://www.w3.org/ns/dcat#")
        subjects = list(graph.subjects(rdflib.RDF.type, DCAT.Dataset))
        assert len(subjects) == 1

    def test_root_node_has_label(self, graph):
        labels = list(graph.objects(predicate=rdflib.RDFS.label))
        assert any(str(l) == "test-run" for l in labels)

    def test_mapped_columns_have_distribution_triples(self, graph):
        DCAT = rdflib.Namespace("http://www.w3.org/ns/dcat#")
        distributions = list(graph.subject_objects(DCAT.distribution))
        assert len(distributions) == 2  # Force + Extension

    def test_force_column_has_unit(self, graph):
        QUDT = rdflib.Namespace("http://qudt.org/schema/qudt/")
        units = list(graph.objects(predicate=QUDT.hasUnit))
        assert any(str(u) == "http://qudt.org/vocab/unit/N" for u in units)

    def test_extension_column_has_no_unit(self, graph):
        QUDT = rdflib.Namespace("http://qudt.org/schema/qudt/")
        # Extension has no unit in the mapping
        units = list(graph.objects(predicate=QUDT.hasUnit))
        assert all("Extension" not in str(u) for u in units)

    def test_custom_root_type(self, tmp_path):
        mapping = {
            **_MINIMAL_MAPPING,
            "root_type": "https://example.org/MyProcess",
        }
        result = QuickMapper(mapping).run(_write_csv(tmp_path, _SAMPLE_DF))
        g = _flat_graph(result)
        MY = rdflib.URIRef("https://example.org/MyProcess")
        assert (None, rdflib.RDF.type, MY) in g


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------

class TestOverrides:
    def test_label_override(self, tmp_path):
        result = QuickMapper(_MINIMAL_MAPPING).run(
            _write_csv(tmp_path, _SAMPLE_DF),
            label="overridden label",
        )
        assert result.oold_doc["label"] == "overridden label"

    def test_default_label_is_file_stem(self, tmp_path):
        mapping = {k: v for k, v in _MINIMAL_MAPPING.items() if k != "label"}
        result = QuickMapper(mapping).run(_write_csv(tmp_path, _SAMPLE_DF, name="my_run.csv"))
        assert result.oold_doc["label"] == "my_run"


# ---------------------------------------------------------------------------
# File reading options
# ---------------------------------------------------------------------------

class TestFileOptions:
    def test_skip_rows(self, tmp_path):
        p = tmp_path / "data.csv"
        p.write_text("# comment\nForce,Extension,Temperature\n0,0,22\n1,1,22\n", encoding="utf-8")
        mapping = {**_MINIMAL_MAPPING, "file": {"skip_rows": 1}}
        result = QuickMapper(mapping).run(p)
        assert len(result.dataframe) == 2

    def test_custom_separator(self, tmp_path):
        p = tmp_path / "data.csv"
        p.write_text("Force;Extension;Temperature\n0;0;22\n100;1;23\n", encoding="utf-8")
        mapping = {**_MINIMAL_MAPPING, "file": {"separator": ";"}}
        result = QuickMapper(mapping).run(p)
        assert len(result.dataframe) == 2
