# Getting started

This guide walks you through converting an instrument output file into an RDF
knowledge graph in five minutes.

## What you need

- Python 3.10 or newer
- A measurement file from a supported instrument (see the
  [parser catalogue](#available-parsers) below)
- The `semantic-schemas` repository, for the schema transform and context files

## 1. Set up the environment

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

## 2. Open the notebook for your schema

Each schema in `semantic-schemas` has a notebook in its `docs/` folder. For
tensile test data from a Zwick/Roell machine, open:

```
semantic-schemas/schemas/characterization/tensile-test/TTO/docs/tensile_test_csv_workflow.ipynb
```

```bash
cd semantic-schemas
jupyter lab schemas/characterization/tensile-test/TTO/docs/tensile_test_csv_workflow.ipynb
```

## 3. Point to your file and run

In the notebook, edit **Step 0** (one line):

```python
csv_file = HERE / 'my_tensile_test.TXT'   # ← your file
```

Then run all cells (`Kernel → Restart & Run All`). The notebook will:

1. Read the instrument file and extract metadata + time series
2. Transform the metadata into an OO-LD document using the schema transform
3. Build an RDF graph typed to the relevant ontology classes
4. Validate the graph against SHACL shapes
5. Show the measurement column annotations from the graph
6. Save the graph as `.ttl` and the time series as `.parquet`

## 4. Use the result

The two output files are saved next to the notebook:

| File | What it contains |
|---|---|
| `<stem>.ttl` | RDF graph with test metadata and column descriptors, ready to publish |
| `<stem>.parquet` | Full time-series DataFrame, ready for analysis |

To publish the graph to a triple store, upload the `.ttl` file or use a
SPARQL `INSERT` query. To analyse the time series, load the `.parquet` file
with pandas:

```python
import pandas as pd
df = pd.read_parquet('my_tensile_test.parquet')
df.head()
```

## Available parsers

| Schema | Instrument | Notes |
|---|---|---|
| `characterization/tensile-test/TTO` | Zwick/Roell (testXpert III) | `.TXT` tab-separated export, UTF-8 |

More parsers are planned. See [adding-a-parser.md](adding-a-parser.md) to
contribute one.

## Linking to a specimen (advanced)

A tensile test in the knowledge graph is normally linked to the specimen it
consumed, identified by an IRI already registered in your knowledge graph.
If you have that IRI, pass it to the converter:

```python
result = converter.run(
    'my_tensile_test.TXT',
    specimen_iri = 'https://your-instance.org/specimens/sample-42',
)
```

Without a specimen IRI the graph is valid for exploration but SHACL validation
will flag a missing `has_specified_input`. Add a real IRI before publishing.
