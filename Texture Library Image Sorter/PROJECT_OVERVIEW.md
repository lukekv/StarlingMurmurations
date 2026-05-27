# Texture Library Pipeline — Project Overview

**Purpose**: A local Python pipeline that ingests a chaotic library of 20,000+ raw texture images, cleans and validates them, deduplicates them, classifies each one using a local AI vision model, and writes a fully organized output library with a consistent naming convention and JSON sidecar for every texture set.

**Context**: Built for an architectural visualization workflow. The output library is used in Revit/Enscape and similar renderers. No files are ever deleted — failed or low-quality assets are routed to review or recycle-bin directories.

---

## Repository Location

```
D:\_AI\Texture Library Image Sorter\Texture Library Image Sorter\texture_pipeline\
```

Launch script:

```
D:\_AI\Texture Library Image Sorter\run_pipeline.bat
```

The batch file sets:
- `INPUT_DIR` = `D:\_AI\Texture Library Image Sorter\_Shared Asset Library`
- `OUTPUT_DIR` = `D:\_AI\Texture Library Image Sorter\_Shared Asset Library\_output`
- `PYTHON` = `C:\Python314\python.exe`

---

## File Map

| File | Role |
|---|---|
| `main.py` | Orchestrator. Runs all five stages in sequence. CLI entry point. |
| `config.py` | Single source of truth for all tunable parameters and paths. |
| `database.py` | SQLite state manager with WAL mode and a serialized writer thread. |
| `scanner.py` | Stage 1. Walks input directory, groups files into PBR sets, registers groups in SQLite. |
| `scanner_helpers.py` | Pure functions supporting the scanner (base map ID, suffix stripping, dimension scraping). |
| `deduplicator.py` | Stage 2. pHash computation + BK-tree Hamming search for perceptual duplicates. |
| `image_processor.py` | Stage 3. Pre-filters, square crop detection, and two-signal tileability test. |
| `ai_tagger.py` | Stage 4. Calls local Ollama/LM Studio vision model to classify and tag each texture. |
| `file_ops.py` | Stage 5. Writes organized output files with standardized naming and JSON sidecar. |
| `requirements.txt` | Python dependencies. |

---

## Pipeline Stages

### Stage 1 — Scan (`scanner.py`)

Recursively walks `input_dir`. For each directory, image files are grouped by their base name after stripping known PBR map suffixes (e.g. `_normal`, `_roughness`, `_albedo`). Each group is a `PBRGroup` dataclass containing:

- `group_id` — SHA-256 hash of directory path + base name (16 hex chars)
- `base_name` — canonical name after suffix stripping
- `base_map_path` — the identified albedo/diffuse map (used for all visual analysis downstream)
- `image_files` — all image maps in the group
- `demo_files` — render/preview images (excluded from tileability tests)
- `pat_files` — Revit `.pat` hatch pattern files
- `real_world_dimensions` — scraped from the base name if present (e.g. `2m x 2m`)

Groups with no identifiable base map are immediately flagged `REVIEW_NO_BASE_MAP`. `.psd` and `.gif` files go to `_needs_review/format_review/`. Configured directory exclusions (`Cut Out Libary`, `ChaosGroupTextureCache`) are skipped entirely. Pipeline-managed output subdirectories are also excluded to prevent re-scanning previously written output.

The scanner checks `is_terminal_state()` before processing each group, so re-running the pipeline on the same database safely skips already-completed groups.

---

### Stage 2 — Deduplication (`deduplicator.py`)

**Pass 1** — concurrent pHash computation via `ThreadPoolExecutor`. Pillow opens each group's base map, checks pixel count (skips images above 100 MP to avoid DecompressionBomb errors), computes `imagehash.phash()`, and stores the hex string in the database.

**Pass 2** — serial BK-tree Hamming search. All computed hashes are indexed in a BK-tree. Every hash is queried against the tree using `phash_hamming_threshold` (default: 4 bits) as the search radius. Duplicate pairs are resolved: the group with the larger base-map pixel area is kept. Ties break alphabetically on `base_name`. Losers are marked `is_duplicate = 1` in the database and their base maps are copied to `_recycle_bin/duplicates/`. A plain-text `duplicate_report.txt` is written to the output directory.

No files are ever deleted.

---

