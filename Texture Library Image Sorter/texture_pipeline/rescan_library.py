"""
rescan_library.py
-----------------
Re-audits an already-organised texture library against the current pipeline
filter settings WITHOUT re-running AI tagging.

For every texture group found in the organised library, the script re-runs
the same algorithmic filters as the main pipeline:

  Pre-filter 1  --  Minimum resolution
  Pre-filter 2  --  Blank / solid-colour detection
  Pre-filter 3  --  Line-art / technical drawing detection
  Pre-filter 4  --  Product photo / isolated-object detection
  Tileability   --  Three-signal test (gradient spike, seam diff, offset seam)

Groups that no longer pass are moved to:
    <library>/_needs_review/almost_passed/<Category>/<GroupName>/

A dry-run mode shows what would change without touching any files.
--also-check-failed reports which previously-failed textures now pass
(useful after lowering a threshold).

Usage
-----
    python rescan_library.py --library /path/to/library
    python rescan_library.py --library /path/to/library --dry-run
    python rescan_library.py --library /path --tile-offset-seam 1.5 --dry-run
    python rescan_library.py --library /path --also-check-failed
"""

import argparse
import logging
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, UnidentifiedImageError

from config import Config
from image_processor import ImageProcessor


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(library_dir: Path) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt  = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    stamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = library_dir / f"rescan_{stamp}.log"
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    logging.getLogger(__name__).info("Log: %s", log_path)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# No-op database stub
# ImageProcessor requires a db argument but the rescan does not write to SQLite.
# ---------------------------------------------------------------------------

class _NullDB:
    """Accepts all DatabaseManager calls without side effects."""
    def update_group_status(self, *a, **kw): pass
    def set_group_unit_aspect_ratio(self, *a, **kw): pass
    def set_file_dimensions(self, *a, **kw): pass
    def get_group(self, *a, **kw): return None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class GroupScan:
    category: str
    group_name: str
    group_dir: Path
    base_map: Optional[Path]


@dataclass
class AuditResult:
    group:          GroupScan
    passed:         bool
    failure_reason: str = ""
    detail:         str = ""


# ---------------------------------------------------------------------------
# Base-map detection
# ---------------------------------------------------------------------------

def _find_base_map(group_dir: Path, config: Config) -> Optional[Path]:
    """
    Locate the base (albedo/diffuse/colour) map in an already-processed group
    folder, using the same priority order as the main scanner.

    Strategy:
      1. Any image whose stem ends with a tier-1 base suffix (e.g. _albedo).
      2. Among images without a known non-base suffix, pick the largest file.
      3. Fall back: single largest image in the folder.
    """
    exts   = set(config.supported_image_formats)
    images = [
        f for f in group_dir.iterdir()
        if f.is_file() and f.suffix.lower() in exts
    ]
    if not images:
        return None

    # Priority 1 -- explicit tier-1 base suffix
    for f in sorted(images, key=lambda x: x.name):
        stem = f.stem.lower()
        if any(stem.endswith(s) for s in config.base_map_tier1_suffixes):
            return f

    # Priority 2 -- no non-base suffix; take largest
    candidates = [
        f for f in images
        if not any(f.stem.lower().endswith(s) for s in config.non_base_map_suffixes)
    ]
    if candidates:
        return max(candidates, key=lambda f: f.stat().st_size)

    # Priority 3 -- fall back to largest image
    return max(images, key=lambda f: f.stat().st_size)


# ---------------------------------------------------------------------------
# Library scanning
# ---------------------------------------------------------------------------

def _scan_category(cat_dir: Path, config: Config) -> List[GroupScan]:
    """Return one GroupScan per immediate subdirectory of a category folder."""
    groups: List[GroupScan] = []
    if not cat_dir.is_dir():
        return groups
    for group_dir in sorted(cat_dir.iterdir()):
        if not group_dir.is_dir():
            continue
        groups.append(GroupScan(
            category  = cat_dir.name,
            group_name = group_dir.name,
            group_dir  = group_dir,
            base_map   = _find_base_map(group_dir, config),
        ))
    return groups


def _scan_library(library_dir: Path, config: Config) -> List[GroupScan]:
    """
    Scan every recognised category subfolder under *library_dir*.
    Categories listed in tileability_override_categories (Art, Sky, Utility,
    Water, etc.) are skipped entirely — they are inherently non-tileable and
    were never subject to tileability testing in the main pipeline.
    """
    skip = set(config.tileability_override_categories)
    all_groups: List[GroupScan] = []
    for cat in config.categories:
        if cat in skip:
            logger.info("  %-22s  skipped (non-tileable category)", cat)
            continue
        found = _scan_category(library_dir / cat, config)
        if found:
            logger.info("  %-22s  %d group(s)", cat, len(found))
        all_groups.extend(found)
    return all_groups


