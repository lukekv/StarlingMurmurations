"""
scanner_helpers.py
------------------
Pure helper functions for the scanner: suffix stripping, file classification,
base map identification, dimension scraping, PAT assignment, and group ID generation.
Separated from scanner.py to keep file sizes manageable.
"""

import hashlib
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Map type normalisation table
# Maps lowercased suffix (with leading _, -, or space) -> standardised type
# ---------------------------------------------------------------------------

SUFFIX_TO_MAP_TYPE: Dict[str, str] = {
    # Base / albedo / diffuse
    "_albedo": "albedo",      "_basecolor": "albedo",   "_base_color": "albedo",
    "_diffuse": "albedo",     "_diff": "albedo",         "_col": "albedo",
    "_color": "albedo",       "_colour": "albedo",       "_bc": "albedo",
    "_d": "albedo",
    " texture": "albedo",     " diffuse": "albedo",      " albedo": "albedo",
    " color": "albedo",       " colour": "albedo",
    # Normal
    "_normal": "normal",      "_norm": "normal",         "_nrm": "normal",
    "_n": "normal",           "-normal": "normal",       "-norm": "normal",
    " normal": "normal",      " norm": "normal",
    # Roughness
    "_roughness": "roughness","_rough": "roughness",     "_rgh": "roughness",
    "-roughness": "roughness","-rough": "roughness",
    " roughness": "roughness"," rough": "roughness",
    # Metallic / metalness
    "_metallic": "metallic",  "_metal": "metallic",      "_met": "metallic",
    "_metalness": "metallic", "-metallic": "metallic",   "-metalness": "metallic",
    " metallic": "metallic",  " metalness": "metallic",  " metal": "metallic",
    # Displacement / height
    "_displacement": "displacement", "_disp": "displacement", "_displ": "displacement",
    "_height": "displacement",       "-displacement": "displacement",
    " displacement": "displacement", " disp": "displacement",
    # Ambient occlusion
    "_ao": "ao",              "_ambientocclusion": "ao", "_ambient_occlusion": "ao",
    "-ao": "ao",
    " ao": "ao",
    # Bump
    "_bump": "bump",          "_bmp": "bump",
    "-bump": "bump",          "-bmp": "bump",
    " bump": "bump",
    # Specular / gloss / reflection
    "_spec": "specular",      "_specular": "specular",   "_gloss": "specular",
    "_glossiness": "specular","_reflect": "specular",    "_reflection": "specular",
    "-spec": "specular",      "-specular": "specular",   "-reflect": "specular",
    " specular": "specular",  " spec": "specular",       " reflect": "specular",
    # Reflection (abbreviated form used by some asset packs)
    "_refl": "specular",
    # Normal map bit-depth variants (e.g. NRM16 = 16-bit normal)
    "_nrm16": "normal",
    # Opacity / emissive / mask
    "_opacity": "opacity",    "_emissive": "emissive",   "_emit": "emissive",
    "_mask": "mask",
}

# Canonical unit normalisation for dimension scraping
UNIT_CANONICAL: Dict[str, str] = {
    "inches": "inches", "inch": "inches", "in": "inches",
    "feet": "feet",     "foot": "feet",   "ft": "feet",
    "centimeters": "cm","centimeter": "cm","centimetres": "cm",
    "centimetre": "cm", "cm": "cm",
    "millimeters": "mm","millimeter": "mm","millimetres": "mm",
    "millimetre": "mm", "mm": "mm",
    "meters": "m",      "meter": "m",     "metres": "m",
    "metre": "m",       "m": "m",
}

# Compiled once -- strips trailing resolution tokens before map-type suffix matching.
# Handles _3K, _4K, _2K, _1K, _8K, _16K etc. (case-insensitive).
# e.g. ConcreteWall001_NRM_3K -> ConcreteWall001_NRM, Marble062_COL_4K -> Marble062_COL
_RES_TOKEN_RE = re.compile(r'[_-]\d+[kK]$')

# Strips trailing colour/material variant designators before map-type suffix matching.
# Applied after resolution token stripping.
# Handles _VAR1, _VAR2, _VAR01, _VAR12 (case-insensitive).
# e.g. ConcreteWall001_COL_VAR1_3K -> (strip _3K) -> ConcreteWall001_COL_VAR1
#                                   -> (strip _VAR1) -> ConcreteWall001_COL
#                                   -> (match _col suffix) -> base: ConcreteWall001
_VARIANT_TOKEN_RE = re.compile(r'[_-]VAR\d+$', re.IGNORECASE)