### Stage 3 — Image Processing (`image_processor.py`)

Runs on all `PENDING` groups concurrently via `ThreadPoolExecutor`. Each group's base map is opened once and passed through three pre-filters, then Phase 2 (crop) and Phase 3 (tileability).

**Pre-filter 1 — Minimum resolution**: base maps below `min_resolution_px` (default: 512px on the shortest axis) are routed to `_recycle_bin/low_resolution/` and marked `BINNED_RESOLUTION`.

**Pre-filter 2 — Blank / solid-colour detection**: grayscale pixel standard deviation check. Images with `stddev < blank_image_stddev_bin` (default: 8.0) are routed to `_recycle_bin/blank_images/` and marked `BINNED_BLANK`. Threshold is conservative to avoid false positives on pale plaster or light fabric.

**Pre-filter 3 — Line-art / technical drawing detection**: measures the fraction of near-white pixels (>= 240/255). Images above `line_art_white_pixel_threshold` (default: 0.60, i.e. 60% near-white) are flagged as probable CAD output, site plans, or architectural documents. They are routed to `_needs_review/line_art/` and marked `REVIEW_LINE_ART`.

**Phase 2 — Square check and crop**: if the base map is non-square and the deviation is within `square_tolerance` (default: 2%), a center crop bounding box is computed. The crop is stored as a normalized `CropBbox` (fractions of original dimensions) so it can be applied proportionally to every map in a mixed-resolution PBR set at file-ops time.

**Phase 3 — Two-signal tileability test**: both signals must pass for a texture to be considered tileable.

- **Signal 1 (interior-calibrated gradient spike)**: Sobel magnitude is computed on the grayscale image. Edge strip means (8px wide) are compared against the interior mean (edge strips excluded from the baseline). A ratio above `tileability_gradient_ratio_threshold` (default: 1.8) fails. Using the interior as the denominator prevents directional textures (parquet, corrugated metal) from being falsely flagged.

- **Signal 2 (seam pixel difference)**: mean absolute RGB difference between left vs. right edge strips and top vs. bottom edge strips. A seamless texture wraps cleanly, so opposite edges are nearly identical. Images above `tileability_seam_diff_threshold` (default: 25.0) fail. This is the primary catch for artwork, photography, and non-seamless renders that pass the gradient test.

Filenames containing `seamless` or `tileable` (case-insensitive) bypass both tests entirely. Failures go to `_needs_review/tileability_failed/` (or `_recycle_bin/tileability_failed/` if `auto_bin_tileability_failures = True`). Groups that pass move to `AI_TAGGING` status.

---

### Stage 4 — AI Tagging (`ai_tagger.py`)

Single-threaded by design. GPU is the bottleneck and Ollama serializes inference regardless. The main loop processes groups one at a time.

**Model**: local Ollama or LM Studio instance via OpenAI-compatible API. Default endpoint: `http://localhost:11434/v1`. Default model: `gemma4:e4b`. The model must support vision (image) input.

**Image prep**: base map is resized to fit within `ai_input_resolution` (default: 1024px on the longest axis) and encoded as JPEG base64 before sending.

**System prompt**: instructs the model to return a single JSON object with exactly these fields:
- `category` — one of 16 allowed material categories
- `material` — base substance, 1-2 words (e.g. Cedar, Concrete)
- `material_type` — form or finish, 1-2 words (e.g. Planks, Polished)
- `dominant_color` — one of 50 allowed architectural color names
- `tags` — 3 to 8 lowercase underscore-separated tags
- `is_tileable` — boolean
- `real_world_size_estimate` — string (e.g. `1m x 1m` or `unknown`)

**Allowed categories**: Art, Brick, Concrete, Fabric, Ground, Metal, Misc, Patterns, Plaster and Stucco, Rammed Earth, Rug, Shingle, Stone, Tile, WallCovering, Wood.

**Classification rules enforced in the system prompt**:
- Category reflects material identity, not application (brick on a wall is still Brick, not WallCovering).
- Technical drawings, site plans, CAD output, and architectural hatch patterns are always Misc.
- Siding/cladding products always get the `siding` tag regardless of category.
- Tile hard override: any visible grout lines or individual tile units make the category Tile unconditionally.

