# Issue #4 — Add CI: run tests and lint on every pull request

**Issue:** https://github.com/lukekv/StarlingMurmurations/issues/4

## Task breakdown

1. Add `.github/workflows/ci.yml`: ubuntu runner, Python 3.13, pip cache, install pipeline requirements + pytest + ruff, run `ruff check .` then `pytest -q`
2. Add ruff config to `pyproject.toml`: error-level rules only (E9 syntax errors + F pyflakes), exclude the untracked asset/output folders so local runs don't crawl them
3. Fix the two pre-existing F401 findings so the lint gate starts green (unused `CropBbox` import in `file_ops.py`, unused `pytest` import in the new test file)

## Files touched

- `.github/workflows/ci.yml` (new)
- `pyproject.toml` (ruff config added)
- `Texture Library Image Sorter/texture_pipeline/file_ops.py` (remove unused import)
- `tests/test_image_processor.py` (remove unused import)

## Test / verification strategy

- Locally: `ruff check .` clean, `pytest -q` 71 passed
- Real verification happens on the PR itself: the workflow runs on `pull_request`, so the green check on this PR is the proof
- `libgl1` installed on the runner because opencv-python imports libGL

## Notes

- Branched from `feature/issue-3-seed-test-suite` (stacked): CI needs the tests to exist. Merge PR #7 first.
- CI uses Python 3.13 (latest stable on GitHub runners with guaranteed cache support); local dev runs 3.14 — both exercised.
