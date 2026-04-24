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
from semantic_transformers.parsers.characterization.tensile_test.testxpert_iii import TestXpertIIIParser
from semantic_transformers import Transformer

transformer = Transformer(
    parser          = TestXpertIIIParser.from_config("parser_config.yaml"),
    semantic_schema = "/path/to/semantic-schemas/schemas/characterization/tensile-test/TTO/",
)
result = transformer.run("my_file.txt")
```

### Using keyword arguments

Pass the same settings directly as keyword arguments when constructing the parser:

```python
TestXpertIIIParser(
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
TestXpertIIIParser(
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
src/semantic_transformers/parsers/<domain>/<specialisation>/<instrument>/
  __init__.py    Re-exports the parser class (defaults to the primary locale)
  parser.py      Language-agnostic parsing logic → ParseResult
  README.md      Format specification, quick start, schema compatibility
  CHANGELOG.md   Schema compatibility history
  <lang>/        One subfolder per export language (e.g. de/, en/)
    __init__.py        Re-exports the parser class pre-configured for this locale
    column_mapping.json  Maps locale-specific column names to ontology IRIs and QUDT units
```

`parser.py` contains the parsing logic shared across all locales. Language
subfolders hold only locale-specific data: `column_mapping.json` whose keys
are the instrument-generated column names, which vary by locale. `README.md`
and `CHANGELOG.md` live at the instrument level, not inside locale subfolders.

The folder path mirrors the `schemas/` tree in `semantic-schemas`, but
without the ontology subfolder. Directory names must use underscores (not
hyphens) to be valid Python identifiers. For example:

```text
schemas/characterization/tensile-test/TTO/                                     ← schema
src/semantic_transformers/parsers/characterization/tensile_test/testxpert_iii/ ← parser
src/semantic_transformers/parsers/characterization/tensile_test/testxpert_iii/de/ ← German locale data
```

### Step 1: choose the target schema

Decide which schema in `semantic-schemas` this parser feeds into. The
simplified JSON your parser produces must match the fields in that schema's
`specs/schema.simplified.json`.

### Step 2: create the folder

```bash
mkdir -p src/semantic_transformers/parsers/<domain>/<specialisation>/<instrument>/<lang>/
```

### Step 3: write the parser

Copy the testXpert III parser as a starting point:

```bash
cp src/semantic_transformers/parsers/characterization/tensile_test/testxpert_iii/parser.py \
   src/semantic_transformers/parsers/<domain>/<specialisation>/<instrument>/parser.py
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

### Step 5: write README.md and CHANGELOG.md

Place both files in the instrument folder (e.g. `testxpert_iii/`), not inside
a locale subfolder.

**README.md** — use the testXpert III README as a template. Include:

- **Schema compatibility**: the `semantic-schemas` path and the current tested version.
  Update this table whenever you re-test against a newer schema release.
  Link to `CHANGELOG.md` for the full history.
- **Supported instruments**: brand, models, software version, export format.
- **File format**: the format spec (section layout, row structure).
- **Locale variants**: one row per `<lang>/` subfolder, listing what column names
  that locale exports.
- **Quick start**: a minimal code snippet.
- **Known limitations**.

**CHANGELOG.md** — a compact compatibility table mapping parser versions to schema
versions (use the testXpert III CHANGELOG as a template). This is the authoritative
record of which parser release was tested against which schema release. It is not
shipped in the PyPI wheel; it is for contributors and schema maintainers.

### Step 6: test end-to-end

```python
from semantic_transformers.parsers.<domain>.<specialisation>.<instrument> import MyParser
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

flat   = result.flat_graph   # rdflib.Graph with correct namespace bindings
shapes = rdflib.Graph().parse('/path/to/.../specs/shape.ttl')
conforms, _, report = pyshacl.validate(flat, shacl_graph=shapes, inference='rdfs')
print('Conforms:', conforms)
if not conforms:
    print(report)
```

### Submitting your parser

Open a pull request with:

- `parser.py` in the instrument folder, plus `column_mapping.json`, `README.md`, `CHANGELOG.md` in the language subfolder
- A sample instrument file in `tests/data/` (anonymised if needed)
- A test module that runs `transformer.run()` on the sample file

The CI pipeline runs `nbmake` on the relevant schema notebook to confirm
end-to-end compatibility.