**Response validation**: raw JSON is parsed, normalized (handles PascalCase keys, wrong capitalization, missing fields, legacy key names), then validated through a Pydantic `AITagResult` model. Invalid or unknown category strings are rejected. Up to `ai_max_retries` attempts with exponential backoff. Failed groups are marked `AI_FAILED` and skipped at file ops.

**Misc routing**: groups tagged as Misc are written to `_needs_review/misc/` rather than the main output library, keeping architectural drawings and non-texture content separate.

---

### Stage 5 — File Operations (`file_ops.py`)

Runs inline after each successful AI tag (not as a separate batch pass) so output files appear on disk continuously rather than all at once at the end.

**Output directory structure**:
```
output/
  {Category}/
    {Category}_{Material}_{Type}_{Color}_{V##}/
      {Category}_{Material}_{Type}_{Color}_{V##}.png         <- base map, no suffix
      {Category}_{Material}_{Type}_{Color}_{V##}_NORM.png
      {Category}_{Material}_{Type}_{Color}_{V##}_ROUGH.png
      {Category}_{Material}_{Type}_{Color}_{V##}_DISP.png
      {Category}_{Material}_{Type}_{Color}_{V##}_AO.png
      {Category}_{Material}_{Type}_{Color}_{V##}.pat
      {Category}_{Material}_{Type}_{Color}_{V##}.json
```

The variant number `V##` auto-increments if a texture with the same base slug already exists in the category directory.

**Standardized PBR map codes**: NORM, ROUGH, METAL, DISP, AO, OPAC, EMIS.

**Image writing**: TIF files are converted to PNG if `convert_tif_to_png = True`. If a crop bounding box was computed in Stage 3, it is applied proportionally to every image in the group during copy (not just the base map), preserving correct spatial alignment across mixed-resolution PBR sets.

**JSON sidecar** written alongside every texture set. Fields include:
- `texture_name`, `material`, `material_type`, `dominant_color`, `category`
- `tags`, `gradient_test_passed`, `ai_is_tileable`
- `real_world_size_estimate`, `real_world_dimensions`
- `maps` (list of map codes present), `has_pat`
- `source_files` (map type to original source path)
- `processed_date`

---

## Database (`database.py`)

SQLite database at `pipeline_state.db` (default: inside the output directory). WAL mode enabled for concurrent reads during processing. All writes are serialized through a single dedicated writer thread fed by a `queue.Queue`. Worker threads never write to SQLite directly — they enqueue an operation and return immediately. This eliminates all lock contention under `concurrent.futures` parallelism.

**`groups` table** — one row per PBR group. Key columns: `group_id`, `base_name`, `source_dir`, `base_map_path`, `status`, `status_detail`, `is_duplicate`, `phash`, `ai_output` (JSON), `output_path`, `real_world_dimensions` (JSON).

**`files` table** — one row per individual file. Key columns: `file_id`, `group_id`, `source_path`, `map_type`, `is_base_map`, `is_demo`, `is_pat`, `width`, `height`, `output_path`, `status`.

**Group statuses** (in flow order):

| Status | Meaning |
|---|---|
| `pending` | Registered, not yet processed |
| `dedup_check` | In deduplication pass |
| `duplicate` | Marked as duplicate, base map copied to recycle bin |
| `cropping` | Entering image processing |
| `tileability` | Running tileability test |
| `tileability_failed` | Failed tileability, routed to `_needs_review/tileability_failed/` |
| `ai_tagging` | Entering AI classification |
| `ai_failed` | AI tagging failed after all retries |
| `file_ops` | AI tagging succeeded, waiting for file write |
| `completed` | All output files written successfully |
| `binned_resolution` | Base map below minimum resolution |
| `binned_blank` | Solid-colour / blank image |
| `review_no_base_map` | No albedo/diffuse map identifiable |
| `review_format` | Unsupported format (PSD, GIF) |
| `review_line_art` | Probable technical drawing or CAD output |

Terminal states (`completed`, `duplicate`, `tileability_failed`, `review_*`, `binned_*`) are never re-entered on a resumed run.

---

## Configuration (`config.py`)

All parameters are in a single `Config` dataclass. Edit this file before running. Nothing is hardcoded elsewhere. Key parameters:

