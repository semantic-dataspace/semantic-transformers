# semantic-transformers

A library and a curated collection of parsers that bridge raw instrument output
files and the [semantic-schemas](../semantic-schemas/) knowledge graph pipeline.

## What this repository contains

```text
semantic-transformers/
  src/semantic_transformers/   Python library (Transformer, QuickMapper, …)
    parsers/                   Machine-specific file parsers
      <domain>/                Mirrors the semantic-schemas folder structure
        <specialisation>/
          <machine>/           One folder per instrument model
            <machine>_parser.py  Reads the instrument file
            column_mapping.json  Maps column names to ontology class IRIs and units
            README.md            Quick-start, schema compatibility, and known limitations
  docs/                        Guides for users and contributors
```

## The two parts

### 1. The library (`src/semantic_transformers/`)

| Class | Role |
|---|---|
| `Parser` | Protocol to implement when adding support for a new instrument |
| `ParseResult` | What every parser returns: simplified JSON + DataFrame |
| `Transformer` | Runs parsing → JSONata transform → RDF graph |
| `TransformResult` | What `Transformer.run()` returns: RDF graph + DataFrame |
| `QuickMapper` | Turns any tabular file into RDF using a simple YAML mapping (no parser needed) |

### 2. The parsers (`src/semantic_transformers/parsers/`)

Each parser targets a specific instrument model. The folder path mirrors the
`schemas/` tree in `semantic-schemas`:

| Schema | Instrument | Import path |
|---|---|---|
| `characterization/tensile-test/TTO` | Zwick/Roell (testXpert III) | `semantic_transformers.parsers.characterization.tensile_test.zwick` |

## Installation

### Using pip (recommended)

```bash
# Install the transformers library
pip install semantic-transformers

# Optional: install optional dependencies
pip install semantic-transformers[excel]  # for Excel file support
pip install semantic-transformers[dev]    # for development and testing
```

### Development installation

Both repositories are designed to be cloned as siblings under a shared folder:

```bash
mkdir semantic-dataspace && cd semantic-dataspace

git clone https://github.com/Semantic-Dataspace/semantic-schemas
git clone https://github.com/Semantic-Dataspace/semantic-transformers

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e semantic-transformers/
pip install jupyterlab            # only needed for the interactive notebooks
```

## Two ways to use this library

### Option A: you have a supported instrument

Use a ready-made parser and the matching schema notebook. For a Zwick/Roell
tensile test:

```bash
jupyter lab semantic-schemas/schemas/characterization/tensile-test/TTO/docs/2_tensile_test_csv_workflow.ipynb
```

Edit **Step 0** (one line, point to your file) and run all cells. Done.

### Option B: you have a tabular file with no existing parser

Use `QuickMapper`. Provide a short YAML that names the columns and points each
one at an ontology class IRI:

```python
from semantic_transformers import QuickMapper

mapping = {
    "label": "my experiment",
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

result = QuickMapper(mapping).run("my_data.csv")
print(result.graph.serialize(format="turtle"))
print(result.dataframe.head())
```

Supported file formats: CSV, TSV, Excel (.xlsx), Parquet, JSON.
See the [QuickMapper notebook](docs/3_quickstart-mapping.ipynb) for a guided walkthrough.

## Contributing

To contribute or run tests locally, see [CONTRIBUTING.md](CONTRIBUTING.md) for setup
and development workflow instructions.

## Documentation

- [Getting started](docs/1_getting-started.md): convert your first instrument file
- [QuickMapper walkthrough](docs/3_quickstart-mapping.ipynb): turn any tabular file into RDF
- [Adding a parser](docs/2_adding-a-parser.md): support a new instrument or handle file variants
