# Getting started

This guide walks you through two scenarios:

- **You have a supported instrument file**: use a ready-made parser
- **You have any other tabular file**: use QuickMapper

---

## Setup (do this once)

Clone both repositories as siblings and create a shared virtual environment:

```bash
mkdir semantic-dataspace && cd semantic-dataspace

git clone https://github.com/your-org/semantic-schemas
git clone https://github.com/your-org/semantic-transformers

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e semantic-transformers/
pip install jupyterlab              # only needed for the interactive notebooks
```

---

## Scenario A: supported instrument (e.g. Zwick/Roell tensile test)

### 1. Open the notebook for your schema

Each schema in `semantic-schemas` has a notebook in its `docs/` folder.
For tensile test data from a Zwick/Roell machine:

```bash
jupyter lab semantic-schemas/schemas/characterization/tensile-test/TTO/docs/2_tensile_test_csv_workflow.ipynb
```

### 2. Point to your file and run

Edit **Step 0** in the notebook (one line):

```python
csv_file = HERE / 'my_tensile_test.TXT'   # ← your file
```

Then run all cells (`Kernel → Restart & Run All`). The notebook will:

1. Read the instrument file and extract metadata and time-series data
2. Transform the metadata into a structured document using the schema transform
3. Build an RDF graph annotated with ontology classes
4. Validate the graph against SHACL shapes
5. Show the measurement column annotations
6. Save the graph as `.ttl` and the time series as `.parquet`

### 3. Collect your results

| File | What it contains |
|---|---|
| `<stem>.ttl` | RDF graph with test metadata and column descriptors, ready to publish |
| `<stem>.parquet` | Full time-series data, ready for analysis |

To load the time series in Python:

```python
import pandas as pd
df = pd.read_parquet('my_tensile_test.parquet')
df.head()
```

### My Zwick file looks slightly different

Zwick software versions and machine templates vary. The metadata block may
have a different row count, different label names, or labels in another language.
You do not need to edit any Python for this. See the
[parser README](../parsers/characterization/tensile-test/zwick/README.md)
for the config-file approach.

### Linking to a specimen (optional)

A tensile test in the knowledge graph is normally linked to the specimen it
consumed. If you have that specimen's IRI already registered in your knowledge
graph, pass it to the transformer in the notebook:

```python
result = transformer.run(
    csv_file,
    specimen_iri = 'https://your-instance.org/specimens/sample-42',
)
```

Without a specimen IRI the graph is valid for exploration but SHACL validation
will flag a missing `has_specified_input`. Add a real IRI before publishing.

---

## Scenario B: any tabular file (CSV, Excel, Parquet, JSON, …)

Use `QuickMapper` when there is no existing parser for your instrument. You
provide a short mapping config; the library handles the rest.

### 1. Inspect your file

Open your file and note the column names you want to semantify.

### 2. Write a mapping config

```python
mapping = {
    "label": "my experiment",          # optional, defaults to the file name
    "columns": {
        "Force": {
            "iri":  "https://w3id.org/pmd/tto/StandardForce",   # ontology class
            "unit": "http://qudt.org/vocab/unit/N",              # QUDT unit (optional)
        },
        "Extension": {
            "iri": "https://w3id.org/pmd/tto/Extension",
        },
        # columns not listed here are still returned in the DataFrame
    },
}
```

You can also save the mapping as a YAML file and load it by path:

```yaml
# mapping.yaml
label: my experiment
columns:
  Force:
    iri:  https://w3id.org/pmd/tto/StandardForce
    unit: http://qudt.org/vocab/unit/N
  Extension:
    iri: https://w3id.org/pmd/tto/Extension
```

### 3. Run QuickMapper

```python
from semantic_transformers import QuickMapper

result = QuickMapper("mapping.yaml").run("my_data.csv")

# RDF graph
print(result.graph.serialize(format="turtle"))

# Time-series data
print(result.dataframe.head())
```

The file format is detected automatically from the extension (`.csv`, `.tsv`,
`.xlsx`, `.parquet`, `.json`). For files that need extra hints (non-standard
separators, metadata rows to skip), add a `file:` block to the mapping:

```yaml
file:
  skip_rows: 3        # skip 3 rows before the header
  separator: ";"      # use semicolon instead of comma
```

For a guided walkthrough open the
[QuickMapper notebook](3_quickstart-mapping.ipynb).

---

## Available parsers

| Schema | Instrument | Notes |
|---|---|---|
| `characterization/tensile-test/TTO` | Zwick/Roell (testXpert III) | `.TXT` tab-separated, UTF-8 |

To add a parser for a new instrument, see [2_adding-a-parser.md](2_adding-a-parser.md).
