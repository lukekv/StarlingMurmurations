# Pipeline Refactor Plan — Central Library Mode + Confidence Scale

## Objective

Transform the pipeline from a per-run batch processor into a permanent sorting machine. Every run feeds into a single central texture library. A confidence scale asked at startup controls how aggressively the pipeline filters incoming images.

---

## New Runtime Behavior

1. User runs the pipeline with an input directory.
2. The script asks: **"How confident are you that these images are good tileable textures? (1-5)"**
3. Pipeline runs with thresholds calibrated to that confidence level.
4. Good textures are written directly to the central library at `S:\_LIBRARY\_TEXTURES`.
5. Rejects go to `{input_dir}\_recycle_bin\` (stays with the source).
6. Review items go to `S:\_LIBRARY\_TEXTURES\_needs_review\` (central queue).
7. The central database grows over time and prevents re-processing or duplicating anything already in the library.

---

## Confidence Scale

| Level | Meaning | Filters that run |
|---|---|---|
| 5 | Curated — trust everything | AI tagging only. All checks skipped. |
| 4 | 85% sure — light double-check | All checks run at lenient thresholds. |
| 3 | Moderate confidence | All checks run at moderate thresholds. |
| 2 | Low confidence | All checks run at strict thresholds. |
| 1 | Unknown source — full screening | All checks run at maximum strictness. |

### Threshold values per level

| Level | Gradient ratio threshold | Seam diff threshold | Blank stddev threshold |
|---|---|---|---|
| 1 | 1.40 | 15.0 | 12.0 |
| 2 | 1.55 | 18.0 | 10.0 |
| 3 | 1.65 | 22.0 | 9.0 |
| 4 | 1.80 | 28.0 | 7.0 |
| 5 | skipped | skipped | skipped |

The **line-art white pixel threshold** and **minimum resolution** are fixed constants for levels 1-4. They are only skipped at level 5.

---

## Directory Layout After Refactor

```
S:\_LIBRARY\_TEXTURES\           <- central library root (fixed in config)
  pipeline_state.db              <- central database, grows across all runs
  duplicate_report.txt           <- appended each run
  _needs_review\                 <- central review queue (all runs, all sources)
    tileability_failed\
    line_art\
    no_base_map\
    format_review\
    misc\
  Brick\
    Brick_Clay_Running_Bond_Terracotta_01\
      ...
  Wood\
    Wood_Cedar_Planks_Blonde_01\
      ...

{input_dir}\                     <- wherever you point the script
  _recycle_bin\                  <- created at runtime, stays with the source
    duplicates\
    low_resolution\
    blank_images\
    tileability_failed\          <- only if auto_bin_tileability_failures = True
```

---

## Files That Change

### `config.py`

**Add:**
- `library_dir: Path = Path("S:/_LIBRARY/_TEXTURES")` — permanent library root, set once
- `skip_tileability: bool = False` — runtime flag, set by confidence level
- `skip_blank_check: bool = False` — runtime flag
- `skip_line_art_check: bool = False` — runtime flag
- `skip_resolution_check: bool = False` — runtime flag
- `CONFIDENCE_PRESETS: dict` — read-only dict of threshold values keyed by level 1-4

**Change:**
- `output_dir` defaults to `library_dir`
- `db_path` defaults to `library_dir / "pipeline_state.db"`
- `review_dir` defaults to `library_dir / "_needs_review"`
- `recycle_bin_dir` — removed from config default; set at runtime from input path

---

### `main.py`

**Add `_ask_confidence(args)`**
- Checks for `--confidence N` CLI argument first
- If not provided, prints a brief description of each level and prompts the user
- Validates input is 1-5, re-prompts on invalid entry
- Returns integer 1-5

**Add `_apply_confidence(config, level)`**
- For level 5: sets all four skip flags to True on the config object
- For levels 1-4: writes the preset threshold values from `CONFIDENCE_PRESETS` into the config object (`tileability_gradient_ratio_threshold`, `tileability_seam_diff_threshold`, `blank_image_stddev_bin`)
- Always sets `config.recycle_bin_dir = config.input_dir / "_recycle_bin"`

**Change `_parse_args()`**
- Add `--confidence N` as an optional integer argument (1-5)
- Remove `--output` — library path is fixed in config
- Keep `--input`, `--db`, `--recycle-bin`, `--review-dir`, `--dry-run`

**Change `_build_config(args)`**
- Remove output path handling (now in config)
- Call `_apply_confidence()` before returning the config object

---

### `image_processor.py`

Four guard clauses added, no logic changes to any algorithm:

- `_process_one()` — if `config.skip_resolution_check`: skip pre-filter 1
- `_check_blank()` — if `config.skip_blank_check`: return None immediately
- `_check_line_art()` — if `config.skip_line_art_check`: return None immediately
- `_process_one()` — if `config.skip_tileability`: set `is_tileable = True`, skip `_test_tileability()` call

---

### `deduplicator.py`

**Change `_find_duplicate_pairs()`**

Currently the BK-tree is built only from hashes computed in the current batch. This means textures already in the library from a previous run are invisible to the deduplicator.

Fix: after Pass 1 (current batch hashes computed), load all existing hashes from the central database via `db.get_all_phashes()` and insert them into the BK-tree before running queries. New-batch groups that match a library entry are flagged as duplicates and skipped.

Edge case: when a new group is flagged as a duplicate of an existing library entry, it is the new group that loses. Its files are not copied anywhere — the original is already in the library. It is simply marked duplicate in the database and skipped.

---

### `run_pipeline.bat`

- Remove `--output` argument
- Add optional `--confidence N` argument (if omitted, the script prompts interactively)
- Document both usage modes in a comment at the top of the file

---

## What Does Not Change

- Output naming convention: `{Category}_{Material}_{Type}_{Color}_{V##}`
- Variant auto-increment logic in `file_ops.py`
- JSON sidecar format
- AI tagger and prompt
- Database schema
- Scanner logic
- File ops module
- All tileability and pre-filter algorithms (only whether they run changes, not how)

---

## Review Queue Workflow (human step, not code)

When items accumulate in `S:\_LIBRARY\_TEXTURES\_needs_review\`:

1. Open the folder, inspect the flagged images manually.
2. If you decide a flagged texture is actually good, move or copy it to a temporary input folder and re-run the pipeline on it at confidence 5 to tag and add it to the library.
3. Delete anything you confirm is genuinely bad.

The pipeline itself has no mechanism to promote review items automatically. That is intentional — review items exist precisely because the pipeline was not sure about them.

---

## Implementation Order

1. `config.py` — add library_dir, skip flags, confidence presets
2. `main.py` — add confidence prompt, apply_confidence, update arg parsing and path setup
3. `image_processor.py` — add four skip guards
4. `deduplicator.py` — extend BK-tree with existing library hashes
5. `run_pipeline.bat` — update arguments
6. Test on a small known-good batch at confidence 5, then a mixed batch at confidence 3