# ---------------------------------------------------------------------------
# Per-group audit
# ---------------------------------------------------------------------------

def _audit_group(
    group: GroupScan,
    config: Config,
    processor: ImageProcessor,
) -> AuditResult:
    """
    Re-run all algorithmic filters on *group*.
    Returns AuditResult(passed=True) if the group still meets every threshold.

    Mirrors the filter order in ImageProcessor._process_one:
      Pre-filter 1 → 2 → 3 → 4 → Tileability
    Tileability bypass keywords (e.g. "seamless") are respected.
    """
    if group.base_map is None:
        return AuditResult(group, passed=False,
                           failure_reason="no_base_map",
                           detail="No image files found in group directory.")

    # ── Open image ──────────────────────────────────────────────────────────
    try:
        img = Image.open(group.base_map)
        img.load()
        w, h = img.size
    except (UnidentifiedImageError, OSError, Exception) as exc:
        return AuditResult(group, passed=False,
                           failure_reason="unreadable",
                           detail=str(exc))

    # ── Pre-filter 1: minimum resolution ────────────────────────────────────
    if min(w, h) < config.min_resolution_px:
        img.close()
        return AuditResult(
            group, passed=False,
            failure_reason="below_min_resolution",
            detail=f"{w}x{h} below {config.min_resolution_px}px minimum",
        )

    # ── Convert to grayscale (reused by filters 2–4) ─────────────────────────
    try:
        gray = np.array(img.convert("L"), dtype=np.float32)
    except Exception as exc:
        img.close()
        return AuditResult(group, passed=False,
                           failure_reason="conversion_error", detail=str(exc))

    h_px, w_px = gray.shape

    # ── Pre-filter 2: blank / solid-colour ───────────────────────────────────
    stddev = float(gray.std())
    if stddev < config.blank_image_stddev_bin:
        img.close()
        return AuditResult(
            group, passed=False,
            failure_reason="blank_image",
            detail=f"stddev={stddev:.2f} < threshold {config.blank_image_stddev_bin}",
        )

    # ── Pre-filter 3: line-art / technical drawing ───────────────────────────
    white_fraction = float((gray >= 240.0).sum()) / gray.size
    if white_fraction >= config.line_art_white_pixel_threshold:
        img.close()
        return AuditResult(
            group, passed=False,
            failure_reason="line_art",
            detail=(
                f"white_fraction={white_fraction:.3f} >= "
                f"threshold {config.line_art_white_pixel_threshold}"
            ),
        )

    # ── Pre-filter 4: product photo / isolated-object ────────────────────────
    strip = config.tileability_edge_strip_px
    if h_px >= strip * 4 and w_px >= strip * 4:
        edge_stddevs = {
            "top":    float(gray[:strip,  :].std()),
            "bottom": float(gray[-strip:, :].std()),
            "left":   float(gray[:,  :strip].std()),
            "right":  float(gray[:, -strip:].std()),
        }
        max_edge_std = max(edge_stddevs.values())
        if max_edge_std < config.product_photo_edge_stddev_threshold:
            img.close()
            return AuditResult(
                group, passed=False,
                failure_reason="product_photo",
                detail=(
                    f"max_edge_stddev={max_edge_std:.2f} < "
                    f"threshold {config.product_photo_edge_stddev_threshold} "
                    f"(top={edge_stddevs['top']:.2f} "
                    f"bottom={edge_stddevs['bottom']:.2f} "
                    f"left={edge_stddevs['left']:.2f} "
                    f"right={edge_stddevs['right']:.2f})"
                ),
            )

    # ── Tileability (three signals) ───────────────────────────────────────────
    # Respect the same bypass keywords as the main pipeline.
    if processor._has_tileability_bypass(group.group_name):
        logger.debug(
            "Tileability bypassed for '%s' (filename keyword).", group.group_name
        )
    else:
        is_tileable = processor._test_tileability(img, group.group_name)
        if not is_tileable:
            img.close()
            return AuditResult(
                group, passed=False,
                failure_reason="tileability",
                detail="One or more tileability signals failed (see DEBUG log).",
            )

    img.close()
    return AuditResult(group, passed=True)


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

