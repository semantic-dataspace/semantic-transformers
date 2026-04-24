# Parser: Zwick/Roell testXpert III

Reads a Zwick/Roell [testXpert III](https://www.zwickroell.com/accessories/testxpert-testing-software/)
export and produces a `ParseResult` ready to be fed into a `Transformer`.

## Schema compatibility

| Schema | Version | Tested |
|---|---|---|
| `characterization/tensile-test/TTO` | **1.1.0** | 2026-04-10 |

Update the version and date here whenever you re-test against a newer schema release.
For the full compatibility history see [CHANGELOG.md](CHANGELOG.md).

## Supported instruments

| Brand | Models | Software | Export format |
|---|---|---|---|
| Zwick/Roell | Z020, Z100, Z250 | testXpert III | Tab-separated text (.TXT), UTF-8 |

## File format

testXpert III exports a tab-separated UTF-8 text file structured as follows:

| Rows | Content |
|---|---|
| 1–N | Metadata block: `"label" \t value [\t "unit"]` (quoted strings) |
| N+1 | Column headers (quoted, tab-separated) |
| N+2 | Column units (quoted; not used by the parser — units come from `column_mapping.json`) |
| N+3+ | Numeric time-series data (bare floats, tab-separated) |

`N` defaults to 20 in the standard testXpert III template and can be overridden
via the `metadata_rows` parameter.

Labels and column names are language-dependent (see locale variants below).

## Locale variants

| Locale | Subfolder | Labels |
|---|---|---|
| German (default) | `de/` | German (`Prüfnorm`, `Standardkraft`, …) |

Each locale subfolder contains a `column_mapping.json` that maps the
locale-specific column names exported by testXpert III to TTO class IRIs and
QUDT unit IRIs.

The default import (`from testxpert_iii import TestXpertIIIParser`) uses the
German locale.  For an explicit locale import:

```python
from semantic_transformers.parsers.characterization.tensile_test.testxpert_iii.de import TestXpertIIIParser
```

## Mapped metadata fields (German locale)

| Exported label | Simplified JSON field | Notes |
|---|---|---|
| `Datum/Uhrzeit` | `test_date` | Excel serial number → ISO 8601 datetime |
| `Prüfnorm` | `test_standard` | string |
| `Prüfgeschwindigkeit` | `strain_rate` + `strain_rate_unit` | value + unit column |
| `Temperatur` | `temperature` | number, °C |
| `Messlänge Standardweg` | `gauge_length` + `gauge_length_unit` | value + unit column |
| `Vorkraft` | `preload` + `preload_unit` | value + unit column |

All other metadata rows (institute, operator, machine ID, specimen dimensions, …)
are not mapped: administrative fields do not belong in the test node and specimen
dimensions belong in the specimen record.

## Mapped time-series columns (German locale)

All six standard columns are annotated with TTO class IRIs and QUDT unit IRIs
via `de/column_mapping.json`. The measurement values are not stored in the RDF
graph; only the column descriptors (class + unit) are.

| Exported column | TTO class | QUDT unit |
|---|---|---|
| `Prüfzeit` | `tto:TestTime` | `unit:SEC` |
| `Standardkraft` | `tto:StandardForce` | `unit:N` |
| `Traversenweg absolut` | `tto:AbsoluteCrossheadTravel` | `unit:MilliM` |
| `Standardweg` | `tto:Extension` | `unit:MilliM` |
| `Breitenänderung` | `tto:WidthChange` | `unit:MilliM` |
| `Dehnung` | `tto:Elongation` | `unit:MilliM` |

## Quick start

```python
from semantic_transformers.parsers.characterization.tensile_test.testxpert_iii import TestXpertIIIParser
from semantic_transformers import Transformer

transformer = Transformer(
    parser          = TestXpertIIIParser(),
    semantic_schema = '/path/to/semantic-schemas/schemas/characterization/tensile-test/TTO/',
)

result = transformer.run('my_test.TXT', base='https://your-institute.org/tests/')
flat   = result.flat_graph                        # rdflib.Graph with correct prefixes
print(flat.serialize(format='turtle'))
print(result.dataframe.head())
```

For a full walkthrough, see the
[tensile test CSV notebook](../../../../semantic-schemas/schemas/characterization/tensile-test/TTO/docs/2_tensile_test_csv_workflow.ipynb).

## Adapting to your file variant

If your software version or machine template produces a different header
length, uses different label names, or exports in another language, use
a config YAML instead of editing Python.

Create a `parser_config.yaml` next to your data file:

```yaml
metadata_rows: 15            # rows before the column-header row

# Override the value-field mapping (label → simplified JSON field):
meta_field_map:
  Temperature: temperature
  Standard:    test_standard
  Speed:       strain_rate

# Override which labels have a companion unit column
# (label → {field: <unit field name>, fallback: <default unit string>}):
unit_field_map:
  Speed:
    field:    strain_rate_unit
    fallback: mm/s
```

Then pass it to `from_config()`:

```python
TestXpertIIIParser.from_config("parser_config.yaml")
```

The same settings are available as keyword arguments:

```python
TestXpertIIIParser(
    metadata_rows  = 15,
    meta_field_map = {"Temperature": "temperature"},
    unit_field_map = {"Speed": ("strain_rate_unit", "mm/s")},
)
```

For a completely different file structure (different section layout, binary
format), copy `parser.py` and override `_parse_metadata()` and
`_parse_timeseries()`. The `Transformer` and the schema do not need to change.
See `docs/2_adding-a-parser.md` for the full guide.

## Known limitations

- Scalar result values (Rp0.2, Rm, A, Z) are not in the testXpert III export
  and must be added separately or computed from the time series.
- Assumes UTF-8 encoding.