| Parameter | Default | Purpose |
|---|---|---|
| `min_resolution_px` | 512 | Shortest-axis pixel minimum for base maps |
| `blank_image_stddev_bin` | 8.0 | Grayscale stddev threshold for blank detection |
| `line_art_white_pixel_threshold` | 0.60 | Near-white pixel fraction for drawing detection |
| `square_tolerance` | 0.02 | Aspect ratio tolerance before center crop (2%) |
| `tileability_edge_strip_px` | 8 | Edge strip width in pixels for tileability test |
| `tileability_gradient_ratio_threshold` | 1.8 | Max edge-to-interior gradient ratio |
| `tileability_seam_diff_threshold` | 25.0 | Max opposite-edge mean pixel difference (0-255) |
| `auto_bin_tileability_failures` | False | Route tileability failures to recycle bin instead of review |
| `phash_hamming_threshold` | 4 | Hamming distance threshold for duplicate detection |
| `max_pixels_for_phash` | 100,000,000 | Skip pHash for images over 100 MP |
| `ai_model` | `gemma4:e4b` | Ollama model string (must support vision) |
| `ai_input_resolution` | 1024 | Max dimension for AI image input |
| `ai_max_retries` | 3 | AI tagging retry attempts with exponential backoff |
| `cpu_workers` | 6 | Thread count for image processing |
| `convert_tif_to_png` | True | Convert TIF to PNG on output |
| `exclude_dirs` | `Cut Out Libary`, `ChaosGroupTextureCache` | Directory names to skip entirely during scan |

---

## Python Dependencies

```
Pillow>=10.0.0          # image I/O, crop, resize, format conversion
opencv-python>=4.8.0    # Sobel gradient for tileability test
imagehash>=4.3.1        # pHash perceptual hashing
rapidfuzz>=3.0.0        # fuzzy string matching for PBR group identification
pydantic>=2.0.0         # AI response schema validation
openai>=1.0.0           # OpenAI-compatible client for Ollama / LM Studio
```

Install with:
```
pip install -r requirements.txt
```

---

## Running the Pipeline

**Normal run** (via batch file):
```
run_pipeline.bat
```

**Resume a crashed or interrupted run** (the database already exists — the pipeline picks up where it left off):
```
python main.py --input "..." --output "..." --db "path\to\pipeline_state.db"
```

**Dry run** (scan and deduplication only, no output files written):
```
python main.py --input "..." --output "..." --dry-run
```

**Reset and start over** (deletes all prior state):
```
del pipeline_state.db
rmdir /s /q _output
```
Then re-run the batch file.

**Safe interrupt**: `Ctrl+C` during a run saves progress cleanly. SQLite WAL mode makes even a hard kill (power loss, task kill) safe — the database will not be corrupted.

---

## Output Directory Layout

```
_output/
  pipeline_YYYYMMDD_HHMMSS.log
  pipeline_state.db
  duplicate_report.txt
  Brick/
    Brick_Clay_Running_Bond_Terracotta_01/
      Brick_Clay_Running_Bond_Terracotta_01.png
      Brick_Clay_Running_Bond_Terracotta_01_NORM.png
      Brick_Clay_Running_Bond_Terracotta_01_ROUGH.png
      Brick_Clay_Running_Bond_Terracotta_01.json
  Wood/
    Wood_Cedar_Planks_Blonde_01/
      ...
  _recycle_bin/
    duplicates/
    low_resolution/
    blank_images/
    tileability_failed/      <- only if auto_bin_tileability_failures = True
  _needs_review/
    tileability_failed/      <- default destination for tileability failures
    line_art/
    no_base_map/
    format_review/
    misc/
```

---

## Design Constraints

- **No file deletion**: source files are never modified or deleted. The pipeline only reads from `input_dir` and writes copies to `output_dir`, `recycle_bin_dir`, and `review_dir`.
- **Resume safety**: the SQLite database is the sole state artifact. Any stage can be interrupted and the pipeline resumes cleanly on next run.
- **Concurrency model**: CPU-bound work (hashing, image analysis) runs in `ThreadPoolExecutor`. GPU inference (Ollama) runs single-threaded since Ollama serializes requests regardless. All SQLite writes go through a single dedicated writer thread.
- **No external services**: the entire pipeline runs locally. The AI model is a local Ollama or LM Studio instance. No network calls are made except to `localhost`.
