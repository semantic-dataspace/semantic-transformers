"""
Shared fixtures for the semantic-transformers test suite.
"""
import sys
from pathlib import Path

import pytest

# Repository root (two levels up from this file)
REPO_ROOT = Path(__file__).parent.parent

# Directory containing test data files
DATA_DIR = Path(__file__).parent / "data"

# Add parser directories to sys.path at import time so that pytest can collect
# test modules that import from parsers (collection happens before any fixture runs).
_PARSER_DIRS = [
    REPO_ROOT / "parsers" / "characterization" / "tensile-test" / "zwick",
]
for _d in _PARSER_DIRS:
    _s = str(_d)
    if _s not in sys.path:
        sys.path.insert(0, _s)

# 'parser' is a (deprecated) stdlib module in Python ≤ 3.11. Parser files
# in the parsers/ tree are therefore named after the instrument
# (e.g. zwick_parser.py) rather than simply parser.py to avoid shadowing it.


@pytest.fixture(scope="session")
def zwick_txt():
    """Path to the sample Zwick export file bundled with the test suite."""
    return DATA_DIR / "DX56_D_FZ2_WR00_43.TXT"
