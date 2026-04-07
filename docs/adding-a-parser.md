# Adding a parser

This guide covers two scenarios:

- **Your instrument file is similar to an existing parser** — adjust it with a
  config file or constructor arguments (no Python needed)
- **Your instrument has a completely different file format** — write a new
  extractor from scratch

---

## Scenario A: adapting an existing parser

If your machine exports a file that is *structurally similar* to one the
repository already handles — same section layout, just different label names,
a different metadata row count, or different column names — you can configure
the existing extractor without touching Python.

### Using a config YAML (recommended)

Create a YAML file next to your data file and pass it to `from_config()`:

```yaml
# parser_config.yaml
metadata_rows: 15           # rows before the column-header row (default: 20)
strain_rate_label: null     # null = skip; use a label string to enable
meta_field_map:
  Temperature: [temperature, float]
  Standard:    [test_standard, str]
  Speed:       [strain_rate, float]
```

```python
from extractor import ZwickExtractor
from semantic_transformers import Transformer

transformer = Transformer(
    extractor = ZwickExtractor.from_config("parser_config.yaml"),
    transform = ".../simplified/transform.jsonata",
    context   = ".../specs/schema.oold.yaml",
)
result = transformer.run("my_file.txt")
```

### Using keyword arguments

Pass the same settings directly as keyword arguments when constructing the
extractor:

```python
ZwickExtractor(
    metadata_rows     = 15,
    strain_rate_label = None,
    meta_field_map    = {
        "Temperature": ("temperature", "float"),
        "Standard":    ("test_standard", "str"),
    },
)
```

If you also need to use a custom `column_mapping.json` (to remap or rename
measurement columns), pass it as the first argument:

```python
ZwickExtractor(
    "/path/to/my_column_mapping.json",
    metadata_rows = 15,
)
```

---

## Scenario B: writing a new extractor

Use this path when the file format is fundamentally different — different
section structure, binary format, or a machine family not yet in the
repository.

### Folder structure

```
parsers/<domain>/<specialisation>/<machine>/
  extractor.py         Reads the instrument file → ExtractionResult
  column_mapping.json  Maps column names to ontology IRIs and QUDT units
  compatibility.yaml   Documents which schemas and instruments this covers
  README.md            Quick-start and known limitations
```

The folder path mirrors the `schemas/` tree in `semantic-schemas`, but
without the ontology subfolder. For example:

```
schemas/characterization/tensile-test/TTO/   ← schema
parsers/characterization/tensile-test/zwick/ ← parser
```

### Step 1: choose the target schema

Decide which schema in `semantic-schemas` this parser feeds into. The
simplified JSON your extractor produces must match the fields in that schema's
`simplified/schema.simplified.json`.

### Step 2: create the folder

```bash
mkdir -p parsers/<domain>/<specialisation>/<machine>/
```

### Step 3: write the extractor

Copy the Zwick extractor as a starting point:

```bash
cp parsers/characterization/tensile-test/zwick/extractor.py \
   parsers/<domain>/<specialisation>/<machine>/extractor.py
```

Your extractor must implement one method:

```python
from semantic_transformers import ExtractionResult

class MyExtractor:
    def extract(self, path: Path) -> ExtractionResult:
        ...
        return ExtractionResult(
            simplified_json = { ... },  # fields matching the schema's simplified input
            timeseries      = df,        # pandas DataFrame or None
            column_iris     = { ... },  # column name → ontology class IRI
            column_units    = { ... },  # column name → QUDT unit IRI
        )
```

Rules:
- `simplified_json` must use field names from the target schema's
  `simplified/schema.simplified.json`. Unknown keys are ignored by the
  JSONata transform.
- `timeseries` is optional. Pass `None` if the file has no tabular data.
- `column_iris` and `column_units` are optional.

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

### Step 5: write compatibility.yaml

Copy and edit `parsers/characterization/tensile-test/zwick/compatibility.yaml`.
Update `tested_with` whenever the target schema changes.

### Step 6: write README.md

Include: supported models, what the extractor reads, known limitations,
and a quick-start code snippet.

### Step 7: test end-to-end

```python
import sys
sys.path.insert(0, 'parsers/<domain>/<specialisation>/<machine>')

from extractor import MyExtractor
from semantic_transformers import Transformer

transformer = Transformer(
    extractor = MyExtractor(),
    transform = '/path/to/semantic-schemas/schemas/.../simplified/transform.jsonata',
    context   = '/path/to/semantic-schemas/schemas/.../specs/schema.oold.yaml',
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

- The four files in the correct folder
- A sample instrument file in `tests/data/` (anonymised if needed)
- A test module that runs `transformer.run()` on the sample file

The CI pipeline runs `nbmake` on the relevant schema notebook to confirm
end-to-end compatibility.