# Strips trailing LOD (level-of-detail) variant tokens before map-type suffix matching.
# Applied after resolution and variant token stripping.
# Handles _LOD0, _LOD1 ... _LOD5 (case-insensitive).
# e.g. Aset_wood_log_M_phyr5_4K_Normal_LOD0
#      -> (strip _LOD0) -> Aset_wood_log_M_phyr5_4K_Normal
#      -> (match _normal suffix) -> base: Aset_wood_log_M_phyr5_4K
_LOD_TOKEN_RE = re.compile(r'[_-]LOD\d+$', re.IGNORECASE)

# Dimension regex compiled once at module load.
# Builds unit alternation from UNIT_CANONICAL at import time so scrape_dimensions()
# never repeats the sort+escape+compile work at runtime.
_DIMENSION_RE = re.compile(
    r"(?P<w>\d+(?:\.\d+)?)"
    r"\s*[xX\xd7]\s*"
    r"(?P<h>\d+(?:\.\d+)?)"
    r"(?:\s*(?P<unit>"
    + "|".join(re.escape(u) for u in sorted(UNIT_CANONICAL.keys(), key=len, reverse=True))
    + r"))?",
    re.IGNORECASE,
)


class FileClass:
    IMAGE       = "image"
    PASSTHROUGH = "passthrough"
    REVIEW      = "review"
    SKIP        = "skip"


def build_known_suffixes(config: Config) -> List[str]:
    """
    Compile the full set of map-type suffix strings, sorted longest-first
    to prevent partial matches (e.g. '_displacement' before '_disp').
    """
    suffixes = set(SUFFIX_TO_MAP_TYPE.keys())
    for s in config.non_base_map_suffixes:
        suffixes.add(s.lower())
    for s in config.base_map_tier1_suffixes:
        suffixes.add(s.lower())
    for w in config.base_map_terminal_words:
        suffixes.add(f" {w.lower()}")
    return sorted(suffixes, key=len, reverse=True)


def strip_map_suffix(stem: str, known_suffixes: List[str]) -> Tuple[str, str]:
    """
    Remove a single known map-type suffix from the end of a filename stem.
    Matching is case-insensitive. Strips trailing _ - and whitespace from
    the remaining base name.

    A trailing resolution token (_3K, _4K, _16K, etc.) is stripped first so
    that suffixes like _NRM_3K resolve correctly to their map type.

    Returns (base_name, matched_suffix_lowercase).
    matched_suffix is "" if nothing was stripped.
    """
    stem = _RES_TOKEN_RE.sub('', stem)
    stem = _VARIANT_TOKEN_RE.sub('', stem)
    stem = _LOD_TOKEN_RE.sub('', stem)
    lower = stem.lower()
    for suffix in known_suffixes:
        if lower.endswith(suffix):
            base = stem[: len(stem) - len(suffix)].strip(" _-")
            return base, suffix
    return stem, ""


def classify_file(path: Path, config: Config) -> str:
    """Return a FileClass constant for this path."""
    name_lower = path.name.lower()
    if name_lower in config.skip_filenames:
        return FileClass.SKIP
    ext = path.suffix.lower()
    if ext in [f.lower() for f in config.passthrough_formats]:
        return FileClass.PASSTHROUGH
    if ext in [f.lower() for f in config.review_formats]:
        return FileClass.REVIEW
    if ext in [f.lower() for f in config.supported_image_formats]:
        return FileClass.IMAGE
    return FileClass.SKIP


def is_demo_file(stem: str, config: Config) -> bool:
    """
    True if any demo/preview keyword appears as an exact token in the filename
    stem when split on underscores, hyphens, and whitespace.

    Token-split matching is used instead of regex word boundaries because
    Python treats '_' as a word character, causing \b to not fire between
    an underscore separator and the adjacent letter.  Splitting on delimiters
    and requiring an exact token match prevents false positives like
    'renders' matching 'render' and avoids the \b/underscore trap.
    """
    tokens = set(re.split(r"[_\-\s]+", stem.lower()))
    tokens.discard("")
    return any(kw in tokens for kw in config.demo_keywords)


def strip_demo_keyword(stem: str, config: Config) -> str:
    """
    Remove a demo keyword from the end of a filename stem so that demo files
    can be grouped under their parent base name.
    e.g. '141_light parquet DEMO' -> '141_light parquet'
    Returns the original stem unchanged if no demo keyword is found at the end.
    """
    lower = stem.lower()
    for kw in sorted(config.demo_keywords, key=len, reverse=True):
        pattern = re.compile(r'[\s_\-]+' + re.escape(kw) + r'$', re.IGNORECASE)
        match = pattern.search(lower)
        if match:
            return stem[: match.start()].strip(" _-")
    return stem


def identify_map_type(suffix: str, config: Config) -> str:
    """Return a standardised map type string from a matched suffix."""
    key = suffix.lower()
    if key in SUFFIX_TO_MAP_TYPE:
        return SUFFIX_TO_MAP_TYPE[key]
    for s in config.base_map_tier1_suffixes:
        if key == s.lower():
            return "albedo"
    for w in config.base_map_terminal_words:
        if key == f" {w.lower()}":
            return "albedo"
    return "unknown"


