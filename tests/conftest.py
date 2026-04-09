"""
Shared fixtures for the semantic-transformers test suite.
"""
from pathlib import Path

import pytest

# Directory containing test data files
DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture(scope="session")
def zwick_txt():
    """Path to the sample Zwick export file bundled with the test suite."""
    return DATA_DIR / "example_tensile_test.TXT"
