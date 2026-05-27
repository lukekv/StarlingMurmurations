#!/usr/bin/env python3
"""
generate_texture_library.py
---------------------------
Generates a self-contained HTML browser for the final organised texture library.

Scans only the sorted, accepted texture groups (category subfolders with JSON sidecars).
Generates 256px JPEG thumbnails and builds a single-file HTML viewer with:
  - Category tabs matching the output folder structure
  - Tag/material/color/name search with instant filtering
  - "PBR only" filter toggle
  - "Search in category" filter toggle (confines search to the active tab)
  - Thumbnail grid with lightbox full-size preview
  - Click texture name to copy Windows folder path to clipboard

_needs_review and _recycle_bin folders are intentionally ignored.
For debug mode (includes those folders and multi-select), use generate_preview.py.

Usage:
    python generate_texture_library.py --output "Y:\\_Shared Asset Library\\_MHOA Texture Library"

    # Place the HTML outside the output folder:
    python generate_texture_library.py ^
        --output "Y:\\_Shared Asset Library\\_MHOA Texture Library" ^
        --html-out "Y:\\_Shared Asset Library\\_MHOA Texture Library.html"

Dependencies: Pillow (already in requirements.txt)
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required: pip install pillow --break-system-packages")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

THUMB_SIZE    = 256
THUMB_DIR     = "_thumbnails"
HTML_FILENAME = "texture_library.html"
IMAGE_EXTS    = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

_BASE_MAP_KEYS = {
    "albedo", "base_color", "basecolor", "diffuse", "diff",
    "color", "col", "bc", "d", "texture", "base", "unknown",
}


# ---------------------------------------------------------------------------
# Thumbnail generation
# ---------------------------------------------------------------------------

def make_thumbnail(src: Path, thumb_dir: Path) -> str | None:
    """
    Generate a 256px JPEG thumbnail and return its relative path from
    the output root (e.g. '_thumbnails/Wood_Cedar_01.jpg').
    Returns None on failure. Skips generation if the thumbnail already exists.
    """
    safe_stem  = src.stem.replace(" ", "_")
    thumb_name = f"{safe_stem}.jpg"
    thumb_path = thumb_dir / thumb_name

    if not thumb_path.exists():
        try:
            img = Image.open(src).convert("RGB")
            img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
            img.save(thumb_path, "JPEG", quality=75, optimize=True)
        except Exception as exc:
            print(f"  WARNING: thumbnail failed for {src.name}: {exc}")
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


# Conversion factors: all units -> inches
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

        return f"{_fmt(wi)} x {_fmt(hi)} in"
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Data record builder
# ---------------------------------------------------------------------------

def load_texture(
    texture_dir: Path,
    thumb_dir: Path,
    output_dir: Path,
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

    thumb_rel = make_thumbnail(base_map, thumb_dir) if base_map else None

    base_rel = (
        str(base_map.relative_to(output_dir)).replace("\\", "/")
        if base_map else None
    )

    px_w, px_h = get_image_size(base_map) if base_map else (None, None)

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
<title>MHOA Texture Library</title>
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
.tab-count { display: inline-block; background: #2a2a2a; border-radius: 9px;
             padding: 1px 6px; font-size: 10px; margin-left: 4px; color: #666; }
.tab.active .tab-count { background: #1e2e45; color: #7aabff; }

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

.card-thumb { width: 100%; aspect-ratio: 1; overflow: hidden;
              background: #191919; cursor: pointer; }
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
.copy-confirm { font-size: 10px; color: #5b9; margin-left: 4px; display: none; }

.meta-block { margin-bottom: 5px; }
.meta-label { font-size: 9px; text-transform: uppercase; letter-spacing: 0.6px;
              color: #484848; margin-bottom: 1px; }
.meta-value { font-size: 10px; color: #888; word-break: break-all; line-height: 1.3; }

.chips { display: flex; flex-wrap: wrap; gap: 3px; margin-bottom: 5px; }
.chip { font-size: 10px; padding: 2px 6px; border-radius: 3px;
        white-space: nowrap; line-height: 1.4; }
.chip-map { background: #162035; color: #6a9adf; }
.chip-map.base { background: #162518; color: #5aaf6a; }
.chip-tag { background: #252525; color: #888; border: 1px solid #333; cursor: pointer; }
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
.lb-path { font-size: 10px; color: #666; word-break: break-all; line-height: 1.4;
           font-family: 'Consolas', 'Courier New', monospace; }
#lb-chips { display: flex; flex-wrap: wrap; gap: 3px; margin-bottom: 8px; }
#lb-tags  { display: flex; flex-wrap: wrap; gap: 3px; }
#lb-copy-btn { margin-top: 14px; width: 100%; padding: 8px;
               background: #1e2e40; border: 1px solid #2a4060;
               border-radius: 5px; color: #7aabff; font-size: 12px;
               cursor: pointer; transition: background 0.1s; }
#lb-copy-btn:hover { background: #263848; }

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

@media (max-width: 640px) {
  #lb-inner { flex-direction: column; max-width: 95vw; }
  #lb-img-wrap { max-width: 95vw; min-width: unset; }
  #lb-meta { width: 100%; max-height: 40vh; border-left: none;
             border-top: 1px solid #2a2a2a; }
}
</style>
</head>
<body>

<div id="header">
  <h1>MHOA Texture Library</h1>
  <input type="text" id="search" placeholder="Search tags, materials, colors, filenames&hellip;"
         oninput="onSearch(this.value)">
  <button id="search-clear" onclick="clearSearch()">Clear</button>
  <div id="filters">
    <span id="filters-sep">Filter</span>
    <label class="fcheck" id="lbl-pbr">
      <input type="checkbox" id="filter-pbr" onchange="onFilterPbr(this.checked)">
      PBR only
    </label>
    <label class="fcheck" id="lbl-cat">
      <input type="checkbox" id="filter-cat" onchange="onFilterCategory(this.checked)">
      Search in category
    </label>
  </div>
  <span id="count"></span>
</div>

<div id="tabs"></div>
<div id="grid"></div>

<div id="lightbox" onclick="onLightboxBgClick(event)">
  <div id="lb-inner">
    <button id="lb-close" onclick="closeLightbox()">&#x2715;</button>
    <div id="lb-img-wrap">
      <img id="lb-img" src="" alt="">
    </div>
    <div id="lb-meta">
      <div id="lb-name"></div>
      <div class="lb-section">
        <div class="lb-label">Category</div>
        <div class="lb-value" id="lb-category"></div>
      </div>
      <div class="lb-section">
        <div class="lb-label">Material</div>
        <div class="lb-value" id="lb-material"></div>
      </div>
      <div class="lb-section">
        <div class="lb-label">Color</div>
        <div class="lb-value" id="lb-color"></div>
      </div>
      <div class="lb-section">
        <div class="lb-label">Dimensions</div>
        <div class="lb-value" id="lb-dims"></div>
      </div>
      <div id="lb-chips"></div>
      <div class="lb-section">
        <div class="lb-label">Tags</div>
        <div id="lb-tags"></div>
      </div>
      <div class="lb-section">
        <div class="lb-label">Folder path</div>
        <div class="lb-path" id="lb-path"></div>
      </div>
      <button id="lb-copy-btn" onclick="copyActivePath()">Copy folder path</button>
    </div>
  </div>
</div>

<script>
// Injected by generate_texture_library.py
const DATA = /*TEXTURE_DATA*/;

// ---- State ----
let currentCat       = (DATA[0] || [])[0] || "";
let searchQuery      = "";
let activePath       = "";
let filterPbrOnly    = false;
let searchInCategory = false;

// ---- PBR filter ----
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

// ---- Category filter ----
function onFilterCategory(checked) {
  searchInCategory = checked;
  document.getElementById("lbl-cat").classList.toggle("active", checked);
  renderTabs();
  renderGrid();
}

function allItems() { return DATA.flatMap(([, items]) => items); }

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

  // Global search: collapse all tabs into a single "All Results" count
  if (searchQuery && !searchInCategory) {
    const n = allItems().filter(i => matches(i, searchQuery)).length;
    const tab = makeTab("All Results", n, true);
    el.appendChild(tab);
    return;
  }

  // Per-category view: all tabs remain visible; counts reflect active filters
  DATA.forEach(([name, items]) => {
    const base  = filterPbrOnly ? items.filter(isPbr) : items;
    const count = (searchQuery && searchInCategory)
      ? base.filter(i => matches(i, searchQuery)).length
      : base.length;
    el.appendChild(makeTab(name, count, name === currentCat));
  });
}

function makeTab(name, count, active) {
  const d = document.createElement("div");
  d.className = "tab" + (active ? " active" : "");
  d.textContent = name;
  const c = document.createElement("span");
  c.className = "tab-count";
  c.textContent = count;
  d.appendChild(c);
  // Tab is only clickable when not in global-search mode
  if (!(searchQuery && !searchInCategory)) d.onclick = () => selectCat(name);
  return d;
}

// ---- Grid ----
function renderGrid() {
  const grid = document.getElementById("grid");
  grid.innerHTML = "";

  let items;
  if (searchQuery && !searchInCategory) {
    // Global search across all categories
    items = allItems().filter(i => matches(i, searchQuery));
  } else {
    // Current category, with optional within-category search
    const base = filterPbrOnly ? catItems(currentCat).filter(isPbr) : catItems(currentCat);
    items = searchQuery ? base.filter(i => matches(i, searchQuery)) : base;
  }

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

  items.forEach(item => grid.appendChild(makeCard(item)));
}

// ---- Card ----
function makeCard(item) {
  const card = document.createElement("div");
  card.className = "card";

  // Thumbnail
  const thumb = document.createElement("div");
  thumb.className = "card-thumb";
  thumb.onclick = () => openLightbox(item);
  if (item.thumb) {
    const img = document.createElement("img");
    img.src = item.thumb;
    img.alt = item.name;
    img.loading = "lazy";
    thumb.appendChild(img);
  } else {
    const nt = document.createElement("div");
    nt.className = "no-thumb";
    nt.textContent = item.source_file
      ? item.source_file.split(".").pop().toUpperCase()
      : "No preview";
    thumb.appendChild(nt);
  }
  card.appendChild(thumb);

  const body = document.createElement("div");
  body.className = "card-body";

  // Name + clipboard copy
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

  // Material + color
  const matLine = [item.material, item.material_type].filter(Boolean).join(" ");
  if (matLine || item.color) {
    const db = document.createElement("div");
    db.className = "meta-block";
    if (matLine) {
      const v = document.createElement("div");
      v.className = "meta-value";
      v.textContent = matLine;
      db.appendChild(v);
    }
    if (item.color) {
      const v = document.createElement("div");
      v.className = "meta-value";
      v.style.color = "#666";
      v.textContent = item.color;
      db.appendChild(v);
    }
    body.appendChild(db);
  }

  // Dimensions
  const hasPx    = item.px_w && item.px_h;
  const hasSzEst = item.size_est && item.size_est !== "unknown" && item.size_est !== "";
  if (hasPx || hasSzEst) {
    const db2 = document.createElement("div");
    db2.className = "meta-block";
    const lbl = document.createElement("div");
    lbl.className = "meta-label";
    lbl.textContent = "Dimensions";
    db2.appendChild(lbl);
    if (hasPx) {
      const v = document.createElement("div");
      v.className = "meta-value";
      v.textContent = `${item.px_w} x ${item.px_h} px`;
      db2.appendChild(v);
    }
    if (hasSzEst) {
      const v = document.createElement("div");
      v.className = "meta-value";
      v.textContent = item.size_est;
      db2.appendChild(v);
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

  card.appendChild(body);
  return card;
}

// ---- Lightbox ----
function openLightbox(item) {
  activePath = item.folder_path;
  document.getElementById("lb-img").src      = item.base_img || "";
  document.getElementById("lb-name").textContent     = item.name;
  document.getElementById("lb-category").textContent = item.category || "";
  document.getElementById("lb-material").textContent =
    [item.material, item.material_type].filter(Boolean).join(" ") || "";
  document.getElementById("lb-color").textContent    = item.color || "";
  document.getElementById("lb-path").textContent     = item.folder_path;

  // Dimensions line in lightbox
  const dimParts = [];
  if (item.px_w && item.px_h) dimParts.push(`${item.px_w} x ${item.px_h} px`);
  if (item.size_est && item.size_est !== "unknown") dimParts.push(item.size_est);
  document.getElementById("lb-dims").textContent = dimParts.join("  |  ");

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
  searchQuery = val.trim();
  renderTabs();
  renderGrid();
}

function clearSearch() {
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
        description="Generate a self-contained HTML viewer for the final organised texture library."
    )
    parser.add_argument(
        "--output", required=True,
        help='Path to the organised texture library folder '
             '(e.g. "Y:\\_Shared Asset Library\\_MHOA Texture Library")',
    )
    parser.add_argument(
        "--html-out",
        help=(
            "Path for the output HTML file. Defaults to texture_library.html inside --output. "
            "May be placed in a parent directory of --output; relative asset paths are "
            "adjusted automatically."
        ),
    )
    args       = parser.parse_args()
    output_dir = Path(args.output).resolve()

    if not output_dir.is_dir():
        sys.exit(f"ERROR: Output folder not found: {output_dir}")

    # Resolve HTML output path.
    html_path = Path(args.html_out).resolve() if args.html_out else output_dir / HTML_FILENAME
    html_path.parent.mkdir(parents=True, exist_ok=True)

    # If the HTML lives outside output_dir, compute the prefix that must be prepended
    # to all relative asset paths so the browser resolves thumbnails and images correctly.
    html_dir = html_path.parent
    if html_dir == output_dir:
        path_prefix = ""
    else:
        try:
            path_prefix = str(output_dir.relative_to(html_dir)).replace("\\", "/")
        except ValueError:
            sys.exit(
                f"ERROR: --html-out must be inside or in a parent directory of --output.\n"
                f"  HTML dir : {html_dir}\n"
                f"  Output   : {output_dir}"
            )

    thumb_dir = output_dir / THUMB_DIR
    thumb_dir.mkdir(exist_ok=True)

    print(f"Output folder : {output_dir}")
    print(f"Thumbnails    : {thumb_dir}")
    print(f"HTML output   : {html_path}")
    if path_prefix:
        print(f"Path prefix   : {path_prefix}/")
    print()

    print("Scanning organised textures...")
    categories = scan_output(output_dir, thumb_dir)
    total = sum(len(v) for v in categories.values())
    print(f"  {total} textures across {len(categories)} categories.")

    if not categories:
        sys.exit("No textures found. Has the pipeline run against this output folder?")

    # When the HTML file lives outside output_dir, prefix all relative asset paths.
    if path_prefix:
        for items in categories.values():
            for item in items:
                if item.get("thumb"):
                    item["thumb"] = f"{path_prefix}/{item['thumb']}"
                if item.get("base_img"):
                    item["base_img"] = f"{path_prefix}/{item['base_img']}"

    print()
    print("Building HTML...")
    html = build_html(categories)
    html_path.write_text(html, encoding="utf-8")

    size_kb = html_path.stat().st_size // 1024
    print(f"  Written: {html_path}  ({size_kb} KB)")
    print()
    print("Open in your browser:")
    print(f"  {html_path}")


if __name__ == "__main__":
    main()
