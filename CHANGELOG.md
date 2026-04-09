# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
