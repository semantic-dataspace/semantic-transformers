"""
Tests for the Zwick/Roell parser.

Pure unit tests - no schema files, no Transformer, no RDF.
The conftest session fixture puts the parser directory on sys.path.
"""
import json
from pathlib import Path

import pytest

from zwick_parser import ZwickParser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def parser():
    return ZwickParser()


@pytest.fixture(scope="module")
def result(parser, zwick_txt):
    return parser.parse(zwick_txt)


# ---------------------------------------------------------------------------
# simplified_json
# ---------------------------------------------------------------------------

class TestSimplifiedJson:
    def test_test_name_is_file_stem(self, result):
        assert result.simplified_json["test_name"] == "DX56_D_FZ2_WR00_43"

    def test_test_standard_extracted(self, result):
        assert result.simplified_json["test_standard"] == "ISO 6892-1"

    def test_temperature_extracted(self, result):
        assert result.simplified_json["temperature"] == pytest.approx(22.0)

    def test_strain_rate_extracted(self, result):
        assert result.simplified_json["strain_rate"] == pytest.approx(0.1)

    def test_strain_rate_unit_extracted(self, result):
        assert result.simplified_json["strain_rate_unit"] == "mm/s"


# ---------------------------------------------------------------------------
# DataFrame
# ---------------------------------------------------------------------------

class TestTimeseries:
    def test_dataframe_is_not_none(self, result):
        assert result.timeseries is not None

    def test_expected_columns_present(self, result):
        expected = {
            "Prüfzeit", "Standardkraft", "Traversenweg absolut",
            "Standardweg", "Breitenänderung", "Dehnung",
        }
        assert expected.issubset(set(result.timeseries.columns))

    def test_row_count(self, result):
        # Sample file has 82 data rows (realistic DX56 tensile curve)
        assert len(result.timeseries) == 82

    def test_first_row_values_are_zero(self, result):
        assert result.timeseries["Standardkraft"].iloc[0] == pytest.approx(0.0)

    def test_values_are_numeric(self, result):
        assert result.timeseries["Standardkraft"].dtype.kind == "f"


# ---------------------------------------------------------------------------
# Column annotations
# ---------------------------------------------------------------------------

class TestColumnAnnotations:
    def test_column_iris_non_empty(self, result):
        assert len(result.column_iris) > 0

    def test_column_units_non_empty(self, result):
        assert len(result.column_units) > 0

    def test_standardkraft_iri(self, result):
        assert "Standardkraft" in result.column_iris
        assert "StandardForce" in result.column_iris["Standardkraft"]

    def test_standardkraft_unit_ends_with_newton(self, result):
        assert "Standardkraft" in result.column_units
        assert result.column_units["Standardkraft"].endswith("/N")

    def test_all_annotated_columns_exist_in_dataframe(self, result):
        df_cols = set(result.timeseries.columns)
        for col in result.column_iris:
            assert col in df_cols, f"column_iris key '{col}' not found in DataFrame"


# ---------------------------------------------------------------------------
# Custom column_mapping_path
# ---------------------------------------------------------------------------

def test_custom_mapping_path(tmp_path, zwick_txt):
    mapping = [
        {"key": "Standardkraft", "iri": "https://example.org/Force", "unit_iri": "http://qudt.org/vocab/unit/N"}
    ]
    mapping_file = tmp_path / "custom_mapping.json"
    mapping_file.write_text(json.dumps(mapping), encoding="utf-8")

    result = ZwickParser(column_mapping_path=mapping_file).parse(zwick_txt)

    assert result.column_iris["Standardkraft"] == "https://example.org/Force"
    assert result.column_units["Standardkraft"] == "http://qudt.org/vocab/unit/N"
    # Columns not in the custom mapping are absent from annotations
    assert "Prüfzeit" not in result.column_iris


# ---------------------------------------------------------------------------
# Configurable layout
# ---------------------------------------------------------------------------

def test_custom_metadata_rows(zwick_txt):
    """metadata_rows=20 (explicit) gives the same result as the default."""
    default_result  = ZwickParser().parse(zwick_txt)
    explicit_result = ZwickParser(metadata_rows=20).parse(zwick_txt)
    assert explicit_result.simplified_json == default_result.simplified_json
    assert len(explicit_result.timeseries) == len(default_result.timeseries)


def test_custom_meta_field_map(zwick_txt):
    """A custom field map with an unknown label yields no fields from it."""
    result = ZwickParser(
        meta_field_map={"NonExistentLabel": "my_field"},
        strain_rate_label=None,
    ).parse(zwick_txt)
    assert "my_field" not in result.simplified_json
    assert "strain_rate_unit" not in result.simplified_json


def test_strain_rate_label_none(zwick_txt):
    """Setting strain_rate_label=None suppresses strain_rate_unit."""
    result = ZwickParser(strain_rate_label=None).parse(zwick_txt)
    assert "strain_rate_unit" not in result.simplified_json


def test_configure_drives_type_coercion(zwick_txt):
    """After configure(), field types come from the schema.

    Declare temperature as "string" so the parser must return a str even
    though the raw value ("22.0") would normally be cast to float by the
    heuristic fallback.
    """
    schema = {
        "type": "object",
        "properties": {
            "test_standard": {"type": "string"},
            "temperature":   {"type": "string"},   # intentionally wrong type
            "strain_rate":   {"type": "number"},
        },
    }
    p = ZwickParser()
    p.configure(schema)
    result = p.parse(zwick_txt)

    # temperature declared as string → raw value kept as str, not cast to float
    assert isinstance(result.simplified_json["temperature"], str)
    # strain_rate declared as number → still cast to float
    assert isinstance(result.simplified_json["strain_rate"], float)
    # test_standard is always a string; declaration matches reality
    assert isinstance(result.simplified_json["test_standard"], str)


def test_configure_number_field(zwick_txt):
    """After configure(), a "number" field is cast to float."""
    schema = {
        "type": "object",
        "properties": {
            "temperature": {"type": "number"},
        },
    }
    p = ZwickParser()
    p.configure(schema)
    result = p.parse(zwick_txt)

    assert isinstance(result.simplified_json["temperature"], float)
    assert result.simplified_json["temperature"] == pytest.approx(22.0)


def test_from_config(tmp_path, zwick_txt):
    """from_config() loads layout settings from a YAML file."""
    config = tmp_path / "parser_config.yaml"
    config.write_text(
        "metadata_rows: 20\n"
        "strain_rate_label: null\n"
        "meta_field_map:\n"
        "  Prüfnorm: test_standard\n",
        encoding="utf-8",
    )
    result = ZwickParser.from_config(config).parse(zwick_txt)
    # Field map in config maps Prüfnorm → test_standard
    assert result.simplified_json["test_standard"] == "ISO 6892-1"
    # strain_rate_label: null → no strain_rate_unit
    assert "strain_rate_unit" not in result.simplified_json
