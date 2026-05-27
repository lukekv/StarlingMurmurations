# Texture Library Pipeline — Developer Reference

**For AI assistants:** This document is the authoritative reference for this codebase. Read it in full before making any modifications. It describes what every file does, why every design decision was made, the full pipeline execution order, all configuration parameters, the database schema, the output directory structure, and the history of every significant change made during development. The implementation is stable and tested against a real library of 20,000+ texture images.

---

## Table of Contents

1. [Project Purpose](#1-project-purpose)
2. [Repository Layout](#2-repository-layout)
3. [Pipeline Overview](#3-pipeline-overview)
4. [Dependency Stack](#4-dependency-stack)
5. [How to Run](#5-how-to-run)
6. [File-by-File Reference](#6-file-by-file-reference)
   - [config.py](#61-configpy)
   - [database.py](#62-databasepy)
   - [scanner.py and scanner_helpers.py](#63-scannerpy-and-scanner_helperspy)
   - [deduplicator.py](#64-deduplicatorpy)
   - [image_processor.py](#65-image_processorpy)
   - [ai_tagger.py](#66-ai_taggerpy)
   - [file_ops.py](#67-file_opspy)
   - [main.py](#68-mainpy)
7. [Database Schema](#7-database-schema)
8. [Output Directory Structure](#8-output-directory-structure)
9. [Configuration Parameter Reference](#9-configuration-parameter-reference)
10. [Group Status State Machine](#10-group-status-state-machine)
11. [Design Decisions and Rationale](#11-design-decisions-and-rationale)
12. [Development History](#12-development-history)
13. [Known Edge Cases and How They Are Handled](#13-known-edge-cases-and-how-they-are-handled)
14. [Pending Work and Planned Improvements](#14-pending-work-and-planned-improvements)

---

## 1. Project Purpose

This tool is a professional Python pipeline for processing, cleaning, and organising a chaotic library of over 20,000 PBR (Physically Based Rendering) texture images used in architectural visualisation with Revit and Enscape.

The source library consists of textures collected from multiple vendors (Poliigon, Megascans, AmbientCG, custom scans, and miscellaneous downloads) stored in an unstructured folder hierarchy with no consistent naming conventions, no deduplication, no quality filtering, and no categorisation. The pipeline transforms this into a clean, consistently named, categorised library ready for use in production rendering.

**What the pipeline does, in order:**

1. Recursively scans all source folders and identifies PBR texture groups (a group is one material set — base color, normal, roughness, metallic, displacement, AO, etc. all belonging to the same material)
2. Deduplicates groups using perceptual hashing (pHash with Hamming distance comparison via a BK-tree)
3. Runs quality pre-filters: minimum resolution, blank/solid-colour detection, line-art/technical drawing detection, product photo detection
4. Tests tileability using Sobel gradient analysis and opposite-edge seam comparison
5. Runs geometric unit analysis (Sobel gradient profiles, peak spacing) to compute unit aspect ratios for Brick/Tile/Paver discrimination
6. Sends each group's base map to a local vision AI (Gemma 4 via Ollama) for classification and tagging
7. Writes a clean, consistently named output folder for each accepted group with all PBR maps, a .pat file if present, and a JSON sidecar
8. Routes rejected groups to structured review and recycle bin directories with clear reasons

**What the pipeline never does:**

- Deletes any original source file (all routing is copy-only)
- Re-processes a group that has already been completed (crash-safe via SQLite state)
- Block on GPU inference while CPU workers sit idle (async architecture)

---

## 2. Repository Layout

```
Texture Library Image Sorter/          <- root of the mounted workspace folder
    texture_pipeline/                  <- ALL source code lives here
        config.py                      <- single source of truth for all parameters
        database.py                    <- SQLite state manager (WAL mode, writer thread)
        scanner.py                     <- Phase 1: recursive scan + group registration
        scanner_helpers.py             <- pure helper functions for the scanner
        deduplicator.py                <- Phase 2: pHash duplicate detection (BK-tree)
        image_processor.py             <- Phase 3: pre-filters, crop, tileability, geometry
        ai_tagger.py                   <- Phase 4: local vision AI classification
        file_ops.py                    <- Phase 5: write output files + JSON sidecar
        main.py                        <- orchestrator: runs all stages in order
        requirements.txt               <- pip dependencies

    Texture Library Test/              <- small test library for development runs
    README.md                          <- this file
```

There is also a companion review UI (not inside `texture_pipeline/`) used for manually reviewing edge cases:

```
serve_preview.py                       <- HTTP server for the review web UI
generate_preview.py                    <- generates the static HTML for the review UI
```

These two files are described in section 6 separately from the pipeline.

---

## 3. Pipeline Overview

The pipeline has five numbered stages. Each stage is crash-safe: if the process is killed at any point, re-running it picks up exactly where it left off using the SQLite database as state. Groups in terminal states are never re-processed.

```
Stage 1: SCAN
    Scanner walks input_dir recursively.
    Each directory is examined for image files, .pat files, and .psd/.gif review files.
    Directories containing 3D mesh files (.fbx, .obj, .glb, .gltf, .abc) are flagged
    as mesh assets and routed to _needs_review/mesh_asset/ immediately.
    Files are grouped by base name (suffix-stripped) using token-split matching.
    Every group is registered in SQLite with status=pending.
    Scan-time category hints (e.g., Paver from keyword match) are stamped on the group.
    Review-format files (.psd, .gif) are copied to _needs_review/format_review/.

Stage 2: DEDUPLICATION
    pHash computed concurrently (ThreadPoolExecutor, cpu_workers threads).
    BK-tree Hamming search finds all pairs within phash_hamming_threshold (default 4 bits).
    For each pair, the higher-resolution base map is kept; ties break alphabetically.
    Losers are marked status=duplicate and their base maps copied to _recycle_bin/duplicates/.
    A plain-text duplicate report is written to output_dir/duplicate_report.txt.

Stage 3: IMAGE PROCESSING
    Runs concurrently (ThreadPoolExecutor, cpu_workers threads).
    For each PENDING group with a base map:

    Pre-filter 1 -- Minimum resolution
        If shortest dimension < min_resolution_px (default 512px): BINNED_RESOLUTION
        -> _recycle_bin/low_resolution/

    Pre-filter 2 -- Blank / solid-colour detection
        Grayscale pixel stddev. If < blank_image_stddev_bin (default 2.0): BINNED_BLANK
        -> _recycle_bin/blank_images/

    Pre-filter 3 -- Line-art / technical drawing detection
        Fraction of near-white pixels (>= 240/255). If >= line_art_white_pixel_threshold
        (default 0.60 = 60%): REVIEW_LINE_ART -> _needs_review/line_art/

    Pre-filter 4 -- Product photo detection
        Grayscale stddev across all four edge strips. If max edge strip stddev <
        product_photo_edge_stddev_threshold (default 10.0): BINNED_PRODUCT_PHOTO
        -> _recycle_bin/product_photo/

    Phase 2 -- Square check and center crop
        If width/height ratio deviates from square by more than square_tolerance (2%),
        a centered square crop bounding box is recorded as a normalized fraction of
        original dimensions. This is applied at output time to ALL maps in the group
        proportionally (so a mixed-resolution PBR set crops correctly).

    Phase 3 -- Tileability test (two signals, both must pass)
        Signal 1: Sobel gradient edge-strip spike ratio
            Edge strip gradient mean / interior gradient mean.
            If worst ratio > tileability_gradient_ratio_threshold (1.8): FAIL
        Signal 2: Opposite-edge seam pixel difference
            Mean absolute RGB difference between left/right strips and top/bottom strips.
            If worst seam diff > tileability_seam_diff_threshold (25.0): FAIL
        Files matching tileability_bypass_keywords skip the test entirely (seamless,
        tileable, sky, hdri, grunge, overlay, etc.).
        Failed groups: status=TILEABILITY_FAILED

    Stage 3 geometric pipeline -- Unit aspect ratio
        Runs after tileability on groups that are still proceeding to AI tagging.
        Resizes base map to unit_geometry_max_px (512px max).
        Computes Sobel X and Y gradient magnitudes.
        Row profile = mean absolute Y-gradient per row (horizontal mortar joint peaks).
        Col profile = mean absolute X-gradient per column (vertical joint peaks).
        Custom peak finder: threshold at mean + (peak_k * std), minimum peak separation
        of max_px // 16 to prevent double-counting wide joint edges.
        unit_aspect_ratio = median col spacing / median row spacing (unit width / height).
        If fewer than unit_geometry_min_peaks (2) peaks found in either direction: None.
        Result stored in process_results and written to the database.

Stage 4: AI TAGGING + FILE OPERATIONS (inline, serial)
    Stage 4a: Tileability AI override pass (Option A)
        Groups at TILEABILITY_FAILED are sent to the AI.
        If the AI assigns a category in tileability_override_categories (Art, Sky, Utility,
        Water), the tileability failure is overridden and the group proceeds to file ops.
        All other categories confirm the failure; group stays at TILEABILITY_FAILED.

    Main AI tagging loop (serial -- GPU is the bottleneck):
        For each group at AI_TAGGING status:
        1. Check category_hint in database.
           If set (e.g., "Paver" from scan-time keyword match): synthesize result,
           skip API call, write directly to FILE_OPS. No API cost.
        2. Otherwise: load base map, resize to ai_input_resolution (1024px),
           encode as JPEG base64, send to local Ollama endpoint.
           Prompt includes: all category definitions with disambiguation notes,
           all 50 valid dominant_color values, and (if unit_aspect_ratio is not None)
           a GEOMETRIC CONTEXT note with the ratio and a descriptive label.
        3. Response validated against AITagResult Pydantic schema.
           Retries up to ai_max_retries (3) with exponential backoff on failure.
        4. Secondary non-tileable guard: if AI returns is_tileable=False and the
           category is not in tileability_override_categories, route to
           _needs_review/ai_not_tileable/ instead of the library.
        5. Immediately write output files inline (process_one called in the same loop).
           This means output files appear on disk continuously as tagging progresses.
        6. Groups tagged as Misc go to _needs_review/misc/ not the main library.

Stage 5: FILE OPERATIONS MOP-UP
    Picks up any groups left at FILE_OPS status from a previous interrupted run.
    Ensures crash recovery completes all file writes even if the AI tagging loop
    was interrupted mid-run.
```

---

## 4. Dependency Stack

All dependencies are in `texture_pipeline/requirements.txt`.

| Package | Version | Purpose |
|---|---|---|
| Pillow | >=10.0.0 | Image open, crop, resize, format detection, PNG conversion |
| opencv-python | >=4.8.0 | Sobel gradient computation for tileability and geometry analysis |
| imagehash | >=4.3.1 | pHash computation for duplicate detection |
| rapidfuzz | >=3.0.0 | Fast Levenshtein / partial ratio (fuzzy matching, legacy grouping) |
| pydantic | >=2.0.0 | Strict schema enforcement on AI JSON responses |
| openai | >=1.0.0 | OpenAI-compatible HTTP client for Ollama and LM Studio |

**AI backend:** The pipeline uses any local vision model served via Ollama or LM Studio at an OpenAI-compatible endpoint. The default configured model is `gemma4:e4b`. The model MUST support vision (image input). Text-only models will fail at Stage 4 with a 400 error.

- Ollama default endpoint: `http://localhost:11434/v1`
- LM Studio default endpoint: `http://localhost:1234/v1`

Install dependencies:
```bash
pip install -r requirements.txt --break-system-packages
```

---

## 5. How to Run

```bash
cd texture_pipeline/

# Standard run
python main.py --input /path/to/source/library --output /path/to/output

# Resume a crashed run (DB already exists from previous run)
python main.py --input /path/to/source/library --output /path/to/output --db ./output/pipeline_state.db

# Scan and deduplication only -- no image processing or AI, no files written to output
python main.py --input /path/to/source/library --output /path/to/output --dry-run

# Override review and recycle bin locations
python main.py --input /path --output /path --recycle-bin /path/_bin --review-dir /path/_review
```

The pipeline writes a timestamped log file to `output_dir/pipeline_YYYYMMDD_HHMMSS.log` in addition to stdout. Debug-level messages (gradient ratios, pHash values, suffix stripping traces) go to the log file only. INFO-level messages appear on both.

---

## 6. File-by-File Reference

### 6.1 `config.py`

**Role:** Single source of truth for all pipeline parameters. All other modules import `Config` and read values from it. Nothing is hardcoded in any other file.

`Config` is a Python `dataclass` with typed fields and default values. To change pipeline behaviour, edit this file only.

**Key sections:**

**Paths** — input_dir, output_dir, recycle_bin_dir, review_dir, db_path, duplicate_report_path. All default to relative paths; pass absolute paths for production runs.

**Pre-filter thresholds:**
- `min_resolution_px = 512` — Shortest dimension minimum before binning
- `blank_image_stddev_bin = 2.0` — Grayscale pixel stddev below which an image is considered blank/solid. **Empirically calibrated against the real 20k library.** See section 12 for calibration history.
- `product_photo_edge_stddev_threshold = 10.0` — Max edge strip stddev for product photo detection
- `line_art_white_pixel_threshold = 0.60` — Fraction of near-white pixels for line-art detection

**Tileability:**
- `tileability_edge_strip_px = 8`
- `tileability_gradient_ratio_threshold = 1.8`
- `tileability_seam_diff_threshold = 25.0`
- `tileability_bypass_keywords` — list of keywords that skip tileability entirely
- `auto_bin_tileability_failures = False` — when False, failures go to review; when True, they go to recycle bin

**Deduplication:**
- `phash_hamming_threshold = 4` — Hamming distance at which two images are considered perceptual duplicates
- `max_pixels_for_phash = 100_000_000` — Images larger than this (100 MP) are skipped for pHash

**AI:**
- `ai_base_url = "http://localhost:11434/v1"`
- `ai_model = "gemma4:e4b"` — Change this to switch models
- `ai_input_resolution = 1024` — Base map is resized to this before sending to AI
- `ai_max_retries = 3`
- `ai_retry_base_delay = 2.0` — Doubles on each retry
- `ai_timeout = 120`

**Categories:** The full list of 23 output categories. This list MUST match exactly what is in the AI prompt in `ai_tagger.py`. Adding a category here is not sufficient; the category note in `_CATEGORY_NOTES` must also be added or the model will not know what the category means.

Current categories: Art, Brick, Concrete, Fabric, Glass, Ground, Laminate, Leather, Metal, Misc, Patterns, Paver, Plaster and Stucco, Rammed Earth, Rug, Shingle, Sky, Stone, Tile, Utility, WallCovering, Water, Wood.

**Paver keywords:** `paver_keywords` — list of tokens checked at scan time against the base name of each group. Token-split matching (split on `_`, `-`, whitespace; require exact lowercase match) to avoid false positives like "cobalt" matching "cobble". When a token matches, `category_hint = "Paver"` is written to the database and the AI call is skipped entirely at Stage 4.

**Geometric unit analysis parameters:**
- `unit_geometry_max_px = 512` — Max long-axis pixel count for geometry analysis resize
- `unit_geometry_peak_k = 0.5` — Gradient profile peak threshold multiplier (mean + k * std)
- `unit_geometry_min_peaks = 2` — Minimum peaks in both directions to trust the ratio
- Ratio range constants for Brick, square Tile, subway Tile, and Paver (used to label the ratio in the AI prompt)

**File formats:**
- `supported_image_formats` — .jpg, .jpeg, .png, .tif, .tiff
- `mesh_asset_extensions` — .fbx, .obj, .glb, .gltf, .abc (trigger mesh asset routing)
- `review_formats` — .psd, .gif (copied to _needs_review/format_review/)
- `passthrough_formats` — .pat (copied as-is to output alongside the texture group)
- `convert_tif_to_png = True` — TIF images are converted to PNG at output time

**Base map identification:** Three tiers of logic for identifying which file in a group is the base/albedo color map (see section 6.3 for detail).

---

### 6.2 `database.py`

**Role:** SQLite state manager for the entire pipeline. Provides crash recovery, resumable runs, and serialised writes under concurrent processing.

**Architecture — critical to understand:**

The database uses WAL (Write-Ahead Logging) mode. All writes are serialised through a single dedicated writer thread fed by a `queue.Queue`. Worker threads (image processing, file ops) NEVER write to SQLite directly. They call `_enqueue(sql, params)` which adds the operation to the queue and returns immediately. The writer thread drains the queue one operation at a time. This eliminates all SQLite lock contention under concurrent.futures parallelism.

Short-lived read connections are used for queries. WAL mode allows reads to proceed concurrently with the writer thread without blocking.

**`GroupStatus` enum (all possible statuses):**

| Status | Meaning |
|---|---|
| `pending` | Registered, waiting for image processing |
| `dedup_check` | Being evaluated for duplicates (transitional) |
| `duplicate` | Marked as a perceptual duplicate; will not be processed further |
| `cropping` | Image processing in progress |
| `tileability` | Tileability test in progress |
| `tileability_failed` | Failed tileability; routed to _needs_review/tileability_failed/ |
| `ai_tagging` | Passed tileability; queued for AI |
| `ai_failed` | AI failed after all retries |
| `file_ops` | AI succeeded; file writing in progress |
| `completed` | Fully processed; all output files written |
| `binned_resolution` | Too small; in _recycle_bin/low_resolution/ |
| `binned_blank` | Blank/solid colour; in _recycle_bin/blank_images/ |
| `binned_product_photo` | Product catalog photo; in _recycle_bin/product_photo/ |
| `review_no_base_map` | Could not identify base map; in _needs_review/no_base_map/ |
| `review_format` | .psd or .gif; in _needs_review/format_review/ |
| `review_low_contrast` | (reserved for future use) |
| `review_line_art` | Technical drawing; in _needs_review/line_art/ |
| `review_ai_not_tileable` | AI flagged as not tileable; in _needs_review/ai_not_tileable/ |
| `review_mesh_asset` | 3D mesh asset directory; in _needs_review/mesh_asset/ |

**Terminal statuses** (`_TERMINAL_STATUSES` frozenset): completed, duplicate, file_ops, tileability_failed, review_no_base_map, review_format, review_low_contrast, review_line_art, review_ai_not_tileable, binned_resolution, binned_blank, binned_product_photo, review_mesh_asset.

Groups in a terminal status are NEVER re-entered on a subsequent run. This is the crash-safety guarantee. The check is `is_terminal_state(group_id)` called at the start of `_process_directory()` in the scanner.

**Public API (key methods):**

- `insert_group(group_id, base_name, source_dir, base_map_path, map_count, has_pat, workflow_type)` — INSERT OR IGNORE (safe to call on resume)
- `update_group_status(group_id, status, detail="")` — Primary state machine driver
- `set_group_phash(group_id, phash)` — Store computed pHash hex string
- `mark_group_duplicate(group_id, duplicate_of)` — Mark loser in duplicate pair
- `set_group_ai_output(group_id, ai_data)` — Store AI JSON result
- `set_group_output_path(group_id, output_path)` — Store final output directory
- `set_group_dimensions(group_id, dimensions)` — Store real-world dimensions from filename
- `set_group_category_hint(group_id, hint)` — Store scan-time category override
- `set_group_unit_aspect_ratio(group_id, ratio)` — Store geometric analysis result
- `get_workflow_type_counts()` — Returns dict of {workflow_type: count} for summary
- `shutdown()` — Drain the write queue and join the writer thread (MUST be called at exit)

**Schema migrations:** The schema was originally created without `workflow_type`, `category_hint`, or `unit_aspect_ratio`. These columns are added via `ALTER TABLE ADD COLUMN` inside `_init_schema()` using try/except to silently skip if the column already exists. This pattern allows new columns to be added without breaking existing databases.

---

### 6.3 `scanner.py` and `scanner_helpers.py`

**Role:** Phase 1 — recursive directory scan, PBR group identification, and SQLite registration.

**`scanner.py` — `Scanner` class:**

`scan()` walks `input_dir` recursively using `rglob("*")`. Pipeline-managed output directories (output_dir, recycle_bin_dir, review_dir) and user-configured exclude_dirs are skipped before iterating. This prevents re-scanning previously written output files on a resume run.

`_process_directory(dirpath)` processes a single directory. It classifies every file using `classify_file()`, groups image files by base name, assigns .pat files to groups, and registers each group in the database.

**Mesh asset detection:** Before grouping, the directory is scanned for files with extensions in `mesh_asset_extensions`. If any are found, `has_mesh = True` is set and every group produced from that directory receives status `REVIEW_MESH_ASSET`.

**`PBRGroup` dataclass fields:**
- `group_id` — deterministic SHA-256 hash of (source_dir, base_name). Same inputs always produce the same ID — critical for crash recovery across runs.
- `base_name` — the stem shared by all maps in the group (suffix-stripped)
- `source_dir` — the directory containing all files in the group
- `base_map_path` — Path to the identified base/albedo map (None if not found)
- `image_files` — all image files in the group (including base map)
- `pat_files` — any .pat files assigned to this group
- `demo_files` — files identified as demo/preview renders
- `review_files` — .psd/.gif files (first group in directory only)
- `map_types` — dict mapping file path str -> map type string
- `real_world_dimensions` — scraped from filename (e.g., "39.8x47.9inches")
- `base_map_warnings` — list of warning codes (e.g., "base_map_not_identified")
- `has_mesh_files` — True if the source directory also contains 3D mesh files

**Workflow type classification:** Set at registration time.
- 1 image file: `workflow_type = "Diffuse"` (single legacy map, no PBR companions)
- 2+ image files: `workflow_type = "PBR"`
- 0 image files: `workflow_type = None`

**Scan-time category hint:** After registration, if the group status is PENDING, the base name is token-split and checked against `paver_keywords`. On a match, `set_group_category_hint(group_id, "Paver")` is called. This entirely skips the AI call at Stage 4.

**`scanner_helpers.py` — pure helper functions:**

**Suffix stripping (`strip_map_suffix`):** The core of PBR group identification. Given a filename stem, it returns (base_name, matched_suffix). Three token-stripping passes are applied before matching:
1. `_RES_TOKEN_RE` — strips trailing resolution tokens: `_3K`, `_4K`, `_16K`, etc.
2. `_VARIANT_TOKEN_RE` — strips trailing variant designators: `_VAR1`, `_VAR01`, etc.
3. `_LOD_TOKEN_RE` — strips trailing LOD tokens: `_LOD0`, `_LOD1`, etc.

This three-step stripping was necessary to correctly handle Megascans 3D asset files like `Aset_wood_log_M_phyr5_4K_Normal_LOD0.jpg`, which without LOD stripping would each become their own orphaned group instead of collapsing into one group.

**`SUFFIX_TO_MAP_TYPE`:** Large lookup table mapping lowercased suffixes to standardised map type strings. Covers all common vendor naming conventions for albedo, normal, roughness, metallic, displacement, AO, bump, specular, opacity, emissive, and mask maps.

**Base map identification (`identify_base_map`):** Three-tier logic:
- Tier 1: File has an unambiguous Tier 1 suffix (`_diffuse`, `_albedo`, `_basecolor`, `_col`, `_color`, terminal words like "texture", "diffuse", "albedo")
- Tier 2a: Only one candidate file has NO known map-type suffix
- Tier 2b: Multiple candidates with no suffix; pick the shortest stem
- Tier 3: Cannot identify — returns None (group goes to review_no_base_map)

**Dimension scraping (`scrape_dimensions`):** Extracts real-world dimensions from filenames using regex. Handles formats like "39.8 x 47.9 inches", "600x300mm", "24 x 48" (unit-ambiguous). Returns a dict with width, height, unit, and optional unit_ambiguous flag.

**PAT assignment (`assign_pat_to_groups`):** For directories with a single group, all .pat files go to that group. For multi-group directories, token intersection scoring determines the best match. Ties result in the .pat being logged as unassigned.

**Demo file detection (`is_demo_file`):** Token-split matching against `demo_keywords` (demo, preview, thumb, thumbnail, render, sphere, cube). Token splitting on `[_\-\s]+` is used instead of regex word boundaries because Python treats `_` as a word character, causing `\b` to fail at underscore separators.

---

### 6.4 `deduplicator.py`

**Role:** Phase 2 — perceptual hash (pHash) duplicate detection using a BK-tree.

**Two-pass architecture:**

Pass 1 (concurrent): ThreadPoolExecutor computes `imagehash.phash()` on every group's base map. Images exceeding `max_pixels_for_phash` are skipped to avoid Pillow DecompressionBomb errors on very large renders. Hashes are stored in the database.

Pass 2 (serial): A BK-tree (implemented inline — no extra dependency) is built over all computed hashes. Every hash is queried with `phash_hamming_threshold` (default 4 bits) as the search radius. Duplicate pairs are de-duplicated so each (A, B) pair appears only once.

**Keeper resolution:** For each duplicate pair, the group with the larger base-map pixel area is kept. Ties break alphabetically on base_name. The loser is marked `status=duplicate` in the database. Duplicate routing (copying loser base map to recycle bin) happens in `main._route_duplicates()`.

**BK-tree (`_BKTree`):** A metric tree for Hamming distance queries. `add()` is O(log n) average. `search()` is O(log n) average for small thresholds. Implemented inline to avoid adding `pybktree` as a dependency.

**Output:** A plain-text duplicate report is written to `duplicate_report_path`. It lists every pair with the keeper and loser base names, paths, and Hamming distance.

---

### 6.5 `image_processor.py`

**Role:** Phase 3 — image quality pre-filters, square crop detection, tileability testing, and geometric unit analysis.

**`ProcessResult` dataclass:**
- `group_id` — string
- `crop_bbox` — Optional[CropBbox]: normalized crop bounding box (fractions 0.0–1.0)
- `is_tileable` — bool
- `binned_resolution` — bool
- `base_dims` — Optional[Tuple[int, int]]: original pixel dimensions of base map
- `unit_aspect_ratio` — Optional[float]: computed ratio from geometry analysis (None if signal insufficient)

**`CropBbox` dataclass:** Stores a centered square crop as normalized fractions. `apply_to(width, height)` converts to pixel coordinates. Storing as fractions allows applying the same crop proportionally to all maps in a mixed-resolution PBR set at output time.

**Pre-filter sequence (order matters — each is a fast early exit):**

1. **Minimum resolution** — `min(w, h) < min_resolution_px`. Fast pixel dimension check. Status: `BINNED_RESOLUTION`.

2. **Blank/solid-colour** (`_check_blank`) — Converts to grayscale, computes `numpy` stddev. Status: `BINNED_BLANK`. Threshold: `blank_image_stddev_bin = 2.0`. This value was empirically calibrated (see section 12).

3. **Line-art detection** (`_check_line_art`) — Fraction of pixels with value >= 240/255. Status: `REVIEW_LINE_ART`. Threshold: 60% near-white pixels. Routes to review, not recycle bin, because some borderline cases (white plaster, snow) need human confirmation.

4. **Product photo detection** (`_check_product_photo`) — Grayscale stddev across all four edge strips (top, bottom, left, right), each 8px wide. The MAX of the four stddevs is compared to `product_photo_edge_stddev_threshold = 10.0`. If the maximum is below the threshold, all four strips are near-uniform, indicating a clean studio background with an isolated object. Status: `BINNED_PRODUCT_PHOTO`.

**Tileability test (`_test_tileability`):** Two signals, both must pass:

Signal 1 — Interior-calibrated gradient spike: Sobel magnitude at each edge strip is divided by the interior mean (edge strips excluded from the interior baseline). This self-calibrating approach prevents directional textures from being incorrectly flagged. Threshold: 1.8.

Signal 2 — Seam pixel difference: Mean absolute RGB difference between left/right strip pair and top/bottom strip pair. A seamless texture wraps cleanly, so opposite edges should be nearly identical. Threshold: 25.0 mean absolute difference (0-255 scale).

Bypass: files matching `tileability_bypass_keywords` skip the test and are treated as tileable.

**Geometric unit analysis (`_analyze_unit_geometry`):** Supplementary signal for Brick/Tile/Paver discrimination. Only runs on groups that passed tileability and are proceeding to AI tagging. Failures are non-fatal and silently return None.

Algorithm:
1. Open base map as grayscale, resize to `unit_geometry_max_px` (512px) on the long axis
2. Compute Sobel X and Y gradient magnitudes
3. Row profile = `abs(sobel_y).mean(axis=1)` — peaks at horizontal mortar/grout joints
4. Col profile = `abs(sobel_x).mean(axis=0)` — peaks at vertical joints
5. Custom peak finder (no scipy): threshold at `mean + peak_k * std`; local maxima with minimum separation of `max_px // 16` pixels; median of inter-peak spacings
6. `unit_aspect_ratio = col_spacing / row_spacing` (horizontal unit width / vertical height)
7. If fewer than `unit_geometry_min_peaks` (2) peaks in either direction: return None

Expected discriminator values:
- Standard running-bond brick (wall): ratio 2.5–3.5
- Brick paver (square-ish, laid flat): ratio 1.0–1.8
- Square floor tile: ratio 0.85–1.15
- Subway tile (2:1 format): ratio 1.6–2.4

---

### 6.6 `ai_tagger.py`

**Role:** Phase 4 — AI-based texture classification and tagging using a local vision model.

**Architecture:** Single-threaded. GPU inference via Ollama is the bottleneck. Sending concurrent requests would not improve throughput and would complicate error handling. The main pipeline calls `tag_group()` in a serial loop.

**`AITagResult` Pydantic model** — the schema the AI must return:
- `category` — must be one of the exact strings in `config.categories`
- `material` — base substance, 1-2 words, title case (e.g., "Cedar", "Concrete")
- `material_type` — form/finish/application, 1-2 words, title case (e.g., "Planks", "Polished")
- `dominant_color` — must be one of 50 valid architectural color names
- `tags` — array of 3-8 lowercase underscore tags
- `is_tileable` — bool
- `real_world_size_estimate` — string or "unknown"

Pydantic validators handle common failure modes: case normalisation for colors, fallback values for missing fields, `material_name` backward compatibility.

**System prompt** — hardcoded in `_SYSTEM_PROMPT`. Seven critical rules:
1. Category reflects material identity, not application location (brick is always Brick even on walls)
2. Misc is not a quality bin; reserve for technical drawings and non-photographic content
3. Art is only for deliberate decorative artwork; not a catch-all for unusual images
4. Siding/cladding products always get the tag "siding" regardless of material category
5. TILE HARD OVERRIDE: any visible grout lines = Tile, unconditionally
6. Utility is for imperfection overlays and weathering masks, not base material textures
7. BRICK vs TILE vs PAVER discrimination: Brick = elongated wall masonry, horizontal coursing; Tile = interior ceramic with thin grout lines; Paver = exterior units viewed from above

**`_CATEGORY_NOTES` dict** — per-category disambiguation text injected into the user message. Every category in `config.categories` should have an entry here. Key entries for Brick/Tile/Paver disambiguation:

- **Brick:** "clay, concrete, or calcium-silicate masonry units with visible mortar joints. Units are ELONGATED -- typically 2.5 to 3.5 times wider than tall -- laid in coursed horizontal rows on a WALL." Explicitly warns against confusing with Paver (square units viewed from above) and Tile (rougher, deeper mortar joints vs fine grout).

- **Tile:** "ceramic, porcelain, encaustic, mosaic, zellige, subway, and terracotta tile units for INTERIOR wall and floor applications." Restates the HARD OVERRIDE rule. Distinguishes from Brick (thinner uniform grout joints, smoother factory face) and from Paver (interior vs exterior, fine vs coarse joints).

- **Paver:** "exterior hard-paving units installed in the horizontal ground plane: clay or concrete pavers, natural stone setts, cobblestones, granite cubes, flagstones, bluestone, brick pavers laid flat, and courtyard or plaza paving." Key distinguisher: viewed from ABOVE (top-down), joints run in multiple directions without clear horizontal coursing.

**Geometric context injection:** When `unit_aspect_ratio` is not None, a GEOMETRIC CONTEXT block is appended to the user message:
```
GEOMETRIC CONTEXT (measured from image gradient analysis):
  Detected unit aspect ratio (width / height): {ratio:.2f} -- {label}.
  Use this as supporting evidence alongside the visual content.
  It is not authoritative -- weigh it against what you can see.
```
The label maps the numeric ratio to a descriptive string using the config thresholds (e.g., "elongated (consistent with wall brick)").

**`tag_group(group, unit_aspect_ratio=None)` flow:**
1. Update status to AI_TAGGING
2. Prepare image: open, resize to ai_input_resolution, encode as JPEG base64
3. Call `_retry_with_backoff()` with exponential backoff
4. On success: store AI output in DB, update status to FILE_OPS
5. On failure after all retries: update status to AI_FAILED, return None

**Color validation:** 50 architectural color names are defined in `_VALID_COLORS`. The Pydantic validator normalises the model's response (lowercase, strip spaces/underscores) and looks up the canonical form. Unknown colors default to "Grey" with a warning.

---

### 6.7 `file_ops.py`

**Role:** Phase 5 — write all final output files for completed groups.

**Output naming convention:**
```
[Category]_[Material]_[MaterialType]_[DominantColor]_[V##].[ext]
```
- The base/albedo map gets no map code suffix — it is the default file Enscape loads
- All non-base PBR maps get standardised uppercase codes: NORM, ROUGH, METAL, DISP, AO, OPAC, EMIS
- [V##] is a zero-padded variant number that auto-increments by scanning existing subdirectories

Example for a full PBR set:
```
Wood_Cedar_Planks_Blonde_01.png           <- base color (no suffix)
Wood_Cedar_Planks_Blonde_01_NORM.png
Wood_Cedar_Planks_Blonde_01_ROUGH.png
Wood_Cedar_Planks_Blonde_01_DISP.png
Wood_Cedar_Planks_Blonde_01.json          <- JSON sidecar
Wood_Cedar_Planks_Blonde_01.pat           <- Revit pattern file
```

**`_title_slug(text)`** — Converts AI-returned text to TitleCase underscore slug. "board formed" → "Board_Formed". "PLANKS" → "Planks".

**Variant detection (`_next_variant`):** Scans the category subdirectory for existing folders matching `{base_slug}_(\d+)`. Returns `max + 1`, or 1 if none exist. Thread-safe because file_ops workers operate on different groups.

**Image writing (`_write_image`):** If a crop bounding box exists, opens the image with Pillow and crops it. If the source is a TIF and `convert_tif_to_png` is True, saves as PNG. Otherwise uses `shutil.copy2` for maximum speed (no re-encoding).

**JSON sidecar (`_write_sidecar`):** Written alongside every group. Fields:
- `texture_name`, `material`, `material_type`, `dominant_color`, `category`
- `tags` — from AI output
- `gradient_test_passed` — result of the Stage 3 tileability test
- `ai_is_tileable` — what the AI returned for is_tileable
- `real_world_size_estimate` — AI estimate (e.g., "1m x 1m")
- `real_world_dimensions` — scraped from filename by scanner (dict with width, height, unit)
- `maps` — sorted list of map codes written (e.g., ["AO", "DISP", "NORM", "ROUGH", "base"])
- `has_pat` — bool
- `source_files` — dict mapping map_type -> original source path
- `processed_date` — ISO 8601 UTC timestamp

**Misc routing:** Groups tagged as "Misc" go to `review_dir/misc/` instead of `output_dir/`. This keeps the organised library free of technical drawings, site plans, and other non-texture content that passed all quality filters but was correctly identified by the AI as not a material texture.

---

### 6.8 `main.py`

**Role:** Pipeline orchestrator. Imports all other modules and runs them in sequence. Handles CLI parsing, logging setup, routing helpers, and the end-of-run summary.

**`_safe_dir_name(name)`** — Strips illegal filesystem characters from names before using them as directory names.

**`_copy_files(files, dst_dir)`** — Safe bulk copy. Creates parent directory and copies each file only if it exists.

**Routing functions** — Each routing function iterates all groups and copies files for groups matching a specific terminal status. They are called after each stage completes:
- `_route_duplicates` → `_recycle_bin/duplicates/`
- `_route_binned` → `_recycle_bin/low_resolution/`
- `_route_blank_images` → `_recycle_bin/blank_images/`
- `_route_product_photo` → `_recycle_bin/product_photo/`
- `_route_line_art` → `_needs_review/line_art/`
- `_route_tileability_failures` → `_needs_review/tileability_failed/` or `_recycle_bin/tileability_failed/`
- `_route_ai_not_tileable` → `_needs_review/ai_not_tileable/`
- `_route_no_base_map` → `_needs_review/no_base_map/`
- `_route_mesh_asset` → `_needs_review/mesh_asset/`
- `_route_review_files` → `_needs_review/format_review/`
- `_route_misc` → (log only; actual writes done inline by file_ops)

**`_tileability_ai_override`** — Stage 4a. Runs the AI on all tileability-failed groups to check whether their failure was because the image is inherently non-tileable (Art, Sky, Utility, Water) rather than because of an actual seam defect. Groups confirmed as Art/Sky/Utility/Water are immediately processed via `file_ops.process_one()`. All others are reset to `TILEABILITY_FAILED` for normal routing.

**Stage 4 main loop — category_hint bypass:**
```python
group_row = db.get_group(group.group_id)
category_hint = group_row["category_hint"] if group_row else None

if category_hint:
    # Synthesize result, skip API call
    result = {
        "category": category_hint,
        "material": "Unknown", "material_type": "Unknown",
        "dominant_color": "Grey", "tags": [category_hint.lower()],
        "is_tileable": True, "real_world_size_estimate": "unknown",
    }
    db.set_group_ai_output(group.group_id, result)
    db.update_group_status(group.group_id, GroupStatus.FILE_OPS)
else:
    proc = process_results.get(group.group_id)
    unit_aspect_ratio = proc.unit_aspect_ratio if proc else None
    result = tagger.tag_group(group, unit_aspect_ratio=unit_aspect_ratio)
```

**`_print_summary`** — Logs a summary table of all statuses and counts, plus a Workflow Classification breakdown showing how many groups are PBR vs Diffuse.

**`_mop_up_file_ops`** — Stage 5. Rebuilds PBRGroup objects from the database for any groups at FILE_OPS status (left incomplete by a previous crashed run) and processes their file ops.

---

## 7. Database Schema

```sql
CREATE TABLE groups (
    group_id                TEXT PRIMARY KEY,
    base_name               TEXT NOT NULL,
    source_dir              TEXT NOT NULL,
    base_map_path           TEXT,
    map_count               INTEGER DEFAULT 0,
    has_pat                 INTEGER DEFAULT 0,
    phash                   TEXT,
    status                  TEXT NOT NULL DEFAULT 'pending',
    status_detail           TEXT,
    is_duplicate            INTEGER DEFAULT 0,
    duplicate_of            TEXT,
    output_path             TEXT,
    ai_output               TEXT,             -- JSON blob from AI tagger
    real_world_dimensions   TEXT,             -- JSON blob from scrape_dimensions()
    processed_date          TEXT,             -- ISO 8601 UTC
    workflow_type           TEXT,             -- "PBR" or "Diffuse" or NULL
    category_hint           TEXT,             -- scan-time category override (e.g., "Paver")
    unit_aspect_ratio       REAL              -- computed by _analyze_unit_geometry()
);

CREATE TABLE files (
    file_id         TEXT PRIMARY KEY,
    group_id        TEXT NOT NULL,
    source_path     TEXT NOT NULL UNIQUE,
    map_type        TEXT,
    is_base_map     INTEGER DEFAULT 0,
    is_pat          INTEGER DEFAULT 0,
    is_demo         INTEGER DEFAULT 0,
    original_format TEXT,
    width           INTEGER,
    height          INTEGER,
    output_path     TEXT,
    status          TEXT DEFAULT 'pending',
    FOREIGN KEY (group_id) REFERENCES groups(group_id)
);

CREATE INDEX idx_files_group ON files(group_id);
CREATE INDEX idx_groups_status ON groups(status);
CREATE INDEX idx_groups_phash ON groups(phash);
```

**Note on migrations:** The columns `workflow_type`, `category_hint`, and `unit_aspect_ratio` did not exist in the original schema. They are added via `ALTER TABLE ADD COLUMN` inside `_init_schema()` using a try/except that silently continues if the column already exists. This makes the migration safe to run against any existing database.

**`group_id` generation:** SHA-256 hash of the lowercased string `"{source_dir}::{base_name}"`, truncated to 16 hex characters. Deterministic — same group always gets the same ID across runs, which is what makes crash recovery work.

**`file_id` generation:** SHA-256 hash of the source path string, truncated to 16 hex characters.

---

## 8. Output Directory Structure

```
output_dir/
    pipeline_YYYYMMDD_HHMMSS.log
    duplicate_report.txt
    pipeline_state.db

    Brick/
        Brick_Clay_Running_Bond_Terracotta_01/
            Brick_Clay_Running_Bond_Terracotta_01.jpg        <- base color
            Brick_Clay_Running_Bond_Terracotta_01_NORM.jpg
            Brick_Clay_Running_Bond_Terracotta_01_ROUGH.jpg
            Brick_Clay_Running_Bond_Terracotta_01_DISP.jpg
            Brick_Clay_Running_Bond_Terracotta_01_AO.jpg
            Brick_Clay_Running_Bond_Terracotta_01.json
            Brick_Clay_Running_Bond_Terracotta_01.pat        <- if .pat file present

    Wood/
        Wood_Cedar_Planks_Blonde_01/
            ...

    Tile/
        ...

    Paver/
        ...

    _recycle_bin/
        duplicates/
            [base map files of duplicate groups]
        low_resolution/
            [base map files of sub-512px groups]
        blank_images/
            [base_name]/
                [all files in the group]
        product_photo/
            [base_name]/
                [all files in the group]

    _needs_review/
        tileability_failed/
            [base_name]/
                [all files in the group]
        line_art/
            [base_name]/
                [all files in the group]
        ai_not_tileable/
            [base_name]/
                [all files in the group]
        no_base_map/
            [base_name]/
                [all files in the group]
        format_review/
            [.psd and .gif files]
        mesh_asset/
            [base_name]/
                [all files in the group, including mesh files]
        misc/
            [AI-tagged as Misc: drawings, site plans, etc.]
```

---

## 9. Configuration Parameter Reference

All parameters are in `config.py` in the `Config` dataclass. Defaults reflect empirical calibration against the real 20k+ texture library.

| Parameter | Default | Description |
|---|---|---|
| `min_resolution_px` | 512 | Shortest side minimum pixel count |
| `blank_image_stddev_bin` | 2.0 | Grayscale stddev below which image is binned as blank |
| `product_photo_edge_stddev_threshold` | 10.0 | Max edge strip stddev for product photo detection |
| `line_art_white_pixel_threshold` | 0.60 | Near-white pixel fraction for line-art detection |
| `square_tolerance` | 0.02 | Max aspect ratio deviation before center crop |
| `tileability_edge_strip_px` | 8 | Width of edge strips for tileability signals |
| `tileability_gradient_ratio_threshold` | 1.8 | Edge/interior gradient ratio threshold |
| `tileability_seam_diff_threshold` | 25.0 | Opposite-edge mean absolute RGB difference threshold |
| `auto_bin_tileability_failures` | False | Route failures to recycle bin instead of review |
| `phash_hamming_threshold` | 4 | Hamming distance for perceptual duplicate detection |
| `max_pixels_for_phash` | 100,000,000 | Max pixel count before skipping pHash |
| `ai_input_resolution` | 1024 | Base map max dimension before sending to AI |
| `ai_max_retries` | 3 | AI API retry count with exponential backoff |
| `ai_retry_base_delay` | 2.0 | Seconds before first retry (doubles each time) |
| `ai_timeout` | 120 | API call timeout in seconds |
| `cpu_workers` | 6 | ThreadPoolExecutor worker count for image processing |
| `file_ops_workers` | 4 | ThreadPoolExecutor worker count for file writing |
| `convert_tif_to_png` | True | Convert TIF outputs to PNG |
| `fuzzy_match_threshold` | 85 | RapidFuzz threshold (legacy; preserved in config) |
| `unit_geometry_max_px` | 512 | Max long-axis px for geometry analysis resize |
| `unit_geometry_peak_k` | 0.5 | Gradient peak threshold multiplier (mean + k*std) |
| `unit_geometry_min_peaks` | 2 | Minimum peaks in both directions to trust ratio |
| `unit_geometry_brick_ratio_min/max` | 2.3 / 3.8 | Ratio range for standard wall brick |
| `unit_geometry_tile_square_ratio_min/max` | 0.85 / 1.15 | Ratio range for square tile |
| `unit_geometry_tile_subway_ratio_min/max` | 1.6 / 2.4 | Ratio range for subway tile |
| `unit_geometry_paver_ratio_min/max` | 0.85 / 1.9 | Ratio range for paver |

---

## 10. Group Status State Machine

```
[registered] --> pending
    |
    +--(mesh files detected)--> review_mesh_asset [TERMINAL]
    +--(no base map identified)--> review_no_base_map [TERMINAL]
    |
    v
pending
    |
    +--(pHash computed)--> [internal dedup state]
    +--(marked as duplicate)--> duplicate [TERMINAL]
    |
    v
cropping / tileability
    |
    +--(below min resolution)--> binned_resolution [TERMINAL]
    +--(blank/solid colour)--> binned_blank [TERMINAL]
    +--(line art detected)--> review_line_art [TERMINAL]
    +--(product photo detected)--> binned_product_photo [TERMINAL]
    +--(tileability fail)--> tileability_failed
    |                               |
    |                               +--(AI override: Art/Sky/Util/Water)--> ai_tagging
    |                               +--(AI confirms failure / AI fails)--> tileability_failed [TERMINAL]
    |
    v
ai_tagging
    |
    +--(category_hint set; AI skipped)--> file_ops
    +--(AI succeeds)--> file_ops
    +--(AI fails all retries)--> ai_failed [NOT terminal; can retry on resume]
    +--(AI returns is_tileable=False, non-exempt category)--> review_ai_not_tileable [TERMINAL]
    |
    v
file_ops
    |
    v
completed [TERMINAL]
```

---

## 11. Design Decisions and Rationale

**Why SQLite with a writer thread?**
The pipeline uses concurrent CPU workers for image processing and file operations. SQLite's default journal mode serialises writes at the OS level and throws "database is locked" errors under concurrent access. WAL mode allows concurrent reads but still requires serialised writes. A single writer thread fed by a queue eliminates all lock contention. Worker threads add operations to the queue and return immediately to their CPU work — the write latency is absorbed asynchronously.

**Why is group_id a deterministic hash rather than a UUID?**
A UUID-based ID would change between runs, making it impossible to look up existing database records for a group encountered during a resume run. The SHA-256 hash of (source_dir, base_name) is stable across runs as long as the source directory and group name do not change. This is the key that makes crash recovery work: the scanner re-encounters the same group on a resume run and `is_terminal_state(group_id)` returns True, skipping it cleanly.

**Why is file modification via bash/Python string manipulation rather than the Edit tool?**
A recurring problem during development was that the Cowork Edit tool truncated Python files when the replacement string was long. All file modifications in this project are made using Python string replacement scripts executed via bash (`python3 - << 'EOF'`), followed by `python3 -c "import ast; ast.parse(open(f).read())"` to verify syntax. This approach is immune to truncation.

**Why is the tileability test interior-calibrated?**
The original design compared edge strip gradient mean to the whole-image gradient mean. This caused directional textures (parquet, corrugated metal) with strong interior features to incorrectly raise the denominator, making edge spikes look proportionally small. Using only the interior region (edge strips excluded) as the denominator means the threshold is honestly calibrated against what the texture looks like in the middle, not at the edges.

**Why does the seam difference test compare opposite edges rather than adjacent edges?**
A seamless tileable texture wraps: the right edge becomes the left edge when tiled. So the left strip and right strip should be nearly identical pixel values. Non-seamless images (renders, photos, art) have no spatial relationship between opposite edges and score high on this test. Adjacent edge comparison would be meaningless for this purpose.

**Why is blank detection at 2.0 stddev and not 8.0?**
The original threshold of 8.0 was set based on the assumption that even subtle textures have stddev of 15+. Empirical measurement of 426 images in the recycle bin showed real textures at stddev as low as 2.0 (light plaster at 4.16, pale parquet at 5.34, polished marble at 6.07). The natural break in the data between genuinely blank images (0–1.5 stddev) and real-texture-with-very-subtle-detail (2.0+) is at 2.0. The threshold was lowered from 8.0 to 2.0.

**Why is AI tagging serial despite having GPU hardware?**
Ollama serialises inference internally regardless of how many concurrent requests are sent. Sending parallel requests would queue them internally at Ollama and provide no throughput benefit while complicating error handling. The CPU-intensive pre-filters (tileability, geometry analysis) run concurrently on a ThreadPoolExecutor; the GPU-intensive AI step runs serially after all CPU work is done.

**Why is `_analyze_unit_geometry()` non-fatal?**
The geometric signal is supplementary context for the AI, not a decision gate. A failure to detect peaks — because the texture has strong surface noise that obscures joint lines, or because the geometry does not have a regular repeating unit — should not block the group from being processed. The AI can still classify it correctly based on visual inspection alone.

**Why is the Paver category hint set at scan time rather than after geometry analysis?**
Keyword-based detection (paver, cobblestone, sett, flagstone, etc.) is a near-certain signal from the vendor who named the file. It is cheap, reliable, and available immediately at scan time before any image processing happens. Geometry analysis is a weaker probabilistic signal. For files that are clearly labelled as pavers by name, skipping the AI call entirely is both faster and more accurate than letting the AI see a top-down cobblestone texture and possibly misclassify it as Stone.

---

## 12. Development History

This section documents every significant change made during the development of the pipeline, in chronological order, including the reasoning and any problems encountered.

### Megascans LOD suffix fix

**Problem:** Megascans 3D asset files use the naming convention `Aset_wood_log_M_phyr5_4K_Normal_LOD0.jpg`. The `_LOD0` suffix was not in the suffix stripping table. After resolution stripping (`_4K` is not at the very end when `_LOD0` follows), no known map-type suffix matched. Each LOD variant (`_LOD0`, `_LOD1`, etc.) became its own orphaned group with a single file, all going to `_needs_review/no_base_map/`.

**Fix:** Added `_LOD_TOKEN_RE = re.compile(r'[_-]LOD\d+$', re.IGNORECASE)` to `scanner_helpers.py`. Applied in `strip_map_suffix()` as the third step after resolution and variant stripping. The stripping order is: `_3K/_4K` → `_VAR01` → `_LOD0` → map type suffix. After the fix, all 12 files in a typical Megascans folder collapse into one group correctly.

### Mesh asset detection

**Problem:** Megascans and similar vendors bundle 3D mesh files (.fbx, .obj, .glb) in the same directory as PBR texture maps. These texture maps are UV-bound to a specific mesh geometry and are not standalone tileable materials — they cannot be used as general library textures without the mesh.

**Fix:** Before grouping in `_process_directory()`, scan for files with extensions in `mesh_asset_extensions`. If found, stamp `has_mesh_files = True` on every group from that directory and immediately register them as `REVIEW_MESH_ASSET`. The group is copied to `_needs_review/mesh_asset/`. Added `mesh_asset_extensions` to `config.py` and `REVIEW_MESH_ASSET` to `GroupStatus` and `_TERMINAL_STATUSES`.

### Workflow type classification (Diffuse vs PBR)

**Requirement:** Single-map textures (one image file per group, no normal/roughness/etc.) are legacy "diffuse-only" textures from pre-PBR workflows. They should be tagged differently from multi-map PBR sets so the reviewer UI can distinguish them.

**Implementation:** At registration time, check `len(group.image_files)`. 1 file = `workflow_type = "Diffuse"`, 2+ files = `workflow_type = "PBR"`. Stored in the database via an ALTER TABLE migration. `get_workflow_type_counts()` added to DatabaseManager. `_print_summary()` in main.py shows the breakdown.

### Blank image threshold calibration

**Problem:** The initial `blank_image_stddev_bin` threshold of 8.0 was incorrectly binning real texture maps including light parquet (stddev 5.34), smooth white plaster (stddev 4.16), and polished marble (stddev 6.07). A full scan of 426 images in the recycle bin's blank_images directory confirmed this was a systemic problem.

**Root cause:** The threshold was set based on an untested assumption that all real textures have stddev 15+. Real library data shows that very subtle, light-coloured materials can have stddev as low as 2.0. Genuinely blank or solid-colour images (flat metalness maps, solid paint exports, uniform AO maps) have stddev 0–1.5.

**Fix:** Threshold lowered from 8.0 to 2.0. The empirical break in the data at 2.0 cleanly separates objectively unusable images from legitimately subtle textures.

### Multi-select in the review UI

**Requirement:** The review UI (`serve_preview.py`, `generate_preview.py`) previously allowed moving or deleting only one texture at a time. The reviewer needed to be able to select multiple textures and bulk-move or bulk-delete them.

**Implementation:**
- Added a `Set` of composite keys (`folder_path + "|" + source_file`) that survives grid re-renders
- Added checkboxes to each card that toggle membership in the selection set
- Added a floating selection bar at the bottom of the screen with count, "Select all visible", "Move to...", "Delete selected", "Clear" buttons
- `_itemPayload(item)` determines whether an item is a raw review item or a misc/library item, routing to the correct server endpoint
- New server endpoints: `/api/bulk-accept` (calls `accept_misc` or `accept_raw` for each item) and `/api/bulk-delete` (calls `delete_item` for each item)
- Partial failures reported per-item in the response rather than failing the whole batch

### Brick / Tile / Paver discrimination pipeline

**Problem:** The AI (Gemma 4) had consistent difficulty distinguishing between Brick, Tile, and Paver. White brick textures were being classified as Tile. Red terracotta pavers were being classified as Brick. The visual signal alone was insufficient.

**Analysis:** The problem has three dimensions: color (overlapping palettes), shape (similar rectangular units), and viewing angle (wall-mounted brick vs floor-laid paver). Color filtering was rejected as unreliable. The key discriminators are: (1) unit aspect ratio (Brick is elongated 2.5-3.5x, Paver is square or near-square), (2) viewing angle (Brick = frontal/vertical wall, Paver = top-down ground plane), and (3) joint character (Brick mortar = deep, rough; Tile grout = thin, uniform; Paver joints = medium, irregular).

**Implemented solution (six-file change):**

1. **`config.py`:** Added "Paver" to categories list, `paver_keywords` list (paver, pavers, paving, cobble, cobblestone, sett, flagstone, bluestone, courtyard, plaza), and geometric detection parameters (unit_geometry_* fields).

2. **`database.py`:** Added `category_hint TEXT` and `unit_aspect_ratio REAL` columns via ALTER TABLE migrations. Added `set_group_category_hint()` and `set_group_unit_aspect_ratio()` to the public API.

3. **`scanner.py`:** At registration time, token-split base_name against paver_keywords. On match, call `set_group_category_hint(group_id, "Paver")`. Only applied to PENDING groups (not mesh-asset or no-base-map groups).

4. **`image_processor.py`:** Added `unit_aspect_ratio: Optional[float]` to `ProcessResult`. Added `_analyze_unit_geometry()` method implementing the Sobel gradient profile peak detection algorithm. Called after tileability on tileable groups. Result written to DB and returned in ProcessResult.

5. **`ai_tagger.py`:** Rewrote Brick, Tile, and Paver category notes with explicit distinguishing criteria. Added system prompt Rule 7 (BRICK vs TILE vs PAVER discrimination). Added `unit_aspect_ratio` parameter to `tag_group()`, `_build_messages()`, and `_retry_with_backoff()`. When ratio is available, a GEOMETRIC CONTEXT block is injected into the user message with the numeric value and a descriptive label.

6. **`main.py`:** Stage 4 loop checks `category_hint` before calling the AI. Groups with a hint synthesize a minimal result and skip the API call entirely. Normal AI path now passes `unit_aspect_ratio` from ProcessResult into `tag_group()`. Fixed a pre-existing field name bug: the secondary non-tileable guard was checking `result.get("ai_is_tileable")` but the Pydantic field is `is_tileable`.

---

## 13. Known Edge Cases and How They Are Handled

**Files with no base map identified:**
When `identify_base_map()` returns None and there are image files in the group, the group is registered as `REVIEW_NO_BASE_MAP` and copied to `_needs_review/no_base_map/`. This prevents the pipeline from attempting to run image processing or AI tagging on a group with an ambiguous base file.

**Mixed-resolution PBR sets (e.g., 4K base + 2K normal):**
The crop bounding box is stored as normalised fractions (0.0–1.0) of the original dimensions. At file_ops time, `crop_bbox.apply_to(w, h)` is called independently for each map using that map's actual pixel dimensions. This correctly handles sets where the normal or roughness map is at a lower resolution than the base.

**Directories with multiple PBR groups (e.g., one folder containing several different marble variants):**
Each base name produces its own group. PAT file assignment for multi-group directories uses token intersection scoring between the .pat filename and each group's base_name. Review files (.psd, .gif) are only assigned to the first group in the directory to avoid duplication.

**Megascans 3D asset texture sets:**
Detected by presence of .fbx/.obj/.glb files in the same directory. The entire directory is routed to `_needs_review/mesh_asset/`. These textures are UV-specific to a mesh and require manual review.

**Megascans LOD variants (LOD0, LOD1, LOD2, etc.):**
Handled by `_LOD_TOKEN_RE` stripping in `strip_map_suffix()`. All LOD variants of the same map type collapse into one group entry (whichever is encountered first). In practice, Megascans includes LOD0 (full resolution), LOD1, and LOD2 (lower resolution) — the first encountered is registered, subsequent ones are ignored by the `INSERT OR IGNORE` in `insert_file()` since source_path must be UNIQUE.

**Files with variant designators (VAR1, VAR01, etc.):**
Handled by `_VARIANT_TOKEN_RE` stripping. `ConcreteWall001_COL_VAR1_3K.jpg` → base name `ConcreteWall001`, map type albedo.

**Very large images (>100MP renders, panoramas):**
Skipped during pHash computation. The group is still scanned, pre-filtered, and processed normally — only pHash is skipped. These images will not be detected as duplicates of each other.

**TIF images:**
Processed normally. At output time, if `convert_tif_to_png = True`, the TIF is opened with Pillow and saved as PNG. The output file extension is changed to `.png`. If a crop is also needed, both operations happen in the same open/save call.

**Category hint bypass for Paver:**
When `category_hint = "Paver"` is set, the synthesized result uses `material = "Unknown"` and `material_type = "Unknown"`. The file_ops naming will therefore produce `Paver_Unknown_Unknown_Grey_01.jpg`. This is a known limitation — Paver groups do not receive material/color refinement from the AI. A future improvement would be to run a targeted AI call for material/color fields only, using the hint as the fixed category.

**AI returns unknown category:**
The `_normalize_response()` method attempts case-insensitive lookup in `_category_lookup`. If the normalised category does not match any known category, it is passed through as-is. The Pydantic `_validate_response()` then raises a `ValueError` which triggers a retry. After all retries fail, the group goes to `ai_failed` status.

**Crashed pipeline / interrupted run:**
On resume, the scanner re-encounters all groups. `is_terminal_state(group_id)` is checked before processing any group. Groups in terminal states are skipped. Groups in non-terminal intermediate states (cropping, tileability, ai_tagging, file_ops) are reset by `_mop_up_file_ops()` (for file_ops groups) or reprocessed from their last known good state. The deterministic group_id means the database lookup always finds the right record.

---

## 14. Pending Work and Planned Improvements

The following improvements have been discussed and approved but not yet implemented:

**Paver material/color refinement:**
Groups routed via `category_hint = "Paver"` bypass the AI and receive generic `material = "Unknown"` naming. A targeted AI call that fixes the category to "Paver" and asks only for material/color refinement would produce more useful output names like `Paver_Granite_Sett_Grey_01.jpg`.

**Geometric signal calibration against real data:**
The `unit_geometry_*` ratio thresholds (brick 2.3–3.8, square tile 0.85–1.15, subway tile 1.6–2.4, paver 0.85–1.9) are based on theoretical unit dimensions. They should be validated and potentially adjusted against a labelled sample of known-good texture images from the library to confirm the ratio ranges are empirically correct.

**Disambiguation re-pass for contradictory cases:**
When the AI classifies a group as "Brick" but the computed `unit_aspect_ratio` is in the paver range (0.85–1.9, square/near-square), this is a signal of likely misclassification. A planned optional post-AI pass would flag these contradictory cases, log them, and optionally re-submit with a more pointed prompt. This has been designed but not coded.

**Review UI improvements:**
The multi-select feature added checkbox-based selection, but bulk move could be improved with a drag-and-drop interaction and a preview of where the group will land in the output tree.

**Requirements for running this codebase:**
- Python 3.10+
- Ollama running locally with a vision-capable model pulled (default: `gemma4:e4b`)
- All packages from requirements.txt installed
- Sufficient disk space for the output (plan for roughly 1:1 with source library size since no originals are deleted)
- For the geometry analysis: numpy is pulled in by opencv-python; no additional installation needed
