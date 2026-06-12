# Issue #3 — Seed pytest test suite for core pipeline logic

**Issue:** https://github.com/lukekv/StarlingMurmurations/issues/3

## Task breakdown

1. Add `pyproject.toml` with pytest config restricting collection to `tests/` (so pytest never crawls the texture library folders)
2. Add `tests/conftest.py` putting `Texture Library Image Sorter/texture_pipeline/` on `sys.path` (modules use flat imports like `from config import Config`)
3. `tests/test_scanner_helpers.py` — characterization tests for the pure helpers: suffix stripping (incl. `_4K`/`_VAR1`/`_LOD0` token handling), map-type identification, file classification, demo detection (token-exact), 3-tier base map identification, dimension scraping, PAT assignment, group ID determinism
4. `tests/test_image_processor.py` — pre-filters and tileability with synthetic numpy images + a stub DatabaseManager: blank detection, line-art detection, product-photo detection, the 3-signal tileability test (pass and fail cases), bypass keywords, crop bbox math

## Files touched

- `pyproject.toml` (new)
- `tests/conftest.py`, `tests/test_scanner_helpers.py`, `tests/test_image_processor.py` (new)

## Test / verification strategy

The tests ARE the deliverable: `pytest -q` from the repo root, 71 tests, all green locally on Python 3.14.5. No test touches the real texture library, SQLite, or the network.

## Notes / gotchas discovered

- A low-frequency sinusoid does NOT pass the tileability test even when mathematically periodic: the Signal 2 high-pass (box blur) has edge-reflection bias that inflates strip residuals (measured 26.7 vs 25.0 threshold). The robust synthetic seamless image is one whose period divides both the image size and the opposite-strip offset (period 8 on a 512px image) so opposite strips are pixel-identical.
- A half-dark/half-light image passes Signals 1 and 2 (the high-pass flattens it) and is caught only by Signal 3 (offset seam) — good evidence Signal 3 covers a real gap.
