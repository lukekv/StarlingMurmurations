# Texture Library Image Sorter — Project Summary

**Prepared for:** Presentation / external AI handoff  
**Audience:** General — no deep coding or AI knowledge assumed  
**Project location:** `D:\_AI\Texture Library Image Sorter\`

---

## 1. What This Project Is

The **Texture Library Image Sorter** is an automated software pipeline — a series of steps that run one after another like an assembly line — that takes a large, disorganized folder of texture image files and turns it into a clean, searchable, categorized library.

A **texture** in this context is a flat image file (a photograph or a digitally created surface pattern) that is applied to 3D surfaces in architectural visualization software like Revit and Enscape. When an architect wants a wall to look like red brick, or a floor to look like dark oak wood, they apply a texture image to that surface.

The library in question contains over **20,000 texture files** accumulated from multiple sources over many years. Without this tool, an architect wanting to find a specific material would need to manually browse thousands of unsorted files — a process that could take hours.

---

## 2. The Problem Being Solved

### 2a. Why a Manual Approach Fails at This Scale

20,000+ files cannot be sorted by hand in any reasonable timeframe. Even at one file per 10 seconds, manually reviewing every texture would take over 55 hours of focused work. And that doesn't account for:

- **Duplicates.** Many sources overlap. The same texture may appear multiple times under different file names or in different folders.
- **PBR texture sets.** Modern textures come in *sets* of related files — a base color image, a normal map (surface detail), a roughness map, a metallic map, and sometimes a displacement map. These need to be recognized as a group and kept together, not treated as separate unrelated files.
- **Junk files.** Not everything in a texture library is actually a usable texture. There are product catalog photos, line drawings, resolution renders, and blank images mixed in.
- **Non-tileable images.** A seamless, "tileable" texture is one that can be repeated edge-to-edge without a visible seam — like wallpaper. Many images in a library look like textures but are actually photographs or artwork that would show an ugly border if tiled. These need to be identified and handled separately.

### 2b. What the Tool Does

The pipeline automatically:

1. Finds and groups all related texture files
2. Removes exact and near-exact duplicates
3. Tests whether each texture is actually usable (right size, not blank, actually tileable)
4. Uses an AI model to identify what category of material each texture is (wood, brick, concrete, metal, etc.) and describe it in detail
5. Copies the organized results into a clean folder structure with consistent, descriptive file names
6. Generates a visual browser — a single HTML file you can open in any web browser — to search and preview the entire library

---

## 3. How the Pipeline Works — The Five Stages

Think of the pipeline like a factory with five workstations. Every texture image passes through each station in order. If it fails a quality check at any station, it is sent to a holding area rather than discarded, so a human can review the decision later.

---

### Stage 1 — Scan (Finding and Grouping Files)

The pipeline walks through all the folders in the input directory and finds every image file. It then **groups** related files together. For example, if it finds:

```
ConcreteWall_Albedo.jpg
ConcreteWall_Normal.jpg
ConcreteWall_Roughness.jpg
```

It recognizes that these three files all belong to the same texture set and groups them as one unit called a **PBR group**.

This grouping is based on the file name (stripping known suffixes like `_Albedo`, `_Normal`, `_Roughness`) and the folder they are in. Files in the same folder with the same base name become one group.

The scanner also:
- **Skips excluded folders** — certain folder names (like `"Single planks"` or `"ChaosGroupTextureCache"`) are known to contain source files that are not usable textures, and are skipped entirely
- **Detects mesh asset folders** — if a folder contains 3D model files (`.fbx`, `.obj`), the textures inside are flagged for human review because they may be tied to a specific object rather than being general-purpose materials
- **Extracts real-world dimensions** — if a file name contains a measurement (like `"48x36in"` or `"600x300mm"`), that is stored alongside the texture for display in the browser. If no unit is specified, it defaults to inches (appropriate for a US-based workflow)
- **Reads category hints from file names** — certain keywords in a file name (like "sky", "hdri", "rug", "paver") are used as pre-classification hints that inform the AI stage later

Every group discovered gets a unique ID stored in a database. If the pipeline crashes and is restarted, it picks up exactly where it left off rather than starting over.

---

### Stage 2 — Deduplication (Finding and Removing Duplicates)

Once all groups are identified, the pipeline checks for duplicates. It does this using a technique called **perceptual hashing (pHash)**.

A perceptual hash is like a "fingerprint" for an image — a short string of numbers that represents what the image looks like, rather than its exact pixel data. Two images that look visually identical (even if they are different file sizes or formats) will produce the same or very similar hash.

The pipeline computes a pHash for every texture's base map (the main color/albedo image). It then compares all hashes to each other using a data structure called a **BK-tree**, which is designed for efficient fuzzy searching — finding things that are *almost* the same rather than *exactly* the same.

If two textures have hashes that are close enough (within a "Hamming distance" of 4, meaning fewer than 4 bit differences out of 64), one is marked as the duplicate and sent to a `_recycle_bin/duplicates/` folder. The one that stays is chosen based on whichever came first alphabetically.

A **duplicate report** text file is written listing every pair found, so you can audit the decisions.

> **Important note identified during development:** pHash at very tight tolerances can incorrectly flag *colorway variants* — the same fabric pattern in different colors — as duplicates. This is a known limitation and an area for future refinement.

---

### Stage 3 — Image Processing (Quality Testing)

Every group that passes deduplication goes through a series of automated quality checks. This stage runs using multiple CPU cores simultaneously (parallel processing) to handle the large number of files efficiently.

#### Check 1 — Minimum Resolution
The base map must be at least **512 pixels** on its shortest side. Anything smaller is too low-resolution to be useful in professional rendering and goes to the recycle bin.

#### Check 2 — Blank / Solid Color Detection
The pipeline calculates the statistical variation in pixel brightness across the image (the "standard deviation"). A perfectly solid-color image scores 0.0. Real textures with surface detail score 2.0 or above. Anything below 2.0 is sent to the recycle bin as a blank or unusable image.

#### Check 3 — Product Photo Detection
A seamless material texture has visual content all the way to its edges. A product catalog photo — like a picture of a chair or a swatch photographed against a clean white or black studio background — has near-uniform (near-zero variation) pixels at all four edges. The pipeline checks the variance of narrow strips along all four edges; if all four are extremely flat, the image is flagged as a product photo and sent to review.

#### Check 4 — Line Art / Technical Drawing Detection
If more than 60% of an image's pixels are near-white (a brightness value of 240 or above out of 255), the image is likely a technical drawing, floor plan, CAD export, or architectural document rather than a photographic texture. These are sent to a review folder.

#### Check 5 — Tileability Testing
This is the most sophisticated check. It tests whether a texture can actually tile seamlessly.

The pipeline uses **two independent signals**:

**Signal 1 — Edge gradient spike:**  
Using an image processing technique called the Sobel filter (which highlights edges and transitions within an image), the pipeline measures how "edgy" the outermost pixel strips are compared to the interior. A properly seamless texture has similar edge content everywhere. A non-tileable image often has a sharp visible seam where the repeating pattern would meet, causing a spike in edge activity at the border. A ratio above **1.8x** triggers a failure.

**Signal 2 — Opposite edge similarity:**  
For a texture to tile seamlessly, the left edge must match the right edge, and the top must match the bottom. The pipeline directly compares opposite edge strips and measures how different they are on average (on a 0-255 brightness scale). A difference above **25 units** triggers a failure. A high-pass filter is applied first to remove lighting gradients that might make two matching edges look different simply because one side of the image is brighter than the other.

Textures that fail either signal are sent to a `_needs_review/tileability_failed/` folder rather than being discarded, because some genuinely useful categories (artwork, sky backgrounds, water textures, utility overlays) are intentionally non-tileable.

---

### Stage 4 — AI Tagging (Identifying and Naming Every Texture)

This is where a **local AI model** looks at each texture and answers a structured set of questions about it.

The AI model runs locally on the same machine using a tool called **Ollama**, which lets you run large language models (the same kind of technology behind ChatGPT) entirely on your own hardware without sending data to the internet. The model used (`gemma4:e4b`) is a multimodal model — meaning it can look at images, not just read text.

For each texture, the pipeline:

1. Loads the base map image and encodes it for the AI to read
2. Sends it to the AI along with a carefully written prompt (a set of instructions) asking it to return a structured JSON response
3. The AI returns: category, material type, dominant color, tags, whether it believes the texture is tileable, and a real-world size estimate

**What the AI determines for each texture:**

| Field | Example |
|---|---|
| Category | `Wood` |
| Material | `Oak` |
| Material type | `Planks` |
| Dominant color | `Beige` |
| Tags | `["wood", "oak", "planks", "natural", "woodgrain"]` |
| Is tileable | `true` |
| Real-world size estimate | `"2m × 2m"` |

The category list is fixed and predefined (23 categories including Art, Brick, Concrete, Fabric, Glass, Metal, Stone, Tile, Wood, etc.). The AI must choose from this exact list.

**Filename hints improve accuracy:**  
Before sending a texture to the AI, the pipeline scans the original filename for keywords that match known categories. If keywords like `"rug"`, `"sky"`, `"hdri"`, or `"paver"` are found, this hint is passed to the AI (or used to bypass the AI entirely for clearly obvious cases). This dramatically improves accuracy for textures that have descriptive file names.

**The AI secondary guard:**  
If the AI says a texture is not tileable but it passed the geometric tileability test in Stage 3, the texture is sent to a `_needs_review/ai_not_tileable/` folder. The AI provides an independent content-based opinion; the geometry test is purely mathematical. When they disagree, human review is requested.

**Retry with backoff:**  
AI model calls sometimes fail (timeout, malformed response). The pipeline automatically retries up to 3 times with increasing delays between attempts. If all retries fail, the group is skipped and can be retried on the next run.

---

### Stage 4b — The Override Pass (Rescuing Non-Tileable Textures)

After the main pipeline run, a second optional pass can be run with the `--override-pass` flag. This pass specifically targets textures that were set aside in the tileability failure folder.

For each of these, the AI is asked to categorize the image. If the AI identifies it as belonging to a category that is *intentionally* non-tileable — `Art`, `Sky`, `Utility`, or `Water` — the texture is rescued and moved to the main library. If the AI confirms it is truly a non-tileable texture (like a product photo of furniture that slipped through), it stays in the review folder.

The override pass is designed to be **interruptible and resumable**: if you press Ctrl+C, it saves its progress. Textures already ruled on get a special status so they won't be re-processed on the next run. Only unfinished work is retried.

---

### Stage 5 — File Operations (Writing the Organized Library)

Once a texture has been categorized and named, its files are copied (never moved or deleted from the source) into the output folder using a consistent naming scheme:

```
_output/
  Wood/
    Wood_Oak_Planks_Beige_01/
      Wood_Oak_Planks_Beige_01.jpg       ← base map, renamed
      Wood_Oak_Planks_Beige_01_NRM.jpg   ← normal map
      Wood_Oak_Planks_Beige_01_RGH.jpg   ← roughness map
      Wood_Oak_Planks_Beige_01.json      ← sidecar file with all metadata
      Wood_Oak_Planks_Beige_01.pat       ← Revit pattern file (if present)
  Concrete/
    Concrete_Cast_Panel_Grey_01/
      ...
