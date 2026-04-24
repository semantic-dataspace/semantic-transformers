#!/usr/bin/env bash
# run_notebooks.sh — execute and validate notebooks and/or pytest suite
#
# Usage:
#   ./scripts/run_notebooks.sh              # run pytests + all notebooks (test mode)
#   ./scripts/run_notebooks.sh --notebooks  # notebooks only (test mode)
#   ./scripts/run_notebooks.sh --tests      # pytest only
#   ./scripts/run_notebooks.sh --refresh    # re-execute notebooks and save outputs in-place
#   ./scripts/run_notebooks.sh path/to/nb.ipynb  # run a single notebook
#
# Requirements: activate your virtual environment first.
#   python -m venv .venv && source .venv/bin/activate
#   pip install -e ".[dev]"

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── Parse arguments ────────────────────────────────────────────────────────
MODE="all"
SINGLE_NB=""

if [ $# -ge 1 ]; then
    case "$1" in
        --refresh)   MODE="refresh" ;;
        --notebooks) MODE="notebooks" ;;
        --tests)     MODE="tests" ;;
        --*)         echo "Unknown option: $1"; exit 1 ;;
        *)           SINGLE_NB="$1"; MODE="single" ;;
    esac
fi

# ── Collect notebooks ──────────────────────────────────────────────────────
if [ "$MODE" = "single" ]; then
    NOTEBOOKS=("$SINGLE_NB")
elif [ "$MODE" != "tests" ]; then
    mapfile -t NOTEBOOKS < <(
        find docs -name "*.ipynb" \
            ! -path "*/.ipynb_checkpoints/*" \
            | sort
    )
    echo "Found ${#NOTEBOOKS[@]} notebook(s)."
    echo ""
fi

# ── Run ────────────────────────────────────────────────────────────────────
run_tests() {
    echo "── pytest ────────────────────────────────────────────────────────────────"
    pytest tests/
    echo ""
}

run_notebooks_test() {
    echo "── notebooks (nbmake) ────────────────────────────────────────────────────"
    pytest --nbmake "${NOTEBOOKS[@]}"
    echo ""
}

case "$MODE" in
    refresh)
        echo "Mode: refresh (executing and saving outputs in-place)"
        echo ""
        for nb in "${NOTEBOOKS[@]}"; do
            echo "  Refreshing: $nb"
            jupyter nbconvert \
                --to notebook \
                --execute \
                --inplace \
                --ExecutePreprocessor.timeout=300 \
                "$nb"
        done
        echo ""
        echo "Done. Commit the updated *.ipynb files together with any code changes."
        ;;
    single)
        echo "Mode: single notebook (test)"
        echo ""
        pytest --nbmake "${NOTEBOOKS[@]}"
        ;;
    tests)
        run_tests
        ;;
    notebooks)
        run_notebooks_test
        ;;
    all)
        run_tests
        run_notebooks_test
        ;;
esac
