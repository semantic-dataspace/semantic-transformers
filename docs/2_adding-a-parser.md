# Adding a parser

This guide covers two scenarios:

- **Your instrument file is similar to an existing parser**: adjust it with a
  config file or constructor arguments (no Python needed)
- **Your instrument has a completely different file format**: write a new
  parser from scratch

---

## Scenario A: adapting an existing parser

If your machine exports a file that is *structurally similar* to one the
repository already handles (same section layout, just different label names,
a different metadata row count, or different column names), you can configure
the existing parser without touching Python.

### Using a config YAML (recommended)

Create a YAML file next to your data file and pass it to `from_config()`:

```yaml
# parser_config.yaml
metadata_rows: 15           # rows before the column-header row (default: 20)
strain_rate_label: null     # null = skip; use a label string to enable
meta_field_map:
  Temperature: temperature
  Standard:    test_standard
  Speed:       strain_rate
```

```python
from zwick_parser import ZwickParser
from semantic_transformers import Transformer

transformer = Transformer(
    parser          = ZwickParser.from_config("parser_config.yaml"),
    semantic_schema = "/path/to/semantic-schemas/schemas/characterization/tensile-test/TTO/",
)
result = transformer.run("my_file.txt")
```

### Using keyword arguments

Pass the same settings directly as keyword arguments when constructing the parser:

```python
ZwickParser(
    metadata_rows     = 15,
    strain_rate_label = None,
    meta_field_map    = {
        "Temperature": "temperature",
        "Standard":    "test_standard",
    },
)
```

If you also need to use a custom `column_mapping.json` (to remap or rename
measurement columns), pass it as the first argument:

```python
ZwickParser(
    "/path/to/my_column_mapping.json",
    metadata_rows = 15,
)
```

---

## Scenario B: writing a new parser

Use this path when the file format is fundamentally different: different
section structure, binary format, or a machine family not yet in the
repository.

### Folder structure

```text
parsers/<domain>/<specialisation>/<machine>/
  <machine>_parser.py  Reads the instrument file → ParseResult
  column_mapping.json  Maps column names to ontology IRIs and QUDT units
  README.md            Quick-start, schema compatibility, and known limitations
```

The folder path mirrors the `schemas/` tree in `semantic-schemas`, but
without the ontology subfolder. For example:

```text
schemas/characterization/tensile-test/TTO/   ← schema
parsers/characterization/tensile-test/zwick/ ← parser
```

### Step 1: choose the target schema

Decide which schema in `semantic-schemas` this parser feeds into. The
simplified JSON your parser produces must match the fields in that schema's
`specs/schema.simplified.json`.

### Step 2: create the folder

```bash
mkdir -p parsers/<domain>/<specialisation>/<machine>/
```

### Step 3: write the parser

Copy the Zwick parser as a starting point:

```bash
cp parsers/characterization/tensile-test/zwick/zwick_parser.py \
   parsers/<domain>/<specialisation>/<machine>/<machine>_parser.py
```

Open the copy and adjust `_parse_metadata()` and `_parse_timeseries()` for
your file's structure. Everything else (`__init__`, `parse()`, the
`from_config()` classmethod) can stay as-is until you need to change it.

Your parser must implement one method:

```python
from semantic_transformers import ParseResult

class MyParser:
    def parse(self, path: Path) -> ParseResult:
        ...
        return ParseResult(
            simplified_json = { ... },  # fields matching the schema's simplified input
            timeseries      = df,        # pandas DataFrame or None
            column_iris     = { ... },  # column name → ontology class IRI
            column_units    = { ... },  # column name → QUDT unit IRI
        )
```

Rules:

- `simplified_json` must use field names from the target schema's
  `specs/schema.simplified.json`. Unknown keys are ignored by the
  JSONata transform.
- `timeseries` is optional. Pass `None` if the file has no tabular data.
- `column_iris` and `column_units` are optional.

#### Optional: schema-driven type coercion

If your parser needs to cast raw string values to the types declared in the
schema (e.g. `"22.0"` → `float` for a `"number"` field), inherit from
`SchemaAwareParser` and implement `configure()`:

```python
from semantic_transformers import ParseResult
from semantic_transformers.parser import SchemaAwareParser

class MyParser(SchemaAwareParser):
    def configure(self, schema: dict) -> None:
        # Called automatically by Transformer with the loaded input schema.
        self._field_types = {
            name: prop.get("type", "string")
            for name, prop in schema.get("properties", {}).items()
        }

    def parse(self, path: Path) -> ParseResult:
        ...
```

`Transformer` detects `SchemaAwareParser` via `isinstance` and calls
`configure(schema)` at construction time, before the first `run()` call.
The user sees none of this; they just write
`Transformer(parser=MyParser(), semantic_schema=...)` as normal.

### Step 4: create column_mapping.json

```json
[
  {
    "key":      "Force",
    "iri":      "https://w3id.org/pmd/tto/StandardForce",
    "unit_iri": "http://qudt.org/vocab/unit/N"
  }
]
```

Columns without a known ontology class can be omitted; they still appear in
the DataFrame.

### Step 5: write README.md

Include these sections (use the Zwick README as a template):

- **Schema compatibility**: the `semantic-schemas` path and version you tested against, and
  which files are used (`transform.jsonata`, `schema.oold.yaml`, `shape.ttl`).
  Update the version whenever you re-test against a newer schema release.
- **Supported instruments**: brand, models, software version, export format.
- **File layout**: what the parser expects to find in the file.
- **Quick start**: a minimal code snippet.
- **Known limitations**.

### Step 6: test end-to-end

```python
import sys
sys.path.insert(0, 'parsers/<domain>/<specialisation>/<machine>')

from <machine>_parser import MyParser
from semantic_transformers import Transformer

transformer = Transformer(
    parser          = MyParser(),
    semantic_schema = '/path/to/semantic-schemas/schemas/.../',
)

result = transformer.run('my_test_file.csv')
print(json.dumps(result.oold_doc, indent=2))
print(result.graph.serialize(format='turtle'))
print(result.dataframe.head())
```

Validate against SHACL shapes to catch structural errors early:

```python
import pyshacl, rdflib

flat = rdflib.Graph()
for s, p, o, _ in result.graph.quads():
    flat.add((s, p, o))

shapes = rdflib.Graph().parse('/path/to/.../specs/shape.ttl')
conforms, _, report = pyshacl.validate(flat, shacl_graph=shapes, inference='rdfs')
print('Conforms:', conforms)
if not conforms:
    print(report)
```

### Submitting your parser

Open a pull request with:

- The three files in the correct folder (`<machine>_parser.py`, `column_mapping.json`, `README.md`)
- A sample instrument file in `tests/data/` (anonymised if needed)
- A test module that runs `transformer.run()` on the sample file

The CI pipeline runs `nbmake` on the relevant schema notebook to confirm
end-to-end compatibility.