```

The naming convention is always: `Category_Material_MaterialType_Color_NN`

Where `NN` is a number that increments if there are multiple textures with the same description (e.g., `_01`, `_02`, `_03`).

The **JSON sidecar file** saved alongside each texture contains all the metadata the AI generated plus the parsed real-world dimensions, original source file path, and a record of which map types are present.

---

## 4. The Preview Browser

After the pipeline runs, a separate script (`generate_preview.py`) generates a single **HTML file** that acts as a visual browser for the entire library. You open it in any web browser — no internet connection required.

Features of the preview browser:
- **Category tabs** at the top for browsing by material type
- **Text search** that filters by material name, color, tags, or source filename in real time
- **PBR filter checkbox** to show only textures that have the full set of PBR maps (not just a single diffuse image)
- **Thumbnail grid** showing 256-pixel preview images for every texture
- **Card details** showing source filename, pixel dimensions, real-world size in inches, map types available, and tags
- **Lightbox** (click a thumbnail to see the full image alongside all metadata)
- **Click-to-copy** the folder path of any texture to your clipboard
- **Debug tabs** for the `_needs_review` and `_recycle_bin` folders, so you can see what was set aside and why
- **Multi-select and batch actions** for moving or removing textures

The preview file is entirely self-contained: all the data is embedded as JavaScript inside the HTML file, so it can be shared or archived without any dependencies.

---

## 5. The Database — The Backbone of Crash Recovery

Every decision the pipeline makes is recorded in a **SQLite database** — a lightweight local database stored as a single file (`pipeline_state.db`) inside the output folder.

The database records the status of every texture group at all times. Statuses progress through stages:

```
pending → dedup_check → tileability → ai_tagging → file_ops → completed
```

With branches for failures:
```
→ duplicate
→ binned_resolution
→ binned_blank
→ binned_product_photo
→ tileability_failed
→ tileability_override_confirmed
→ review_no_base_map
→ review_line_art
→ review_ai_not_tileable
→ review_mesh_asset
```

**Terminal states** are special: once a group reaches a terminal status (completed, duplicate, binned, or any review state), it is **never re-processed** even if the pipeline crashes and is restarted. This is the core of the crash-recovery design — you can safely stop and restart the pipeline at any time without losing work or double-processing anything.

To avoid database conflicts when multiple processes try to write simultaneously, all database writes go through a **single dedicated writer thread** fed by a queue. Worker processes doing image analysis never write to the database directly — they add their writes to the queue and move on. The writer processes them in order, one at a time, eliminating any possibility of data corruption from simultaneous writes.

---

## 6. Key Architecture Decisions and Why They Were Made

### "Never touch the source files"
The pipeline only ever **copies** files to the output folder. It never moves, renames, or deletes anything from the original input directory. This makes the tool safe to run on a production library — the worst case is that you need to delete the output folder and start over.

### Local AI model (Ollama) instead of a cloud API
Running the AI locally means:
- No data leaves the machine (important for proprietary texture libraries)
- No per-call fees (20,000 API calls to a cloud service would cost money)
- No rate limits or internet dependency
- The model can be changed by editing one line in the config file

### Everything configurable through one file
All parameters — resolution thresholds, tileability thresholds, AI model choice, category list, which folders to skip — live in a single `config.py` file. Nothing is hardcoded deep in the processing code. This makes the tool maintainable and tunable without touching the core logic.

### Five separate stages (not one big script)
Keeping each stage separate means:
- You can resume from any stage after a crash
- You can run just the override pass without re-running everything
- You can tune parameters for one stage without affecting others
- Debugging is much easier because failures are contained within a known stage

### The preview browser is a single static HTML file
There is no web server, no database connection, no installation needed to use the preview browser. You just open the file. This makes the finished library shareable: zip the output folder and send it to a colleague — they can open the preview file immediately.

---

## 7. Development History and Iterative Refinements

This tool was developed iteratively through a long collaborative session. Major refinements made during development include:

**Tileability tuning:**  
The initial thresholds for the tileability test were calibrated against real library data. The gradient ratio threshold of 1.8 was found to give directional textures (like wood planks or corrugated metal) enough margin while still catching actual seam failures. A high-pass lighting correction was added to prevent images that are legitimately seamless but unevenly lit from failing the seam-difference test.

**Tokenizer improvements for filename hints:**  
The filename keyword scanner was updated to handle `camelCase` file names (like `BronzeCopper0076`) and file names with digit suffixes (like `RUG1`, `RUG4`). Previously these wouldn't be recognized. The tokenizer now splits on case boundaries and strips trailing digits.

**Color vocabulary expansion:**  
The AI was sometimes returning descriptive color names that the system didn't recognize (`Silver`, `DarkBlue`, `Green`, `Pink`, `LightBrown`). These were added to the accepted color list so they are passed through rather than defaulted to a generic color.

**Excluding compositing source folders:**  
Certain source libraries contain per-plank or per-tile individual scan files (meant to be assembled into a composite texture, not used directly). A folder exclusion system was added so these compositing source folders are skipped at scan time.

**Real-world dimensions:**  
The AI was asked to estimate real-world sizes but its estimates were unreliable (e.g., returning "48cm × 36cm" for a file that clearly said "48x36in" in its name). The solution was to parse dimensions directly from file names using pattern matching, store them separately from the AI's estimate, and always prefer the parsed value. When no unit is found in the filename, the system defaults to inches (appropriate for the US-based workflow). All dimensions are converted and displayed in inches in the preview browser.

**Override pass crash recovery:**  
When the override pass (which re-evaluates tileability failures using AI) was interrupted, a resumed run would wastefully re-run the AI on textures it had already made a decision about. A new database status (`tileability_override_confirmed`) was added: textures the AI has confirmed as non-rescuable are immediately routed and stamped with this terminal status, so resumed runs skip them. Textures mid-processing when interrupted are retried. Rescued textures are already marked `completed`.

---

## 8. File and Folder Structure

```
D:\_AI\Texture Library Image Sorter\
│
├── generate_preview.py          ← Preview browser generator (standalone script)
│
├── Texture Library Image Sorter\
│   └── texture_pipeline\
│       ├── main.py              ← Pipeline orchestrator; runs all five stages
│       ├── config.py            ← All configuration parameters in one place
│       ├── database.py          ← SQLite state management and crash recovery
│       ├── scanner.py           ← Stage 1: file discovery and PBR grouping
│       ├── scanner_helpers.py   ← Helper functions: suffix detection, dimension parsing
│       ├── deduplicator.py      ← Stage 2: pHash duplicate detection
│       ├── image_processor.py   ← Stage 3: quality checks and tileability testing
│       ├── ai_tagger.py         ← Stage 4: local AI categorization and naming
│       ├── file_ops.py          ← Stage 5: output file copying and renaming
│       └── requirements.txt     ← Python package dependencies
│
├── _MHOA Basic Materials\       ← Example input library (test dataset)
│   └── _output\                 ← Pipeline output for this library
│       ├── Wood\                ← One folder per category
│       ├── Concrete\
│       ├── ...
│       ├── _needs_review\       ← Textures requiring human attention
│       ├── _recycle_bin\        ← Duplicates and quality failures
│       ├── pipeline_state.db    ← State database for this run
│       ├── pipeline_YYYYMMDD.log← Timestamped run log
│       ├── duplicate_report.txt ← List of all duplicate pairs found
│       └── library_preview.html ← Visual browser (open in any browser)
│
└── _Shared Asset Library\       ← Full production library (20,000+ files)
    └── _output\                 ← (same structure as above)