def _move_group(result: AuditResult, dest_root: Path, dry_run: bool) -> None:
    """
    Move the entire group folder into dest_root/<Category>/<GroupName>/.
    Appends a numeric suffix if the destination already exists.
    """
    src      = result.group.group_dir
    dest_cat = dest_root / result.group.category
    dest     = dest_cat  / result.group.group_name

    # Avoid overwriting an existing destination
    if dest.exists():
        n = 1
        while (dest_cat / f"{result.group.group_name}_{n}").exists():
            n += 1
        dest = dest_cat / f"{result.group.group_name}_{n}"

    if dry_run:
        logger.info("  [DRY-RUN] would move → %s", dest)
        return

    dest_cat.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    logger.info("  Moved → %s", dest)


# ---------------------------------------------------------------------------
# --also-check-failed: report previously-failed groups that now pass
# ---------------------------------------------------------------------------

def _check_failed_textures(
    library_dir: Path,
    config: Config,
    processor: ImageProcessor,
) -> None:
    """
    Scan _needs_review/tileability_failed/ and report any groups that now pass
    the current filters.  Does NOT move any files — this is report-only.

    Handles both flat (tileability_failed/GroupName/) and nested
    (tileability_failed/Category/GroupName/) directory layouts.
    """
    failed_dir = library_dir / "_needs_review" / "tileability_failed"
    if not failed_dir.is_dir():
        logger.info("  No _needs_review/tileability_failed/ folder found; skipping.")
        return

    # Collect (display_name, group_dir) pairs from a potentially mixed layout.
    candidates: List[Tuple[str, Path]] = []
    for child in sorted(failed_dir.iterdir()):
        if not child.is_dir():
            continue
        sub_dirs = [x for x in child.iterdir() if x.is_dir()]
        if sub_dirs:
            # Looks like a category folder — descend one level
            for gd in sorted(sub_dirs):
                candidates.append((f"{child.name}/{gd.name}", gd))
        else:
            # Treat child directly as a group folder
            candidates.append((child.name, child))

    if not candidates:
        logger.info("  No groups found in tileability_failed/.")
        return

    now_passes: List[str] = []
    for display_name, group_dir in candidates:
        base_map = _find_base_map(group_dir, config)
        group = GroupScan(
            category   = "tileability_failed",
            group_name = group_dir.name,
            group_dir  = group_dir,
            base_map   = base_map,
        )
        result = _audit_group(group, config, processor)
        if result.passed:
            now_passes.append(display_name)

    if now_passes:
        logger.info(
            "\n  Previously-failed textures that NOW PASS current filters:"
        )
        for name in now_passes:
            logger.info("    ✓  %s", name)
        logger.info(
            "\n  %d texture(s) listed above can be manually re-added to the "
            "library after verifying their category assignment.",
            len(now_passes),
        )
    else:
        logger.info("  No previously-failed textures pass the current filters.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Re-audit an organised texture library against the current filter "
            "settings. Failures are moved to _needs_review/almost_passed/."
        )
    )
    p.add_argument(
        "--library", required=True,
        help="Path to the organised texture library (the pipeline --output dir).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without moving any files.",
    )
    p.add_argument(
        "--also-check-failed", action="store_true",
        help=(
            "Also report which textures in _needs_review/tileability_failed/ "
            "now pass the current filters (report only, no files moved)."
        ),
    )

    # Threshold overrides — identical flags to main.py so the GUI can share them
    p.add_argument("--blank-stddev",        type=float, default=None,
                   help="Std-dev threshold below which an image is considered blank")
    p.add_argument("--product-edge-stddev", type=float, default=None,
                   help="Edge std-dev threshold for product-photo detection")
    p.add_argument("--line-art-threshold",  type=float, default=None,
                   help="White-pixel ratio above which an image is classified line-art")
    p.add_argument("--tile-gradient",       type=float, default=None,
                   help="Gradient ratio threshold for tileability Signal 1")
    p.add_argument("--tile-seam-diff",      type=float, default=None,
                   help="Seam difference threshold for tileability Signal 2")
    p.add_argument("--tile-offset-seam",    type=float, default=None,
                   help="Offset-seam projected gradient ratio for tileability Signal 3")
    p.add_argument("--min-resolution",      type=int,   default=None,
                   help="Minimum short-side resolution in pixels")

    return p.parse_args()


