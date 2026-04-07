# semantic-transformers

A library and a curated collection of parsers that bridge raw instrument output
files and the [semantic-schemas](../semantic-schemas/) knowledge graph pipeline.

## What this repository contains

```
semantic-transformers/
  src/semantic_transformers/   Python library (Converter, Extractor protocol)
  parsers/                     Machine-specific file parsers, mirroring the
    <domain>/                  semantic-schemas folder structure
      <specialisation>/
        <Ontology>/
          <machine>/           One folder per instrument model
            extractor.py       Reads the instrument file and returns simplified JSON
            column_mapping.json Maps column names to ontology class IRIs
            compatibility.yaml  Which schemas and instruments this covers
            README.md           Quick-start and limitations
```

## The two parts

### 1. The library (`src/semantic_transformers/`)

Provides two building blocks:

| Class / protocol | Role |
|---|---|
| `Extractor` | Protocol to implement when adding support for a new instrument |
| `ExtractionResult` | What every extractor returns: simplified JSON + DataFrame |
| `Converter` | Runs extraction, JSONata transform, and RDF graph construction |
| `ConversionResult` | What `Converter.run()` returns: RDF graph + DataFrame |

### 2. The parsers (`parsers/`)

Each parser targets a specific combination of instrument model and schema.
The folder path mirrors the `schemas/` tree in `semantic-schemas`:

| Schema | Instrument | Parser path |
|---|---|---|
| `characterization/tensile-test/TTO` | Zwick/Roell (testXpert III) | `parsers/characterization/tensile-test/TTO/zwick/` |

## Installation

```bash
git clone https://github.com/your-org/semantic-transformers
cd semantic-transformers
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Both repositories are designed to be cloned as siblings:

```
semantic-dataspace/
  semantic-schemas/      ← schemas, transforms, notebooks
  semantic-transformers/ ← this repository
  .venv/                 ← shared virtual environment
```

## Quick example

```python
import sys
from pathlib import Path

# Point to the parser for your instrument
TRANSFORMERS = Path('/path/to/semantic-transformers')
sys.path.insert(0, str(TRANSFORMERS / 'parsers' / 'characterization' / 'tensile-test' / 'TTO' / 'zwick'))

from extractor import ZwickExtractor
from semantic_transformers import Converter

SCHEMAS = Path('/path/to/semantic-schemas/schemas')
TTO     = SCHEMAS / 'characterization' / 'tensile-test' / 'TTO'

converter = Converter(
    extractor = ZwickExtractor(),
    transform = TTO / 'simplified' / 'transform.jsonata',
    context   = TTO / 'specs'      / 'schema.oold.yaml',
)

result = converter.run('my_tensile_test.TXT')

# RDF graph, ready to publish
print(result.graph.serialize(format='turtle'))

# pandas DataFrame, ready to analyse
print(result.dataframe.head())
```

For a full interactive walkthrough, open the tensile test notebook:

```
semantic-schemas/schemas/characterization/tensile-test/TTO/docs/tensile_test_csv_workflow.ipynb
```

## Documentation

- [Getting started](docs/getting-started.md): convert your first instrument file
- [Adding a parser](docs/adding-a-parser.md): support a new instrument or schema