```

---

## 9. How to Run the Tool

**Full pipeline run:**
```
python main.py --input "path\to\texture\library" --output "path\to\_output"
```

**Resume after a crash** (re-run the same command — the database prevents re-processing):
```
python main.py --input "path\to\texture\library" --output "path\to\_output"
```

**Override pass** (rescue non-tileable Art/Sky/Utility/Water textures — run after the main pipeline):
```
python main.py --input "path\to\texture\library" --output "path\to\_output" --override-pass
```

**Generate the visual preview browser** (run after the pipeline):
```
python generate_preview.py --output "path\to\_output"
```

---

## 10. Technology Stack

| Component | Technology | Why |
|---|---|---|
| Programming language | Python 3.14 | Cross-platform, strong image processing ecosystem |
| Image processing | Pillow (PIL) | Industry standard Python image library |
| Database | SQLite | Single-file, zero-installation, reliable |
| AI model | Gemma 4 (via Ollama) | Local, free, vision-capable, no internet required |
| AI API format | OpenAI-compatible (via Ollama) | Standardized, swappable with different models |
| Parallel processing | Python `concurrent.futures` | Built-in, simple CPU multi-threading |
| Preview browser | Single-file HTML + vanilla JavaScript | No dependencies, shareable, works offline |
| Duplicate detection | pHash + BK-tree | Efficient, handles near-identical images |

---

*Document generated 2026-05-26. The pipeline continues to be actively developed.*