def _build_config(args: argparse.Namespace) -> Config:
    kwargs: dict = {}
    if args.blank_stddev         is not None:
        kwargs["blank_image_stddev_bin"]                   = args.blank_stddev
    if args.product_edge_stddev  is not None:
        kwargs["product_photo_edge_stddev_threshold"]      = args.product_edge_stddev
    if args.line_art_threshold   is not None:
        kwargs["line_art_white_pixel_threshold"]           = args.line_art_threshold
    if args.tile_gradient        is not None:
        kwargs["tileability_gradient_ratio_threshold"]     = args.tile_gradient
    if args.tile_seam_diff       is not None:
        kwargs["tileability_seam_diff_threshold"]          = args.tile_seam_diff
    if args.tile_offset_seam     is not None:
        kwargs["tileability_offset_seam_ratio_threshold"]  = args.tile_offset_seam
    if args.min_resolution       is not None:
        kwargs["min_resolution_px"]                        = args.min_resolution
    return Config(**kwargs)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args        = _parse_args()
    library_dir = Path(args.library).resolve()

    if not library_dir.is_dir():
        print(f"ERROR: library directory not found: {library_dir}", file=sys.stderr)
        sys.exit(1)

    _setup_logging(library_dir)

    config    = _build_config(args)
    processor = ImageProcessor(config, _NullDB())
    dest_root = library_dir / "_needs_review" / "almost_passed"
    mode      = "DRY-RUN" if args.dry_run else "LIVE"

    # ── Header ──────────────────────────────────────────────────────────────
    logger.info("═" * 62)
    logger.info("Texture Library Rescan  [%s]", mode)
    logger.info("Library : %s", library_dir)
    if not args.dry_run:
        logger.info("Dest    : %s", dest_root)
    logger.info("Active thresholds:")
    logger.info(
        "  blank_stddev=%.2f  product_edge=%.2f  line_art=%.2f",
        config.blank_image_stddev_bin,
        config.product_photo_edge_stddev_threshold,
        config.line_art_white_pixel_threshold,
    )
    logger.info(
        "  tile_gradient=%.2f  tile_seam=%.2f  tile_offset_seam=%.2f",
        config.tileability_gradient_ratio_threshold,
        config.tileability_seam_diff_threshold,
        config.tileability_offset_seam_ratio_threshold,
    )
    logger.info("  min_resolution=%dpx", config.min_resolution_px)
    logger.info("═" * 62)

    # ── Scan ────────────────────────────────────────────────────────────────
    logger.info("Scanning library categories…")
    groups = _scan_library(library_dir, config)
    logger.info("Found %d texture group(s) total.", len(groups))

    if not groups:
        logger.info("Nothing to audit.")
        if args.also_check_failed:
            logger.info("\n── Checking previously-failed textures ──────────────")
            _check_failed_textures(library_dir, config, processor)
        logger.info("Done.")
        return

    # ── Audit ────────────────────────────────────────────────────────────────
    logger.info("\nAuditing…")
    passed: List[AuditResult] = []
    failed: List[AuditResult] = []

    for i, group in enumerate(groups, 1):
        result = _audit_group(group, config, processor)
        if result.passed:
            passed.append(result)
        else:
            failed.append(result)
            logger.info(
                "  [%d/%d] FAIL  %s/%s",
                i, len(groups),
                result.group.category, result.group.group_name,
            )
            logger.info("         reason: %s — %s",
                        result.failure_reason, result.detail)

    # ── Summary ──────────────────────────────────────────────────────────────
    logger.info("\n── Rescan Summary ──────────────────────────────────────")
    logger.info("  Passed : %d", len(passed))
    logger.info("  Failed : %d", len(failed))

    if not failed:
        logger.info("\nAll %d texture(s) pass the current filter settings.", len(passed))
    else:
        # Failures by reason
        by_reason: Dict[str, List[AuditResult]] = {}
        for r in failed:
            by_reason.setdefault(r.failure_reason, []).append(r)

        logger.info("\n  Failures by reason:")
        for reason in sorted(by_reason):
            items = by_reason[reason]
            logger.info("    %-25s  %d", reason, len(items))
            for r in items:
                logger.info(
                    "        %s/%s", r.group.category, r.group.group_name
                )

        logger.info("\n  Destination: %s", dest_root)

        if args.dry_run:
            logger.info("\n[DRY-RUN] The following %d group(s) would be moved:",
                        len(failed))
        else:
            logger.info("\nMoving %d failed group(s)…", len(failed))

        for result in failed:
            logger.info(
                "  %s/%s  [%s]",
                result.group.category, result.group.group_name,
                result.failure_reason,
            )
            _move_group(result, dest_root, dry_run=args.dry_run)

        if not args.dry_run:
            logger.info(
                "\n%d group(s) moved to _needs_review/almost_passed/", len(failed)
            )

    # ── Also-check-failed ────────────────────────────────────────────────────
    if args.also_check_failed:
        logger.info("\n── Checking previously-failed textures ──────────────")
        _check_failed_textures(library_dir, config, processor)

    logger.info("\nRescan complete.")


if __name__ == "__main__":
    main()
