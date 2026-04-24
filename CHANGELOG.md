# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-04-24

### Added

- `unit_column: true` now automatically resolves unit strings to QUDT IRIs
  using a built-in lookup table covering common lab units (N, kN, mm, MPa,
  °C, s, %, and ~30 more). Resolved units are stored with `qudt:hasUnit <IRI>`;
  unrecognised strings fall back to `qudt:unit "string"` as before.
- `result.oold_doc["unit_resolutions"]`: dict mapping each file unit string to
  its resolved QUDT IRI, or `null` if no match was found.
- `QuickMapper.run()` prints a resolution summary attributed to the source file
  (e.g. `QuickMapper: unit resolution for 'my_file.TXT': ...`) whenever
  `unit_column: true` fields are present.

### Changed

- `QuickMapper` quickstart notebook: added a "What does a measurement file look
  like?" section; added unit resolution and unrecognised-unit example cells.
- README folder structure updated to reflect the `parser.py` + locale-subfolder
  layout introduced in v0.2.0.
- `docs/1_getting-started.md`: terminology aligned (`skip_rows` comment now
  says "column names row").

## [0.2.0] - 2026-04-24

### Breaking

- `ZwickParser` renamed to `TestXpertIIIParser`; import path changes from
  `semantic_transformers.parsers.characterization.tensile_test.zwick` to
  `semantic_transformers.parsers.characterization.tensile_test.testxpert_iii`.
  Update any existing imports.
- `QuickMapper` metadata field config: the `predicate` key is renamed to
  `property`. Update any existing YAML or dict configs that use `predicate:`.

### Added

- `scripts/run_notebooks.sh` — single entry point to run the test suite,
  validate notebooks, or refresh notebook outputs in-place.

### Changed

- `QuickMapper` quickstart notebook revised: added a conceptual "what does a
  measurement file look like?" section; clarified `skip_rows`,
  `skip_after_header`, and `unit_column`; removed internal variable names
  (`_cwd`, `_candidates`) from the file-path cell; removed em-dash constructions
  throughout.
- `CONTRIBUTING.md` and `docs/1_getting-started.md` updated to reference
  `scripts/run_notebooks.sh` for running tests and refreshing notebooks.

## [0.1.5] - 2026-04-10

### Added (`ZwickParser`)

- `gauge_length` / `gauge_length_unit` — `Messlänge Standardweg` metadata row
  now parsed and emitted as a `pmdco:PMD_0000013` process condition.
- `preload` / `preload_unit` — `Vorkraft` metadata row parsed as a pre-load condition.
- `test_date` — `Datum/Uhrzeit` Excel serial-number date auto-converted to an
  ISO 8601 datetime string via the new `_excel_serial_to_iso()` helper.
- `unit_field_map` parameter — generalises unit-column extraction for any
  metadata label; replaces the hardcoded `strain_rate_label` mechanism.
  `strain_rate_label` is retained for backwards compatibility but is deprecated.

### Added (`TransformResult`)

- `flat_graph` property — returns a `rdflib.Graph` with all triples and namespace
  bindings propagated from the internal `Dataset`. Replaces the repetitive
  `for s, p, o, _ in result.graph.quads(): flat.add(...)` pattern in notebooks.

### Changed

- Namespace bindings in serialised TTL output (`pmdco`, `tto`, `obo`, `qudt`, …)
  are now derived from the schema `@context` and rdflib's built-in namespace
  manager rather than being hard-coded in Python. Adding a prefix to the schema
  YAML is sufficient; no library changes are needed.

### Schema compatibility

- `ZwickParser` is now compatible with `characterization/tensile-test/TTO` **v1.1.0**.
  Remains backwards-compatible with v1.0.0 files (all new fields are optional).

## [0.1.4] - 2026-04-09

### Added

- `base` keyword-only parameter on `Transformer.run()` — pass a custom base
  IRI (e.g. `"https://example.org/"`) to override the schema's `@base` entry
  so all data node IRIs are resolved against your own namespace instead of the
  schema's internal PMDCo test namespace

## [0.1.3] - 2026-04-09

### Fixed

- Moved `parsers/` from repo root into `src/semantic_transformers/parsers/` so
  parsers are included in the wheel and importable after a regular `pip install`
- Renamed `tensile-test/` to `tensile_test/` throughout to produce valid Python
  package identifiers
- Removed the `importlib`-based shim in `src/semantic_transformers/parsers/`
  that only worked with editable installs
- Only `*.json` data files (column mappings) are shipped in the wheel; parser
  `README.md` files are excluded via `package-data`
- Removed duplicate `example_tensile_test.TXT` from the parser folder
  (canonical copy is in `tests/data/`)
- Cleaned up test imports to use the proper package path instead of
  `sys.path` manipulation

## [0.1.2] - 2026-04-09

### Added

- Example tensile test data bundled with ZwickParser
  - `example_tensile_test.TXT` now included in parsers distribution
  - Users can load example files directly from installed package

## [0.1.1] - 2026-04-09

### Added

- Parsers module now included in PyPI distribution
  - Zwick/Roell tensile test parser available via `from semantic_transformers.parsers import ZwickParser`
  - Users can now access sample parsers without requiring GitHub checkout

## [0.1.0] - 2026-04-09

### Added

- Initial public release of semantic-transformers
- Core library components:
  - `Transformer`: Main class for running parsing → JSONata transform → RDF graph pipeline
  - `Parser`: Protocol for implementing custom instrument parsers
  - `ParseResult`: Standard return type for parsers (simplified JSON + DataFrame)
  - `TransformResult`: Pipeline result containing RDF graph and metadata
  - `QuickMapper`: Simple YAML-based mapping system for tabular files without custom parsers

### Parsers

- Characterization parsers:
  - Tensile test parser for Zwick/Roell (testXpert III) instruments
  - Support for both CSV and binary data formats

### Features

- Multi-format file support: CSV, TSV, Excel (.xlsx), Parquet, JSON
- Automatic RDF/Turtle graph generation
- DataFrame export for data inspection
- JSONata transformation templates
- Column mapping to ontology IRIs and units
- Optional dependencies for Excel file handling

### Documentation

- Getting Started Guide (docs/1_getting-started.md)
- Parser Development Guide (docs/2_adding-a-parser.md)
- QuickMapper Quickstart Notebook (docs/3_quickstart-mapping.ipynb)
- Example measurement data (docs/example_measurement.ttl)
- Full API documentation and examples in docstrings

### Tests

- Comprehensive test suite for Transformer, Parser, and QuickMapper classes
- Example data and test fixtures included
- Tests for column mapping and unit conversion

### Dependencies

- pandas
- rdflib
- pyyaml
- jsonata-python
- jsonschema

### Optional Dependencies

- `excel`: openpyxl (for Excel file support)
- `dev`: pytest, nbmake (for development and testing)

### Requirements

- Python 3.10+

## [Unreleased]

### Planned

- Additional instrument parsers (metallography, microscopy, analysis)
- Streaming support for large data files
- Caching and memoization for repeated transformations
- Web API wrapper for parser services
- Expanded unit conversion and validation
- Database output formats (JSON-LD, RDF-JSON)
