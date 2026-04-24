# Contributing to semantic-transformers

> **Who is this for?** People who want to add a new parser, improve the core
> transformer, or contribute fixes. If you just want to use the library, start
> with [README.md](README.md) instead.

Thank you for contributing! This library is community-maintained.

---

## Ground rules

- **Parsers must follow the `Parser` protocol** defined in `src/semantic_transformers/parser.py`.
- **Each parser lives in its own folder** under `parsers/<domain>/<instrument>/`.
- **Keep parsers focused**: one file format or instrument per parser.
- **Document sample data**: include an example input file with your parser.

---

## Workflow

### 1. Set up your development environment

**Create and activate a virtual environment:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows, use:

```bash
python -m venv .venv
.venv\Scripts\activate
```

**Install the package and dev dependencies:**

```bash
pip install -e ".[dev]"
```

The `[dev]` extra installs `pytest` and `nbmake` for testing.

### 2. Write or improve a parser

Parsers must implement the `Parser` protocol:

```python
from semantic_transformers import Parser, ParseResult

class MyInstrumentParser(Parser):
    def parse(self, file_path: str) -> ParseResult:
        """Read a file and return simplified JSON + DataFrame."""
        # Your parsing logic here
        return ParseResult(
            simplified_json={...},
            dataframe=df
        )
```

See [docs/2_adding-a-parser.md](docs/2_adding-a-parser.md) for a step-by-step guide.

### 3. Run the tests locally

The `scripts/run_notebooks.sh` script is the single entry point for running
both the test suite and the example notebooks:

```bash
./scripts/run_notebooks.sh            # run tests + notebooks
./scripts/run_notebooks.sh --tests    # run only pytest
./scripts/run_notebooks.sh --notebooks  # run only notebooks (via nbmake)
```

You can also call pytest directly for more control:

**Run all tests:**

```bash
pytest tests/
```

**Run only parser tests:**

```bash
pytest tests/parsers/
```

**Run tests for a specific parser:**

```bash
pytest tests/parsers/characterization/tensile-test/testxpert_iii/
```

**Run with verbose output:**

```bash
pytest -v tests/
```

**Run and show print statements:**

```bash
pytest -s tests/
```

### 4. Test your parser against notebooks

The example notebooks in `semantic-schemas` use your parser. To verify they still work:

```bash
# From the semantic-transformers repo root
pip install -e ../semantic-schemas

# Run one of the notebooks
pytest --nbmake ../semantic-schemas/schemas/characterization/tensile-test/TTO/docs/2_tensile_test_csv_workflow.ipynb
```

### 5. Refresh notebook outputs (for documentation)

Notebooks are committed with their output cells so that GitHub renders them as
readable documentation. After changing a parser or the library, re-execute all
notebooks in-place to update the stored outputs before committing:

```bash
./scripts/run_notebooks.sh --refresh
```

Commit the resulting `*.ipynb` changes together with any code changes so that
the rendered output on GitHub stays in sync.

**Tip:** To refresh a single notebook only, pass its path directly:

```bash
./scripts/run_notebooks.sh docs/3_quickstart-mapping.ipynb
```

### 6. Code style and linting

Pre-commit hooks will run automatically before each commit:

```bash
pre-commit run --all-files
```

This checks:

- Python code formatting (black)
- Imports are sorted (isort)
- YAML syntax (yamllint)
- Markdown style (markdownlint)

If a hook fails, fix the issue and stage the changes:

```bash
git add <fixed-files>
git commit
```

### 7. Update CHANGELOG.md

Add an entry under a new section (e.g., `## [Unreleased]`) describing your changes:

```markdown
## [Unreleased]

### Added
- New TestXpertIIIParser variant for binary .zwick files
- Support for multi-line metadata in CSV headers

### Fixed
- Column autodetection now handles whitespace correctly
```

When the package is released, `[Unreleased]` becomes the new version number.

If your change affects schema compatibility (new fields mapped, new schema version
tested), also update the parser's own `CHANGELOG.md` — the compact compatibility
table inside the instrument folder (e.g. `parsers/characterization/tensile_test/testxpert_iii/CHANGELOG.md`).
This is separate from the package-level changelog and records only which parser
version was tested against which schema version.

### 8. Open a pull request

Use the PR template. Link any related issues.

---

## Parser quality criteria

Reviewers will check:

| Criterion | What to look for |
|---|---|
| Correctness | Parser output matches the instrument's specification |
| Coverage | Handles edge cases (missing columns, special characters, etc.) |
| Documentation | Example file + docstrings + section in `docs/` |
| Testing | Unit tests pass; example notebooks run without errors |
| Integration | Works with the `Transformer` pipeline end-to-end |

---

## Versioning

This project follows [Semantic Versioning](https://semver.org/):

- **Patch** (0.0.X): Bug fixes, documentation updates
- **Minor** (0.X.0): New parsers, new optional features
- **Major** (X.0.0): Breaking API changes (changes to `Parser` protocol, removal of public APIs)

Update version in `pyproject.toml` and document it in `CHANGELOG.md`.

---

## Questions?

Open an issue on GitHub or check the documentation in `docs/`.