def identify_base_map(
    image_files: List[Path],
    known_suffixes: List[str],
    config: Config,
) -> Optional[Path]:
    """
    3-tier base map identification.

    Tier 1 - File has an unambiguous Tier 1 suffix (_diffuse, _albedo, 'texture'...)
    Tier 2a - Only one candidate with NO known map-type suffix
    Tier 2b - Multiple candidates with no suffix; pick shortest stem
    Tier 3 - Cannot identify; return None
    """
    non_base = {s.lower() for s in config.non_base_map_suffixes}

    # Compute strip_map_suffix once per file; reuse across all three tiers.
    suffix_cache = {f: strip_map_suffix(f.stem, known_suffixes) for f in image_files}

    # Exclude files that clearly are not the base map
    candidates = [
        f for f in image_files
        if suffix_cache[f][1].lower() not in non_base
    ]

    if not candidates:
        return None

    # Tier 1
    tier1_keys  = {s.lower() for s in config.base_map_tier1_suffixes}
    tier1_words = {w.lower() for w in config.base_map_terminal_words}

    for f in candidates:
        _, matched = suffix_cache[f]
        ml = matched.lower()
        if ml in tier1_keys:
            return f
        if ml.lstrip(" -_") in tier1_words:
            return f
        # Terminal word match with no separator
        stem_lower = _RES_TOKEN_RE.sub('', f.stem).lower()
        for word in tier1_words:
            if stem_lower.endswith(word):
                return f

    # Tier 2a - single file with no known suffix
    no_suffix = [f for f in candidates if suffix_cache[f][1] == ""]
    if len(no_suffix) == 1:
        return no_suffix[0]

    # Tier 2b - pick shortest stem
    if len(no_suffix) > 1:
        return min(no_suffix, key=lambda f: len(f.stem))

    return None


def scrape_dimensions(filename: str, config: Config) -> Optional[dict]:
    """
    Extract real-world dimensions from a filename.
    Returns a dict or None. Never raises -- parsing failure is not an error.

    Examples:
        "39.8 x 47.9 inches" -> {width:39.8, height:47.9, unit:"inches", raw:"..."}
        "600x300mm"          -> {width:600.0, height:300.0, unit:"mm", raw:"..."}
        "24 x 48"            -> {width:24.0, height:48.0, unit:"inches", unit_ambiguous:True}
    """
    match = _DIMENSION_RE.search(filename)
    if not match:
        return None

    w = float(match.group("w"))
    h = float(match.group("h"))
    raw_unit = match.group("unit") or ""
    unit = UNIT_CANONICAL.get(raw_unit.lower(), None) if raw_unit else None

    result: dict = {"width": w, "height": h, "raw": match.group(0)}
    if unit:
        result["unit"] = unit
    else:
        result["unit"] = "inches"    # no unit in filename → assume inches (US default)
        result["unit_ambiguous"] = True
    return result


def assign_pat_to_groups(
    pat_files: List[Path],
    groups: List[dict],
) -> Dict[str, Optional[str]]:
    """
    Best-effort assignment of .pat files to their parent PBR group.
    Single-group directories: all PATs go to that group.
    Multi-group directories: keyword matching on tokenised filenames.
    Ties or no match: PAT left unassigned (caller logs and handles).
    Returns dict mapping pat_path_str -> group_id or None.
    """
    assignment: Dict[str, Optional[str]] = {}

    if len(groups) == 1:
        for p in pat_files:
            assignment[str(p)] = groups[0]["group_id"]
        return assignment

    for p in pat_files:
        tokens = set(re.split(r"[\s_-]+", p.stem.lower()))
        tokens.discard("")
        scores = []
        for g in groups:
            base_tokens = set(re.split(r"[\s_-]+", g["base_name"].lower()))
            scores.append((len(tokens & base_tokens), g["group_id"]))
        scores.sort(key=lambda x: x[0], reverse=True)

        if not scores or scores[0][0] == 0:
            assignment[str(p)] = None
        elif len(scores) > 1 and scores[0][0] == scores[1][0]:
            assignment[str(p)] = None
            logger.warning("Ambiguous PAT: %s tied between %s and %s",
                           p.name, scores[0][1], scores[1][1])
        else:
            assignment[str(p)] = scores[0][1]

    return assignment


def make_group_id(source_dir: Path, base_name: str) -> str:
    """
    Deterministic SHA-256-based group ID.
    Same inputs always produce the same ID -- critical for crash recovery.
    """
    key = f"{str(source_dir).lower()}::{base_name.lower().strip()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
