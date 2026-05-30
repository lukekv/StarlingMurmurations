#!/usr/bin/env python3
"""
generate_preview.py
-------------------
Generates library_preview.html in the pipeline output folder.

Scans processed texture groups (each folder with a JSON sidecar), generates
256px JPEG thumbnails, and builds a single-file HTML browser with:
  - Category tabs matching the output folder structure
  - Tag/material/color search with instant filtering
  - Thumbnail grid with lightbox full-size preview
  - Click texture name to copy Windows folder path to clipboard
  - DEBUG: original source filename shown on each card
  - DEBUG: _needs_review subfolders shown as amber-labelled tabs

Usage:
    python generate_preview.py --output "D:\\path\\to\\_output"

Dependencies: Pillow (already in requirements.txt)
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required: pip install pillow --break-system-packages")

# Suppress Pillow's DecompressionBombWarning for large-but-valid textures.
# Professional texture libraries routinely contain 100–180 MP images; the
# warning is a DOS-attack guard for untrusted web images and is noise here.
# Images that actually exceed the hard error limit (~178 MP) are still caught
# and handled gracefully inside make_thumbnail().
warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

THUMB_SIZE    = 256
THUMB_DIR     = "_thumbnails"
HTML_FILENAME = "library_preview.html"
IMAGE_EXTS    = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

# These map_type keys in source_files indicate the base/albedo map
_BASE_MAP_KEYS = {
    "albedo", "base_color", "basecolor", "diffuse", "diff",
    "color", "col", "bc", "d", "texture", "base", "unknown",
}


# ---------------------------------------------------------------------------
# Thumbnail generation
# ---------------------------------------------------------------------------

def make_thumbnail(src: Path, thumb_dir: Path, prefix: str = "") -> str | None:
    """
    Generate a 256px JPEG thumbnail and return its relative path from
    the output root (e.g. '_thumbnails/Wood_Cedar_01.jpg').
    Returns None on failure.

    Caching behaviour:
      - Non-empty .jpg exists  → real thumbnail, returned immediately (no re-generate).
      - Empty .jpg exists      → skip marker left by a previous failure, skip silently.
      - No file               → attempt generation.

    Very large images (> Pillow's decompression-bomb limit, ~178 MP) raise
    DecompressionBombError.  On any failure an empty marker file is touched so
    the image is never retried on subsequent runs.  DecompressionBombWarning
    (89–178 MP range) is suppressed; those images still thumbnail successfully.
    """
    safe_stem = src.stem.replace(" ", "_")
    thumb_name = f"{prefix}{safe_stem}.jpg"
    thumb_path = thumb_dir / thumb_name

    if thumb_path.exists():
        # Empty file = skip marker from a previous failure — don't retry
        return f"{THUMB_DIR}/{thumb_name}" if thumb_path.stat().st_size > 0 else None

    try:
        img = Image.open(src).convert("RGB")
        img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
        img.save(thumb_path, "JPEG", quality=75, optimize=True)
    except Exception as exc:
        print(f"  WARNING: thumbnail failed for {src.name}: {exc}")
        thumb_path.touch()   # empty marker: skip on future runs
        return None

    return f"{THUMB_DIR}/{thumb_name}"


# ---------------------------------------------------------------------------
# Sidecar helpers
# ---------------------------------------------------------------------------

def find_sidecar(texture_dir: Path) -> dict | None:
    """Return parsed JSON sidecar from texture_dir, or None."""
    for f in texture_dir.iterdir():
        if f.suffix == ".json":
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


def find_base_map(texture_dir: Path, texture_name: str) -> Path | None:
    """
    Find the base map image in texture_dir.
    The base map stem exactly matches the texture_name (no MAPCODE suffix).
    """
    for ext in IMAGE_EXTS:
        candidate = texture_dir / (texture_name + ext)
        if candidate.exists():
            return candidate
    return None


def get_image_size(img_path: Path) -> tuple[int, int] | tuple[None, None]:
    """Return (width, height) of img_path, or (None, None) on failure."""
    try:
        with Image.open(img_path) as img:
            return img.size
    except Exception:
        return (None, None)


def extract_source_basename(sidecar: dict) -> str:
    """
    Extract the original source filename from the sidecar's source_files dict.
    Prefers base-map-type keys; falls back to the first available entry.
    """
    source_files = sidecar.get("source_files", {})
    if not source_files:
        return ""
    for key, path_str in source_files.items():
        if key.lower() in _BASE_MAP_KEYS:
            return Path(path_str).name
    return Path(next(iter(source_files.values()))).name


# Conversion factors: all units → inches
_INCHES_FACTORS: dict = {
    "inches": 1.0,
    "feet":   12.0,
    "cm":     1.0 / 2.54,
    "mm":     1.0 / 25.4,
    "m":      39.3701,
}


def _dims_to_inches_str(real_world_dims: dict | None) -> str | None:
    """
    Convert a real_world_dimensions sidecar dict to a display string in inches.
    Returns None if the dict is absent or unparseable.

    Examples:
        {"width": 48.0,  "height": 36.0,  "unit": "inches"} → "48 × 36 in"
        {"width": 600.0, "height": 300.0, "unit": "mm"}     → "23.6 × 11.8 in"
        {"width": 2.0,   "height": 4.0,   "unit": "feet"}   → "24 × 48 in"
    """
    if not real_world_dims:
        return None
    try:
        w    = float(real_world_dims["width"])
        h    = float(real_world_dims["height"])
        unit = (real_world_dims.get("unit") or "inches").lower()
        f    = _INCHES_FACTORS.get(unit, 1.0)
        wi, hi = w * f, h * f

        def _fmt(v: float) -> str:
            return f"{v:.0f}" if v == int(v) else f"{v:.1f}"

        return f"{_fmt(wi)} × {_fmt(hi)} in"
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Data record builders
# ---------------------------------------------------------------------------

def load_texture(
    texture_dir: Path,
    thumb_dir: Path,
    output_dir: Path,
    is_review: bool = False,
    review_reason: str = "",
) -> dict | None:
    """
    Build a data record for a processed texture group that has a JSON sidecar.
    Returns None if no usable sidecar exists.
    """
    sidecar = find_sidecar(texture_dir)
    if sidecar is None:
        return None

    texture_name = sidecar.get("texture_name") or texture_dir.name
    base_map     = find_base_map(texture_dir, texture_name)

    prefix    = "_rev_" if is_review else ""
    thumb_rel = make_thumbnail(base_map, thumb_dir, prefix) if base_map else None

    base_rel = (
        str(base_map.relative_to(output_dir)).replace("\\", "/")
        if base_map else None
    )

    px_w, px_h = get_image_size(base_map) if base_map else (None, None)

    # Prefer filename-parsed dimensions (converted to inches) over AI's free-text estimate.
    # AI estimate is retained only as a last resort and "unknown" is suppressed entirely.
    _size_est = _dims_to_inches_str(sidecar.get("real_world_dimensions"))
    if not _size_est:
        _ai_est   = sidecar.get("real_world_size_estimate") or ""
        _size_est = "" if _ai_est in ("unknown", "") else _ai_est

    return {
        "name":          texture_name,
        "category":      sidecar.get("category", ""),
        "material":      sidecar.get("material", ""),
        "material_type": sidecar.get("material_type", ""),
        "color":         sidecar.get("dominant_color", ""),
        "tags":          sidecar.get("tags", []),
        "maps":          sidecar.get("maps", []),
        "size_est":      _size_est,
        "px_w":          px_w,
        "px_h":          px_h,
        "thumb":         thumb_rel,
        "base_img":      base_rel,
        "folder_path":   str(texture_dir).replace("/", "\\"),
        "source_file":   extract_source_basename(sidecar),
        "is_review":     is_review,
        "review_reason": review_reason,
    }


def load_raw_review_item(
    img_path: Path,
    thumb_dir: Path,
    output_dir: Path,
    reason: str,
) -> dict:
    """
    Build a data record for a raw file in _needs_review (no sidecar).
    """
    thumb_rel = None
    if img_path.suffix.lower() in IMAGE_EXTS:
        thumb_rel = make_thumbnail(img_path, thumb_dir, prefix="_rev_")

    base_rel = (
        str(img_path.relative_to(output_dir)).replace("\\", "/")
        if img_path.exists() else None
    )

    px_w, px_h = get_image_size(img_path) if img_path.suffix.lower() in IMAGE_EXTS else (None, None)

    return {
        "name":          img_path.stem,
        "category":      f"Review: {reason}",
        "material":      "",
        "material_type": "",
        "color":         "",
        "tags":          [],
        "maps":          [],
        "size_est":      "",
        "px_w":          px_w,
        "px_h":          px_h,
        "thumb":         thumb_rel,
        "base_img":      base_rel,
        "folder_path":   str(img_path.parent).replace("/", "\\"),
        "source_file":   img_path.name,
        "is_review":     True,
        "review_reason": reason,
    }


# ---------------------------------------------------------------------------
# Directory scanning
# ---------------------------------------------------------------------------

def scan_output(output_dir: Path, thumb_dir: Path) -> dict:
    """
    Walk output_dir for category subdirectories (skips anything starting with _).
    Returns ordered dict: category_name -> list of texture records.
    """
    categories = {}
    for cat_dir in sorted(output_dir.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name.startswith("_"):
            continue
        textures = []
        for texture_dir in sorted(cat_dir.iterdir()):
            if not texture_dir.is_dir():
                continue
            record = load_texture(texture_dir, thumb_dir, output_dir)
            if record:
                textures.append(record)
        if textures:
            categories[cat_dir.name] = textures
    return categories


def scan_needs_review(output_dir: Path, thumb_dir: Path) -> dict:
    """
    Walk _needs_review for debug sections.
    misc/ subdirs have JSON sidecars. All other subfolders are flat image files.
    """
    review_dir = output_dir / "_needs_review"
    if not review_dir.exists():
        return {}

    categories = {}
    for subdir in sorted(review_dir.iterdir()):
        if not subdir.is_dir():
            continue

        label = f"Review: {subdir.name}"
        items = []

        if subdir.name == "misc":
            # misc has JSON sidecars — use full texture loader
            for texture_dir in sorted(subdir.iterdir()):
                if texture_dir.is_dir():
                    record = load_texture(
                        texture_dir, thumb_dir, output_dir,
                        is_review=True, review_reason="misc",
                    )
                    if record:
                        items.append(record)
        else:
            entries = sorted(subdir.iterdir())
            if any(e.is_file() for e in entries):
                # Flat: files directly in the subdir
                for f in entries:
                    if f.is_file() and f.suffix.lower() in IMAGE_EXTS | {".psd", ".gif"}:
                        items.append(
                            load_raw_review_item(f, thumb_dir, output_dir, subdir.name)
                        )
            else:
                # Nested: per-group subdirectories (no sidecar)
                for group_dir in entries:
                    if not group_dir.is_dir():
                        continue
                    img = next(
                        (f for f in sorted(group_dir.iterdir())
                         if f.is_file() and f.suffix.lower() in IMAGE_EXTS),
                        None,
                    )
                    if img:
                        record = load_raw_review_item(img, thumb_dir, output_dir, subdir.name)
                        record["name"]        = group_dir.name
                        record["folder_path"] = str(group_dir).replace("/", "\\")
                        items.append(record)

        if items:
            categories[label] = items

    return categories


def scan_recycle_bin(output_dir: Path, thumb_dir: Path) -> dict:
    """
    Walk _recycle_bin for debug sections.
    Flat subdirs (duplicates, low_resolution): one card per image file.
    Nested subdirs (blank_images, product_photo): one card per group subdir.
    """
    bin_dir = output_dir / "_recycle_bin"
    if not bin_dir.exists():
        return {}

    categories = {}
    for subdir in sorted(bin_dir.iterdir()):
        if not subdir.is_dir():
            continue

        label   = f"Bin: {subdir.name}"
        items   = []
        entries = sorted(subdir.iterdir())

        if any(e.is_file() for e in entries):
            # Flat structure: one card per image file
            for f in entries:
                if f.is_file() and f.suffix.lower() in IMAGE_EXTS | {".psd", ".gif"}:
                    items.append(
                        load_raw_review_item(f, thumb_dir, output_dir, subdir.name)
                    )
        else:
            # Nested structure: one card per group subdirectory
            for group_dir in entries:
                if not group_dir.is_dir():
                    continue
                img = next(
                    (f for f in sorted(group_dir.iterdir())
                     if f.is_file() and f.suffix.lower() in IMAGE_EXTS),
                    None,
                )
                if img:
                    record = load_raw_review_item(img, thumb_dir, output_dir, subdir.name)
                    record["name"]        = group_dir.name
                    record["folder_path"] = str(group_dir).replace("/", "\\")
                    items.append(record)

        if items:
            categories[label] = items

    return categories


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------
# Data is injected by replacing the /*TEXTURE_DATA*/ placeholder.
# The template uses no f-string so JS curly braces need no escaping.

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Texture Library Preview</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #1a1a1a; color: #e0e0e0; min-height: 100vh; }

/* ---- Header ---- */
#header { background: #111; padding: 14px 24px; display: flex;
          align-items: center; gap: 14px; border-bottom: 1px solid #2e2e2e;
          position: sticky; top: 0; z-index: 200; flex-wrap: wrap; }
#header h1 { font-size: 17px; font-weight: 700; color: #fff;
             white-space: nowrap; letter-spacing: -0.3px; }
#search { flex: 1; min-width: 200px; padding: 8px 14px;
          background: #242424; border: 1px solid #3a3a3a;
          border-radius: 6px; color: #e0e0e0; font-size: 13px; outline: none; }
#search:focus { border-color: #5b8dd9; }
#search::placeholder { color: #555; }
#search-clear { padding: 7px 13px; background: #2a2a2a; border: 1px solid #3a3a3a;
                border-radius: 6px; color: #888; cursor: pointer;
                font-size: 12px; white-space: nowrap; transition: background 0.1s; }
#search-clear:hover { background: #333; color: #ccc; }
#count { font-size: 12px; color: #666; white-space: nowrap; min-width: 80px;
         text-align: right; }

/* ---- Tabs ---- */
#tabs { background: #141414; padding: 0 20px; display: flex; gap: 0;
        overflow-x: auto; border-bottom: 1px solid #2a2a2a;
        scrollbar-width: thin; scrollbar-color: #333 transparent; }
.tab { padding: 10px 15px; font-size: 12px; cursor: pointer;
       border-bottom: 3px solid transparent; white-space: nowrap;
       color: #666; transition: color 0.15s; user-select: none; }
.tab:hover { color: #bbb; }
.tab.active { color: #fff; border-bottom-color: #5b8dd9; }
.tab.review-tab { color: #7a5a30; }
.tab.review-tab:hover { color: #c0883a; }
.tab.review-tab.active { color: #c0883a; border-bottom-color: #c0883a; }
.tab-count { display: inline-block; background: #2a2a2a; border-radius: 9px;
             padding: 1px 6px; font-size: 10px; margin-left: 4px; color: #666; }
.tab.active .tab-count { background: #1e2e45; color: #7aabff; }
.tab.review-tab.active .tab-count { background: #3a2a10; color: #c0883a; }
.tab.bin-tab { color: #6a2a2a; }
.tab.bin-tab:hover { color: #c05050; }
.tab.bin-tab.active { color: #c05050; border-bottom-color: #c05050; }
.tab.bin-tab.active .tab-count { background: #3a1a1a; color: #c05050; }

/* ---- Grid ---- */
#grid { padding: 20px 24px;
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
        gap: 14px; }

/* ---- Card ---- */
.card { background: #222; border-radius: 8px; overflow: hidden;
        border: 1px solid #2e2e2e; transition: transform 0.12s, box-shadow 0.12s; }
.card:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.5);
              border-color: #484848; }
.card.review-card { border-color: #3a2810; }
.card.review-card:hover { border-color: #7a4818; }
.card.bin-card { border-color: #3a1a1a; }
.card.bin-card:hover { border-color: #7a2020; }

.card-thumb { width: 100%; aspect-ratio: 1; overflow: hidden;
              background: #191919; cursor: pointer; position: relative; }
.card-thumb img { width: 100%; height: 100%; object-fit: cover; display: block;
                  transition: opacity 0.15s; }
.card-thumb img:hover { opacity: 0.85; }
.no-thumb { width: 100%; height: 100%; display: flex; align-items: center;
            justify-content: center; color: #444; font-size: 12px; cursor: pointer; }

.card-body { padding: 9px 11px 11px; }

.card-name { font-size: 11px; font-weight: 600; color: #ccc; margin-bottom: 2px;
             word-break: break-all; line-height: 1.4; cursor: pointer;
             transition: color 0.1s; }
.card-name:hover { color: #7aabff; }
.copy-confirm { font-size: 10px; color: #5b9; margin-left: 4px;
                display: none; }

.debug-block { margin-bottom: 6px; }
.debug-label { font-size: 9px; text-transform: uppercase; letter-spacing: 0.6px;
               color: #484848; margin-bottom: 1px; }
.debug-value { font-size: 10px; color: #9a6830; word-break: break-all;
               line-height: 1.3; }

.chips { display: flex; flex-wrap: wrap; gap: 3px; margin-bottom: 5px; }
.chip { font-size: 10px; padding: 2px 6px; border-radius: 3px;
        white-space: nowrap; line-height: 1.4; }
.chip-map { background: #162035; color: #6a9adf; }
.chip-map.base { background: #162518; color: #5aaf6a; }
.chip-review { background: #2e1e08; color: #b07830; font-size: 9px;
               letter-spacing: 0.3px; }
.chip-bin { background: #2e0e0e; color: #b05050; font-size: 9px;
            letter-spacing: 0.3px; }
.chip-tag { background: #252525; color: #888; border: 1px solid #333;
            cursor: pointer; }
.chip-tag:hover { background: #2e2e2e; color: #bbb; border-color: #4a4a4a; }

/* ---- No results ---- */
#no-results { grid-column: 1/-1; text-align: center; padding: 80px 20px;
              color: #444; font-size: 14px; }

/* ---- Lightbox ---- */
#lightbox { display: none; position: fixed; inset: 0; z-index: 500;
            background: rgba(0,0,0,0.88); align-items: center;
            justify-content: center; padding: 20px; }
#lightbox.open { display: flex; }
#lb-inner { position: relative; background: #1e1e1e; border-radius: 10px;
            overflow: hidden; display: flex; max-width: 92vw; max-height: 90vh;
            box-shadow: 0 24px 64px rgba(0,0,0,0.8); flex-direction: row;
            border: 1px solid #333; }
#lb-img-wrap { display: flex; align-items: center; justify-content: center;
               background: #111; min-width: 300px; max-width: 65vw;
               max-height: 90vh; overflow: hidden; }
#lb-img { max-width: 65vw; max-height: 90vh; object-fit: contain; display: block; }
#lb-meta { width: 280px; min-width: 240px; padding: 20px; overflow-y: auto;
           max-height: 90vh; border-left: 1px solid #2a2a2a; }
#lb-close { position: absolute; top: 10px; right: 10px; background: #333;
            border: 1px solid #444; color: #aaa; font-size: 16px; cursor: pointer;
            border-radius: 50%; width: 30px; height: 30px; display: flex;
            align-items: center; justify-content: center; z-index: 10;
            transition: background 0.1s; }
#lb-close:hover { background: #484848; color: #fff; }
#lb-name { font-size: 13px; font-weight: 700; color: #fff; margin-bottom: 10px;
           word-break: break-all; line-height: 1.4; padding-right: 24px; }
.lb-section { margin-bottom: 10px; }
.lb-label { font-size: 9px; text-transform: uppercase; letter-spacing: 0.6px;
            color: #484848; margin-bottom: 3px; }
.lb-value { font-size: 11px; color: #aaa; word-break: break-all; line-height: 1.4; }
.lb-source { font-size: 11px; color: #9a6830; word-break: break-all;
             line-height: 1.4; }
.lb-path { font-size: 10px; color: #666; word-break: break-all; line-height: 1.4;
           font-family: 'Consolas', 'Courier New', monospace; }
#lb-chips { display: flex; flex-wrap: wrap; gap: 3px; margin-bottom: 8px; }
#lb-tags  { display: flex; flex-wrap: wrap; gap: 3px; }
#lb-copy-btn { margin-top: 14px; width: 100%; padding: 8px;
               background: #1e2e40; border: 1px solid #2a4060;
               border-radius: 5px; color: #7aabff; font-size: 12px;
               cursor: pointer; transition: background 0.1s; }
#lb-copy-btn:hover { background: #263848; }
#lb-tile-btn { margin-top: 6px; width: 100%; padding: 8px;
               background: #1e2e1e; border: 1px solid #2a5a3a;
               border-radius: 5px; color: #7affb2; font-size: 12px;
               cursor: pointer; transition: background 0.1s; }
#lb-tile-btn:hover { background: #263a30; }

/* ---- Tile preview (inside lightbox) ---- */
#tp-canvas { display: none; cursor: crosshair; }
#lb-tile-controls { display: none; margin-top: 10px;
                    border-top: 1px solid #2a2a2a; padding-top: 10px; }
.lb-tile-label { font-size: 10px; color: #555; margin-bottom: 6px;
                 text-transform: uppercase; letter-spacing: 0.06em; }
#tp-mode-btns { display: flex; flex-wrap: wrap; gap: 5px; }
.tp-btn { padding: 5px 9px; border-radius: 4px; border: 1px solid #333;
          background: #222; color: #aaa; font-size: 11px; cursor: pointer;
          transition: background 0.1s, color 0.1s; }
.tp-btn:hover  { background: #2a2a2a; color: #ddd; }
.tp-btn.active { background: #1e3040; border-color: #2a5070; color: #7aabff; }

/* ---- Review action buttons ---- */
.review-actions { display: flex; gap: 6px; margin-top: 8px; }
.btn-accept { flex: 1; padding: 5px 8px; background: #1a3520; border: 1px solid #2a5030;
              border-radius: 4px; color: #5aaf6a; font-size: 11px; cursor: pointer;
              transition: background 0.1s; }
.btn-accept:hover { background: #1e4028; }
.btn-delete { flex: 1; padding: 5px 8px; background: #3a1a1a; border: 1px solid #602020;
              border-radius: 4px; color: #cf6060; font-size: 11px; cursor: pointer;
              transition: background 0.1s; }
.btn-delete:hover { background: #4a2020; }
.btn-accept:disabled,.btn-delete:disabled { opacity: 0.4; cursor: default; }
#reload-btn { padding: 7px 14px; background: #1e2e40; border: 1px solid #2a4060;
              border-radius: 6px; color: #7aabff; font-size: 12px; cursor: pointer;
              white-space: nowrap; display: none; transition: background 0.1s; }
#reload-btn:hover { background: #263848; }

/* ---- Filters ---- */
#filters { display: flex; align-items: center; gap: 10px;
           padding: 5px 10px; border: 1px solid #2e2e2e; border-radius: 6px;
           background: #1a1a1a; white-space: nowrap; }
#filters-sep { font-size: 10px; text-transform: uppercase; letter-spacing: 0.6px;
               color: #383838; }
.fcheck { display: flex; align-items: center; gap: 5px; font-size: 12px;
          color: #666; cursor: pointer; user-select: none; transition: color 0.1s; }
.fcheck input[type="checkbox"] { accent-color: #5b8dd9; cursor: pointer;
                                  width: 13px; height: 13px; flex-shrink: 0; }
.fcheck:hover { color: #bbb; }
.fcheck.active { color: #7aabff; }

/* ---- Category picker modal ---- */
#cat-modal { display: none; position: fixed; inset: 0; z-index: 600;
             background: rgba(0,0,0,0.75); align-items: center; justify-content: center; }
#cat-modal.open { display: flex; }
#cat-box { background: #1e1e1e; border: 1px solid #333; border-radius: 8px;
           padding: 22px; min-width: 300px; }
#cat-box h3 { font-size: 14px; font-weight: 600; color: #fff; margin-bottom: 14px; }
#cat-select { width: 100%; padding: 8px 10px; background: #2a2a2a;
              border: 1px solid #3a3a3a; border-radius: 5px; color: #e0e0e0;
              font-size: 13px; margin-bottom: 14px; }
.cat-modal-btns { display: flex; gap: 8px; justify-content: flex-end; }
#cat-confirm { padding: 7px 16px; background: #1e3a50; border: 1px solid #2a5070;
               border-radius: 5px; color: #7aabff; font-size: 12px; cursor: pointer; }
#cat-cancel  { padding: 7px 16px; background: #2a2a2a; border: 1px solid #3a3a3a;
               border-radius: 5px; color: #888; font-size: 12px; cursor: pointer; }

@media (max-width: 640px) {
  #lb-inner { flex-direction: column; max-width: 95vw; }
  #lb-img-wrap { max-width: 95vw; min-width: unset; }
  #lb-meta { width: 100%; max-height: 40vh; border-left: none;
             border-top: 1px solid #2a2a2a; }
}

/* ---- Multi-select ---- */
.card.selected { border-color: #5b8dd9 !important;
                 box-shadow: 0 0 0 2px rgba(91,141,217,0.25); }
.card.selected .card-thumb::after { content: ''; position: absolute; inset: 0;
  background: rgba(91,141,217,0.1); pointer-events: none; }
.card-cb-wrap { position: absolute; top: 6px; left: 6px; z-index: 5; line-height: 0; }
.card-cb { width: 16px; height: 16px; cursor: pointer; accent-color: #5b8dd9; }
/* ---- Selection toolbar ---- */
#sel-bar { display: none; position: fixed; bottom: 0; left: 0; right: 0; z-index: 300;
           background: #1a2438; border-top: 1px solid #2a4060; padding: 12px 24px;
           align-items: center; gap: 10px; flex-wrap: wrap; }
#sel-count { font-size: 13px; color: #7aabff; font-weight: 600; margin-right: auto; }
.btn-s { padding: 7px 14px; border-radius: 5px; font-size: 12px; cursor: pointer;
         border: 1px solid; transition: background 0.1s; white-space: nowrap; }
.btn-s-move { background: #1e3a50; border-color: #2a5070; color: #7aabff; }
.btn-s-move:hover { background: #263848; }
.btn-s-del  { background: #3a1a1a; border-color: #602020; color: #cf6060; }
.btn-s-del:hover  { background: #4a2020; }
.btn-s-all  { background: #252525; border-color: #3a3a3a; color: #aaa; }
.btn-s-all:hover  { background: #333; color: #ccc; }
.btn-s-clr  { background: #2a2a2a; border-color: #3a3a3a; color: #888; }
.btn-s-clr:hover  { background: #333; color: #bbb; }
</style>
</head>
<body>

<div id="header">
  <h1>Texture Library</h1>
  <input type="text" id="search" placeholder="Search tags, materials, colors, filenames&hellip;"
         oninput="onSearch(this.value)">
  <button id="search-clear" onclick="clearSearch()">Clear</button>
  <div id="filters">
    <span id="filters-sep">Filter</span>
    <label class="fcheck" id="lbl-pbr">
      <input type="checkbox" id="filter-pbr" onchange="onFilterPbr(this.checked)">
      PBR only
    </label>
  </div>
  <span id="count"></span>
  <button id="reload-btn" onclick="location.reload()">Reload Library</button>
</div>

<div id="tabs"></div>
<div id="grid"></div>

<div id="lightbox" onclick="onLightboxBgClick(event)">
  <div id="lb-inner">
    <button id="lb-close" onclick="closeLightbox()">&#x2715;</button>
    <div id="lb-img-wrap">
      <img id="lb-img" src="" alt="">
      <canvas id="tp-canvas"></canvas>
    </div>
    <div id="lb-meta">
      <div id="lb-name"></div>
      <div class="lb-section">
        <div class="lb-label">Source file</div>
        <div class="lb-source" id="lb-source"></div>
      </div>
      <div class="lb-section">
        <div class="lb-label">Folder path</div>
        <div class="lb-path" id="lb-path"></div>
      </div>
      <div id="lb-chips"></div>
      <div class="lb-section">
        <div class="lb-label">Tags</div>
        <div id="lb-tags"></div>
      </div>
      <button id="lb-copy-btn" onclick="copyActivePath()">Copy folder path</button>
      <button id="lb-tile-btn" onclick="toggleTilePreview()">Tile Preview</button>
      <div id="lb-tile-controls">
        <div class="lb-tile-label">Tile mode</div>
        <div id="tp-mode-btns">
          <button class="tp-btn active" id="btn-offset" onclick="setTileMode('offset')">Offset ½</button>
          <button class="tp-btn"        id="btn-grid"   onclick="setTileMode('grid')">3&#x00D7;3 Grid</button>
          <button class="tp-btn"        id="btn-seam"   onclick="toggleSeamLines()">Seam Lines</button>
          <button class="tp-btn"                        onclick="fitTileView();drawTile()">Fit</button>
        </div>
      </div>
    </div>
  </div>
</div>

<div id="cat-modal">
  <div id="cat-box">
    <h3>Move to Category</h3>
    <select id="cat-select"></select>
    <div class="cat-modal-btns">
      <button id="cat-cancel" onclick="closeCatModal()">Cancel</button>
      <button id="cat-confirm" onclick="confirmAccept()">Accept</button>
    </div>
  </div>
</div>


<div id="sel-bar">
  <span id="sel-count"></span>
  <button class="btn-s btn-s-all" onclick="selectAllVisible()">Select all visible</button>
  <button class="btn-s btn-s-move" onclick="openBulkMoveModal()">Move to&hellip;</button>
  <button class="btn-s btn-s-del"  onclick="bulkDelete()">Delete selected</button>
  <button class="btn-s btn-s-clr"  onclick="clearSelection()">Clear</button>
</div>

<script>
// Injected by generate_preview.py
const DATA = /*TEXTURE_DATA*/;
const SERVER_MODE = false;
const CATEGORIES = [];

// ---- State ----
let currentCat    = (DATA[0] || [])[0] || "";
let searchQuery   = "";
let activePath    = "";
let activeItem    = null;
let _changeCount  = 0;
let filterPbrOnly = false;
let _selected      = new Set();
let _selectedItems = new Map();
const _rIC = window.requestIdleCallback
  ? (fn) => window.requestIdleCallback(fn, { timeout: 500 })
  : (fn) => setTimeout(fn, 0);
let _renderToken = 0;
let _searchTimer = null;
function selKey(item) { return item.folder_path + '|' + (item.source_file || ''); }

// ---- PBR filter ----
// A texture is PBR if it has at least one non-base map (normal, roughness, etc.)
function isPbr(item) {
  return Array.isArray(item.maps) &&
    item.maps.some(m => m && m !== "base" && m !== "unknown");
}
function onFilterPbr(checked) {
  filterPbrOnly = checked;
  document.getElementById("lbl-pbr").classList.toggle("active", checked);
  renderTabs();
  renderGrid();
}

function markChanged() {
  _changeCount++;
  const btn = document.getElementById("reload-btn");
  if (btn) {
    btn.style.display = "inline-block";
    btn.textContent   = "Reload Library (" + _changeCount + " change" +
                        (_changeCount === 1 ? "" : "s") + ")";
  }
}

function removeItemFromData(item) {
  for (let i = 0; i < DATA.length; i++) {
    const [catName, items] = DATA[i];
    const idx = items.findIndex(
      it => it.folder_path === item.folder_path && it.source_file === item.source_file
    );
    if (idx !== -1) {
      items.splice(idx, 1);
      if (items.length === 0) {
        DATA.splice(i, 1);
        if (currentCat === catName) {
          currentCat = (DATA[0] || [])[0] || "";
        }
      }
      break;
    }
  }
  _invalidateCache();
  renderTabs();
  renderGrid();
}

function isReview(cat) { return cat.startsWith("Review:"); }
function isBin(cat)    { return cat.startsWith("Bin:"); }

let _allItemsCache = null;
function allItems() {
  if (!_allItemsCache) _allItemsCache = DATA.flatMap(([, items]) => items);
  return _allItemsCache;
}
function _invalidateCache() { _allItemsCache = null; }

function catItems(cat) {
  const e = DATA.find(([n]) => n === cat);
  return e ? e[1] : [];
}

function matches(item, q) {
  if (filterPbrOnly && !isPbr(item)) return false;
  if (!q) return true;
  const hay = [
    ...(item.tags || []),
    item.material || "", item.material_type || "",
    item.color || "", item.name || "",
    item.source_file || "", item.category || "",
  ].join(" ").toLowerCase();
  return q.toLowerCase().split(/\s+/).filter(Boolean).every(w => hay.includes(w));
}

// ---- Tabs ----
function renderTabs() {
  const el = document.getElementById("tabs");
  el.innerHTML = "";

  if (searchQuery) {
    const n = allItems().filter(i => matches(i, searchQuery)).length;
    el.appendChild(makeTab("All Results", n, true, false));
    return;
  }

  DATA.forEach(([name, items]) => {
    const count = filterPbrOnly ? items.filter(isPbr).length : items.length;
    el.appendChild(makeTab(name, count, name === currentCat, isReview(name), isBin(name)));
  });
}

function makeTab(name, count, active, review, bin) {
  const d = document.createElement("div");
  d.className = "tab" + (active ? " active" : "") + (review ? " review-tab" : "") + (bin ? " bin-tab" : "");
  d.textContent = name;
  const c = document.createElement("span");
  c.className = "tab-count";
  c.textContent = count;
  d.appendChild(c);
  if (!searchQuery) d.onclick = () => selectCat(name);
  return d;
}

// ---- Grid ----
function renderGrid() {
  const token = ++_renderToken;
  const grid = document.getElementById("grid");
  grid.innerHTML = "";

  const items = searchQuery
    ? allItems().filter(i => matches(i, searchQuery))
    : filterPbrOnly ? catItems(currentCat).filter(isPbr) : catItems(currentCat);

  const n = items.length;
  document.getElementById("count").textContent =
    n === 0 ? "No results" : n === 1 ? "1 texture" : n + " textures";

  if (n === 0) {
    const msg = document.createElement("div");
    msg.id = "no-results";
    msg.textContent = (searchQuery || filterPbrOnly)
      ? "No textures match your filters."
      : "No textures in this category.";
    grid.appendChild(msg);
    return;
  }

  const BATCH = 60;
  const frag = document.createDocumentFragment();
  const first = Math.min(BATCH, n);
  for (let i = 0; i < first; i++) frag.appendChild(makeCard(items[i]));
  grid.appendChild(frag);

  if (n <= BATCH) return;

  function appendBatch(start) {
    if (token !== _renderToken) return; // a newer render started; abort
    const f = document.createDocumentFragment();
    const end = Math.min(start + BATCH, n);
    for (let i = start; i < end; i++) f.appendChild(makeCard(items[i]));
    grid.appendChild(f);
    if (end < n) _rIC(() => appendBatch(end));
  }
  _rIC(() => appendBatch(first));
}

// ---- Card ----
function makeCard(item) {
  const card = document.createElement("div");
  card.className = "card"
    + (item.is_review && !isBin(item.category) ? " review-card" : "")
    + (isBin(item.category) ? " bin-card" : "");
  if (_selected.has(selKey(item))) card.classList.add("selected");

  // Thumbnail
  const thumb = document.createElement("div");
  thumb.className = "card-thumb";
  thumb.onclick = () => openLightbox(item);
  // Selection checkbox
  const cbWrap = document.createElement('label');
  cbWrap.className = 'card-cb-wrap';
  cbWrap.onclick = e => e.stopPropagation();
  const cb = document.createElement('input');
  cb.type = 'checkbox'; cb.className = 'card-cb';
  const _k = selKey(item);
  cb.checked = _selected.has(_k);
  cb.onchange = () => {
    if (cb.checked) { _selected.add(_k); _selectedItems.set(_k, item); }
    else            { _selected.delete(_k); _selectedItems.delete(_k); }
    card.classList.toggle('selected', cb.checked);
    updateSelectionBar();
  };
  cbWrap.appendChild(cb);
  thumb.appendChild(cbWrap);
  if (item.thumb) {
    const img = document.createElement("img");
    img.src = item.thumb;
    img.alt = item.name;
    img.loading = "lazy";
    thumb.appendChild(img);
  } else {
    const nt = document.createElement("div");
    nt.className = "no-thumb";
    nt.textContent = item.source_file ? item.source_file.split(".").pop().toUpperCase() : "No preview";
    thumb.appendChild(nt);
  }
  card.appendChild(thumb);

  const body = document.createElement("div");
  body.className = "card-body";

  // Name + copy
  const nameEl = document.createElement("div");
  nameEl.className = "card-name";
  nameEl.textContent = item.name;
  const confirm = document.createElement("span");
  confirm.className = "copy-confirm";
  confirm.textContent = "Copied!";
  nameEl.appendChild(confirm);
  nameEl.onclick = e => {
    e.stopPropagation();
    navigator.clipboard.writeText(item.folder_path).then(() => {
      confirm.style.display = "inline";
      setTimeout(() => { confirm.style.display = "none"; }, 1500);
    });
  };
  body.appendChild(nameEl);

  // Debug: source filename
  if (item.source_file) {
    const db = document.createElement("div");
    db.className = "debug-block";
    const lbl = document.createElement("div");
    lbl.className = "debug-label";
    lbl.textContent = "Source";
    const val = document.createElement("div");
    val.className = "debug-value";
    val.textContent = item.source_file;
    db.appendChild(lbl);
    db.appendChild(val);
    body.appendChild(db);
  }

  // Dimensions: pixel size + real-world estimate
  const hasPx    = item.px_w && item.px_h;
  const hasSzEst = item.size_est && item.size_est !== "unknown" && item.size_est !== "";
  if (hasPx || hasSzEst) {
    const db2 = document.createElement("div");
    db2.className = "debug-block";
    const lbl2 = document.createElement("div");
    lbl2.className = "debug-label";
    lbl2.textContent = "Dimensions";
    db2.appendChild(lbl2);
    if (hasPx) {
      const pxVal = document.createElement("div");
      pxVal.className = "debug-value";
      pxVal.textContent = `${item.px_w} × ${item.px_h} px`;
      db2.appendChild(pxVal);
    }
    if (hasSzEst) {
      const szVal = document.createElement("div");
      szVal.className = "debug-value";
      szVal.textContent = item.size_est;
      db2.appendChild(szVal);
    }
    body.appendChild(db2);
  }

  // PBR map chips
  if (item.maps && item.maps.length > 0) {
    const chips = document.createElement("div");
    chips.className = "chips";
    item.maps.forEach(m => {
      const c = document.createElement("span");
      c.className = "chip chip-map" + (m === "base" ? " base" : "");
      c.textContent = m === "base" ? (item.maps.length === 1 ? "DIFF" : "ALBEDO") : m;
      chips.appendChild(c);
    });
    body.appendChild(chips);
  }

  // Review / bin reason badge
  if (item.is_review && item.review_reason) {
    const chips = document.createElement("div");
    chips.className = "chips";
    const c = document.createElement("span");
    c.className = "chip " + (isBin(item.category) ? "chip-bin" : "chip-review");
    c.textContent = item.review_reason.replace(/_/g, " ").toUpperCase();
    chips.appendChild(c);
    body.appendChild(chips);
  }

  // Tag chips
  if (item.tags && item.tags.length > 0) {
    const tags = document.createElement("div");
    tags.className = "chips";
    item.tags.slice(0, 8).forEach(t => {
      const c = document.createElement("span");
      c.className = "chip chip-tag";
      c.textContent = t.replace(/_/g, " ");
      c.onclick = e => { e.stopPropagation(); setSearch(t.replace(/_/g, " ")); };
      tags.appendChild(c);
    });
    body.appendChild(tags);
  }

  // Review action buttons (server mode only)
  if (SERVER_MODE && item.is_review) {
    const actions = document.createElement("div");
    actions.className = "review-actions";
    const acceptBtn = document.createElement("button");
    acceptBtn.className = "btn-accept";
    acceptBtn.textContent = "Accept";
    acceptBtn.onclick = e => { e.stopPropagation(); openAcceptModal(item, acceptBtn); };
    const delBtn = document.createElement("button");
    delBtn.className = "btn-delete";
    delBtn.textContent = "Delete";
    delBtn.onclick = e => { e.stopPropagation(); deleteItem(item, delBtn); };
    actions.appendChild(acceptBtn);
    actions.appendChild(delBtn);
    body.appendChild(actions);
  }

  card.appendChild(body);
  return card;
}

// ---- Lightbox ----
function openLightbox(item) {
  activePath = item.folder_path;
  activeItem = item;
  document.getElementById("lb-img").src = item.base_img || "";
  document.getElementById("lb-name").textContent = item.name;
  document.getElementById("lb-source").textContent = item.source_file || "";
  document.getElementById("lb-path").textContent   = item.folder_path;

  const chips = document.getElementById("lb-chips");
  chips.innerHTML = "";
  (item.maps || []).forEach(m => {
    const c = document.createElement("span");
    c.className = "chip chip-map" + (m === "base" ? " base" : "");
    c.textContent = m === "base" ? "ALBEDO" : m;
    chips.appendChild(c);
  });

  const tags = document.getElementById("lb-tags");
  tags.innerHTML = "";
  (item.tags || []).forEach(t => {
    const c = document.createElement("span");
    c.className = "chip chip-tag";
    c.textContent = t.replace(/_/g, " ");
    c.onclick = () => { closeLightbox(); setSearch(t.replace(/_/g, " ")); };
    tags.appendChild(c);
  });

  document.getElementById("lightbox").classList.add("open");
}

function closeLightbox() {
  if (tpImg) closeTilePreview();
  document.getElementById("lightbox").classList.remove("open");
  document.getElementById("lb-img").src = "";
}

function onLightboxBgClick(e) {
  if (e.target === document.getElementById("lightbox")) closeLightbox();
}

function copyActivePath() {
  navigator.clipboard.writeText(activePath).then(() => {
    const btn = document.getElementById("lb-copy-btn");
    const orig = btn.textContent;
    btn.textContent = "Copied!";
    setTimeout(() => { btn.textContent = orig; }, 1500);
  });
}

// ---- Search ----
function onSearch(val) {
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(() => {
    searchQuery = val.trim();
    renderTabs();
    renderGrid();
  }, 250);
}

function clearSearch() {
  clearTimeout(_searchTimer);
  document.getElementById("search").value = "";
  searchQuery = "";
  renderTabs();
  renderGrid();
}

function setSearch(term) {
  document.getElementById("search").value = term;
  onSearch(term);
}

function selectCat(name) {
  currentCat  = name;
  searchQuery = "";
  document.getElementById("search").value = "";
  renderTabs();
  renderGrid();
}


// ---- Selection ----
function updateSelectionBar() {
  const bar = document.getElementById("sel-bar");
  const n   = _selected.size;
  if (n === 0) {
    bar.style.display = "none";
    document.getElementById("grid").style.paddingBottom = "20px";
  } else {
    bar.style.display = "flex";
    document.getElementById("grid").style.paddingBottom = "80px";
    document.getElementById("sel-count").textContent =
      n + " item" + (n === 1 ? "" : "s") + " selected";
  }
}

function selectAllVisible() {
  const items = searchQuery
    ? allItems().filter(i => matches(i, searchQuery))
    : filterPbrOnly ? catItems(currentCat).filter(isPbr) : catItems(currentCat);
  items.forEach(item => {
    const k = selKey(item);
    _selected.add(k);
    _selectedItems.set(k, item);
  });
  document.querySelectorAll(".card-cb").forEach(cb => { cb.checked = true; });
  document.querySelectorAll(".card").forEach(c => c.classList.add("selected"));
  updateSelectionBar();
}

function clearSelection() {
  _selected.clear();
  _selectedItems.clear();
  document.querySelectorAll(".card-cb").forEach(cb => { cb.checked = false; });
  document.querySelectorAll(".card").forEach(c => c.classList.remove("selected"));
  updateSelectionBar();
}

function openBulkMoveModal() {
  if (_selected.size === 0) return;
  _bulkMode = true;
  const sel = document.getElementById("cat-select");
  sel.innerHTML = "";
  CATEGORIES.forEach(c => {
    const opt = document.createElement("option");
    opt.value = c; opt.textContent = c;
    sel.appendChild(opt);
  });
  document.getElementById("cat-box").querySelector("h3").textContent =
    "Move " + _selected.size + " item" + (_selected.size === 1 ? "" : "s") + " to Category";
  document.getElementById("cat-modal").classList.add("open");
}

function _itemPayload(item) {
  return {
    item_type:   (item.review_reason === "misc" || !item.is_review) ? "misc" : "raw",
    folder_path: item.folder_path,
    source_file: item.source_file || "",
    name:        item.name,
  };
}

function confirmBulkAccept() {
  const cat   = document.getElementById("cat-select").value;
  closeCatModal();
  const items = Array.from(_selectedItems.values());
  const bar   = document.getElementById("sel-bar");
  bar.style.opacity = "0.5";
  fetch("/api/bulk-accept", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: items.map(_itemPayload), target_category: cat }),
  })
  .then(r => r.json())
  .then(d => {
    bar.style.opacity = "1";
    if (d.ok) {
      d.results.filter(r => r.ok).forEach(r => {
        const item = items.find(i => i.name === r.item);
        if (item) removeItemFromData(item);
      });
      const failed = d.results.filter(r => !r.ok);
      clearSelection();
      markChanged();
      if (failed.length) alert(failed.length + " item(s) failed:\n" +
        failed.map(r => r.item + ": " + r.error).join("\n"));
    } else { alert("Bulk move failed: " + (d.error || "Unknown")); }
  })
  .catch(err => { bar.style.opacity = "1"; alert("Request failed: " + err.message); });
}

function bulkDelete() {
  const n = _selected.size;
  if (n === 0) return;
  if (!confirm("Move " + n + " item" + (n === 1 ? "" : "s") + " to the recycle bin?")) return;
  const items = Array.from(_selectedItems.values());
  const bar   = document.getElementById("sel-bar");
  bar.style.opacity = "0.5";
  fetch("/api/bulk-delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: items.map(_itemPayload) }),
  })
  .then(r => r.json())
  .then(d => {
    bar.style.opacity = "1";
    if (d.ok) {
      d.results.filter(r => r.ok).forEach(r => {
        const item = items.find(i => i.name === r.item);
        if (item) removeItemFromData(item);
      });
      const failed = d.results.filter(r => !r.ok);
      clearSelection();
      markChanged();
      if (failed.length) alert(failed.length + " item(s) failed:\n" +
        failed.map(r => r.item + ": " + r.error).join("\n"));
    } else { alert("Bulk delete failed: " + (d.error || "Unknown")); }
  })
  .catch(err => { bar.style.opacity = "1"; alert("Request failed: " + err.message); });
}

// ---- Server mode review actions ----
let _pendingItem = null;
let _pendingAcceptBtn = null;
let _bulkMode    = false;

function openAcceptModal(item, btn) {
  _pendingItem = item;
  _pendingAcceptBtn = btn;
  const sel = document.getElementById("cat-select");
  sel.innerHTML = "";
  CATEGORIES.forEach(c => {
    const opt = document.createElement("option");
    opt.value = c; opt.textContent = c;
    sel.appendChild(opt);
  });
  document.getElementById("cat-modal").classList.add("open");
}

function closeCatModal() {
  document.getElementById("cat-modal").classList.remove("open");
  _pendingItem = null; _pendingAcceptBtn = null;
  _bulkMode = false;
  document.getElementById("cat-box").querySelector("h3").textContent = "Move to Category";
}

function confirmAccept() {
  if (_bulkMode) { confirmBulkAccept(); return; }
  if (!_pendingItem) return;
  const cat = document.getElementById("cat-select").value;
  const item = _pendingItem;
  const btn = _pendingAcceptBtn;
  closeCatModal();
  if (btn) { btn.disabled = true; btn.textContent = "Processing…"; }
  fetch("/api/accept", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_type:       item.review_reason === "misc" ? "misc" : "raw",
      folder_path:     item.folder_path,
      source_file:     item.source_file,
      target_category: cat,
    }),
  })
  .then(r => r.json())
  .then(d => {
    if (d.ok) {
      if (btn) btn.textContent = "Accepted!";
      setTimeout(() => { removeItemFromData(item); markChanged(); }, 600);
    } else {
      if (btn) { btn.disabled = false; btn.textContent = "Accept"; }
      alert("Error: " + (d.error || "Unknown"));
    }
  })
  .catch(err => {
    if (btn) { btn.disabled = false; btn.textContent = "Accept"; }
    alert("Request failed: " + err.message);
  });
}

function deleteItem(item, btn) {
  const label = item.source_file || item.name;
  if (!confirm("Move “" + label + "” to the recycle bin?")) return;
  btn.disabled = true; btn.textContent = "Deleting…";
  fetch("/api/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_type:   item.review_reason === "misc" ? "misc" : "raw",
      folder_path: item.folder_path,
      source_file: item.source_file,
    }),
  })
  .then(r => r.json())
  .then(d => {
    if (d.ok) {
      btn.textContent = "Deleted";
      setTimeout(() => { removeItemFromData(item); markChanged(); }, 600);
    } else {
      btn.disabled = false; btn.textContent = "Delete";
      alert("Error: " + (d.error || "Unknown"));
    }
  })
  .catch(err => {
    btn.disabled = false; btn.textContent = "Delete";
    alert("Request failed: " + err.message);
  });
}

// ---- Keyboard ----
document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeLightbox();
  if (e.key === "/" && document.activeElement !== document.getElementById("search")) {
    e.preventDefault();
    document.getElementById("search").focus();
  }
});

// ---- Init ----
renderTabs();
renderGrid();

// ---- Tile Preview (in-lightbox) ----------------------------------------
let tileMode  = 'offset';   // 'offset' | 'grid'
let seamLines = false;
let tpZoom = 1.0, tpOffsetX = 0, tpOffsetY = 0;
let tpDragging = false, tpDragLast = {x: 0, y: 0};
let tpImg = null;

function toggleTilePreview() {
  tpImg ? closeTilePreview() : openTilePreview();
}

function openTilePreview() {
  if (!activeItem || !activeItem.base_img) return;

  // Size canvas to the current image-wrap area before swapping content
  const wrap   = document.getElementById('lb-img-wrap');
  const canvas = document.getElementById('tp-canvas');
  canvas.width  = wrap.offsetWidth;
  canvas.height = wrap.offsetHeight;

  // Swap: hide static image, show canvas
  document.getElementById('lb-img').style.display = 'none';
  canvas.style.display = 'block';

  // Show mode controls; flip button label
  document.getElementById('lb-tile-controls').style.display = 'block';
  document.getElementById('lb-tile-btn').textContent = '↩ Back to Image';

  // Reset mode state
  tileMode  = 'offset';
  seamLines = false;
  document.getElementById('btn-offset').classList.add('active');
  document.getElementById('btn-grid').classList.remove('active');
  document.getElementById('btn-seam').classList.remove('active');

  // Load image -> auto-fit zoom -> draw
  tpImg = new Image();
  tpImg.onload = function () { fitTileView(); drawTile(); };
  tpImg.src = activeItem.base_img;
}

function closeTilePreview() {
  document.getElementById('tp-canvas').style.display = 'none';
  document.getElementById('lb-img').style.display    = '';
  document.getElementById('lb-tile-controls').style.display = 'none';
  document.getElementById('lb-tile-btn').textContent = 'Tile Preview';
  tpImg = null;
}

function fitTileView() {
  // Zoom so the full tiled extent fills the canvas with a small margin.
  // Offset 1/2 shows 2x2 area (seam at centre); 3x3 Grid shows 3x3 tiles.
  if (!tpImg) return;
  const canvas = document.getElementById('tp-canvas');
  const cw = canvas.width, ch = canvas.height;
  const iw = tpImg.naturalWidth, ih = tpImg.naturalHeight;
  if (!iw || !ih) return;
  const span = (tileMode === 'offset') ? 2 : 3;
  tpOffsetX = 0;
  tpOffsetY = 0;
  tpZoom    = Math.min(cw / (span * iw), ch / (span * ih)) * 0.92;
}

function drawTile() {
  if (!tpImg) return;
  const canvas = document.getElementById('tp-canvas');
  const ctx    = canvas.getContext('2d');
  const cw = canvas.width, ch = canvas.height;

  const iw = tpImg.naturalWidth, ih = tpImg.naturalHeight;
  if (!iw || !ih) return;

  ctx.save();
  ctx.translate(cw / 2 + tpOffsetX, ch / 2 + tpOffsetY);
  ctx.scale(tpZoom, tpZoom);

  if (tileMode === 'offset') {
    // Tile origin at canvas-centre → four tile corners meet at the crosshair.
    // Seams appear at the centre, making any mismatch immediately obvious.
    const cols = Math.ceil(cw / tpZoom / iw / 2) + 2;
    const rows = Math.ceil(ch / tpZoom / ih / 2) + 2;
    for (let c = -cols; c <= cols; c++)
      for (let r = -rows; r <= rows; r++)
        ctx.drawImage(tpImg, c * iw, r * ih);
  } else {
    // 3×3 grid: centre tile centred on the canvas; seams between tiles.
    for (let c = -1; c <= 1; c++)
      for (let r = -1; r <= 1; r++)
        ctx.drawImage(tpImg, c * iw - iw / 2, r * ih - ih / 2);
  }

  if (seamLines) {
    ctx.strokeStyle = 'rgba(255,80,80,0.75)';
    ctx.lineWidth   = 2 / tpZoom;
    const range = Math.ceil(Math.max(cw, ch) / tpZoom / Math.min(iw, ih)) + 3;
    const bigD  = (Math.max(cw, ch) / tpZoom + Math.max(iw, ih)) * 2;
    // Seam positions differ by mode: offset → at multiples of iw; grid → offset by ½
    const ox = (tileMode === 'offset') ? 0 : -iw / 2;
    const oy = (tileMode === 'offset') ? 0 : -ih / 2;
    for (let i = -range; i <= range; i++) {
      ctx.beginPath(); ctx.moveTo(i * iw + ox, -bigD); ctx.lineTo(i * iw + ox, bigD); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(-bigD, i * ih + oy); ctx.lineTo(bigD, i * ih + oy); ctx.stroke();
    }
  }
  ctx.restore();
}

function setTileMode(mode) {
  tileMode = mode;
  document.getElementById('btn-offset').classList.toggle('active', mode === 'offset');
  document.getElementById('btn-grid').classList.toggle('active',   mode === 'grid');
  fitTileView();   // re-zoom to fit the new extent before redrawing
  drawTile();
}

function toggleSeamLines() {
  seamLines = !seamLines;
  document.getElementById('btn-seam').classList.toggle('active', seamLines);
  drawTile();
}

function resetTpZoom() {
  tpZoom = 1; tpOffsetX = 0; tpOffsetY = 0;
  drawTile();
}

// Zoom toward cursor + click-drag pan on the in-lightbox canvas
(function () {
  const canvas = document.getElementById('tp-canvas');

  canvas.addEventListener('wheel', function (e) {
    if (!tpImg) return;
    e.preventDefault();
    const factor  = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const rect    = canvas.getBoundingClientRect();
    const mx      = e.clientX - rect.left - canvas.width  / 2;
    const my      = e.clientY - rect.top  - canvas.height / 2;
    const newZoom = Math.min(12, Math.max(0.05, tpZoom * factor));
    tpOffsetX = mx - (mx - tpOffsetX) * (newZoom / tpZoom);
    tpOffsetY = my - (my - tpOffsetY) * (newZoom / tpZoom);
    tpZoom    = newZoom;
    drawTile();
  }, { passive: false });

  canvas.addEventListener('mousedown', function (e) {
    if (!tpImg) return;
    tpDragging = true;
    tpDragLast = { x: e.clientX, y: e.clientY };
    canvas.style.cursor = 'grabbing';
  });
  window.addEventListener('mousemove', function (e) {
    if (!tpDragging) return;
    tpOffsetX += e.clientX - tpDragLast.x;
    tpOffsetY += e.clientY - tpDragLast.y;
    tpDragLast = { x: e.clientX, y: e.clientY };
    drawTile();
  });
  window.addEventListener('mouseup', function () {
    if (tpDragging) {
      tpDragging = false;
      if (tpImg) canvas.style.cursor = 'crosshair';
    }
  });
})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def build_html(all_categories: dict) -> str:
    data_list = list(all_categories.items())
    data_json = json.dumps(data_list, ensure_ascii=False, separators=(",", ":"))
    return HTML_TEMPLATE.replace("/*TEXTURE_DATA*/", data_json)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate library_preview.html from a texture pipeline output folder."
    )
    parser.add_argument(
        "--output", required=True,
        help='Path to the pipeline output folder (e.g. "D:\\...\\Texture Library Test\\_output")',
    )
    args   = parser.parse_args()
    output_dir = Path(args.output)

    if not output_dir.is_dir():
        sys.exit(f"ERROR: Output folder not found: {output_dir}")

    thumb_dir = output_dir / THUMB_DIR
    thumb_dir.mkdir(exist_ok=True)

    print(f"Output folder : {output_dir}")
    print(f"Thumbnails    : {thumb_dir}")
    print()

    print("Scanning organised textures...")
    categories = scan_output(output_dir, thumb_dir)
    total = sum(len(v) for v in categories.values())
    print(f"  {total} textures across {len(categories)} categories.")

    print("Scanning _needs_review (debug)...")
    review_cats = scan_needs_review(output_dir, thumb_dir)
    review_total = sum(len(v) for v in review_cats.values())
    if review_total:
        print(f"  {review_total} review items across {len(review_cats)} review folders.")
    else:
        print("  No _needs_review folder found or it is empty.")

    print("Scanning _recycle_bin (debug)...")
    bin_cats = scan_recycle_bin(output_dir, thumb_dir)
    bin_total = sum(len(v) for v in bin_cats.values())
    if bin_total:
        print(f"  {bin_total} binned items across {len(bin_cats)} bin folders.")
    else:
        print("  No _recycle_bin folder found or it is empty.")

    all_cats = {**categories, **review_cats, **bin_cats}

    if not all_cats:
        sys.exit("Nothing to preview. Has the pipeline run against this output folder?")

    print()
    print("Building HTML...")
    html      = build_html(all_cats)
    html_path = output_dir / HTML_FILENAME
    html_path.write_text(html, encoding="utf-8")

    size_kb = html_path.stat().st_size // 1024
    print(f"  Written: {html_path}  ({size_kb} KB)")
    print()
    print("Open in your browser:")
    print(f"  {html_path}")


if __name__ == "__main__":
    main()
