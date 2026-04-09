# Parser: Zwick/Roell → characterization/tensile-test/TTO

Reads a Zwick/Roell testXpert III export and converts it to the
`characterization/tensile-test/TTO` simplified schema, ready to be fed into
a `Transformer`.

## Schema compatibility

| Schema | Version | Files used |
|---|---|---|
| `characterization/tensile-test/TTO` | 1.0.0 | `simplified/transform.jsonata`, `specs/schema.oold.yaml`, `specs/shape.ttl` |

Update the version here whenever you re-test against a newer schema release.

## Supported instruments

| Brand | Models | Software | Export format |
|---|---|---|---|
| Zwick/Roell | Z020, Z100, Z250 | testXpert III | Tab-separated text (.TXT), UTF-8 |

## File layout

| Rows | Content |
|---|---|
| 1–20 | Metadata block: `"label" \t value [\t "unit"]` (German labels, quoted strings) |
| 21 | Column headers (quoted, tab-separated) |
| 22 | Column units (used for `strain_rate_unit`; data units come from `column_mapping.json`) |
| 23+ | Numeric time-series data |

Mapped metadata fields:

| Exported label | Simplified JSON field |
|---|---|
| `Prüfnorm` | `test_standard` |
| `Temperatur` | `temperature` |
| `Prüfgeschwindigkeit` | `strain_rate` |

## Quick start

```python
import sys
sys.path.insert(0, '/path/to/semantic-transformers/parsers/characterization/tensile-test/zwick')

from zwick_parser import ZwickParser
from semantic_transformers import Transformer

transformer = Transformer(
    parser          = ZwickParser(),
    semantic_schema = '/path/to/semantic-schemas/schemas/characterization/tensile-test/TTO/',
)

result = transformer.run('my_test.TXT')
print(result.graph.serialize(format='turtle'))
print(result.dataframe.head())
```

For a full walkthrough, see the
[tensile test CSV notebook](../../../../../semantic-schemas/schemas/characterization/tensile-test/TTO/docs/2_tensile_test_csv_workflow.ipynb).

## Adapting to your Zwick file variant

If your software version or machine template produces a different header
length, uses different label names, or is localised to another language, use
a config YAML instead of editing Python.

Create a `parser_config.yaml` next to your data file:

```yaml
metadata_rows: 15               # rows before the column-header row
strain_rate_label: null         # null = skip; default is "Prüfgeschwindigkeit"
meta_field_map:
  Temperature: temperature
  Standard:    test_standard
  Speed:       strain_rate
```

Then pass it to `from_config()`:

```python
ZwickParser.from_config("parser_config.yaml")
```

The same settings are available as keyword arguments if you prefer to stay in Python:

```python
ZwickParser(
    metadata_rows     = 15,
    strain_rate_label = None,
    meta_field_map    = {"Temperature": "temperature"},
)
```

For a completely different file structure (different section layout, binary
format), copy this parser and override `_parse_metadata()` and
`_parse_timeseries()`. The `Transformer` and the schema do not need to change.
See `docs/2_adding-a-parser.md` for the full guide.

## Known limitations

- Scalar result values (Rp0.2, Rm, A, Z) are not in the Zwick export and must
  be added separately or computed from the time series.
- Assumes UTF-8 encoding.
