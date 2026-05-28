"""
main.py
-------
Pipeline orchestrator. Runs all five stages in order, routes non-output
groups to the correct directories, and prints an end-of-run summary.

Usage
-----
    python main.py --input /path/to/library --output /path/to/output

    # Resume a crashed run (database already exists):
    python main.py --input /path/to/library --output /path/to/output --db ./pipeline_state.db

    # Scan and deduplication only -- no files written to output:
    python main.py --input /path/to/library --output /path/to/output --dry-run

    # Run the tileability AI override pass independently (after a completed run):
    python main.py --input /path/to/library --output /path/to/output --override-pass
"""

import argparse
import logging
import shutil
import sys
from pathlib import Path
from typing import Dict, List

from ai_tagger import AITagger
from config import Config
from database import DatabaseManager, GroupStatus
from deduplicator import Deduplicator
from file_ops import FileOps
from image_processor import ImageProcessor, ProcessResult
from scanner import PBRGroup, Scanner

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(config: Config) -> None:
    from datetime import datetime
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt  = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    log_dir  = Path(config.output_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"pipeline_{stamp}.log"
    fh       = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    logger.info("Log file: %s", log_file)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Texture Library Pipeline: process and organise PBR textures."
    )
    p.add_argument("--input",       required=True)
    p.add_argument("--output",      required=True)
    p.add_argument("--db",          default=None)
    p.add_argument("--recycle-bin", default=None)
    p.add_argument("--review-dir",  default=None)
    p.add_argument("--dry-run",       action="store_true")
    p.add_argument("--override-pass", action="store_true",
                   help="Run the tileability AI override pass independently "
                        "(processes all TILEABILITY_FAILED groups in the database).")

    # --- Runtime / model overrides (used by GUI) ----------------------------
    p.add_argument("--ai-model",            default=None,
                   help="Ollama model tag, e.g. gemma4:e4b")
    p.add_argument("--cpu-workers",         type=int,   default=None,
                   help="Thread-pool size for CPU-bound passes")

    # --- Filter thresholds --------------------------------------------------
    p.add_argument("--blank-stddev",         type=float, default=None,
                   help="Std-dev threshold below which an image is considered blank")
    p.add_argument("--product-edge-stddev",  type=float, default=None,
                   help="Edge std-dev threshold for product-photo detection")
    p.add_argument("--line-art-threshold",   type=float, default=None,
                   help="White-pixel ratio above which an image is classified line-art")
    p.add_argument("--tile-gradient",        type=float, default=None,
                   help="Gradient ratio threshold for tileability pass")
    p.add_argument("--tile-seam-diff",       type=float, default=None,
                   help="Seam difference threshold for tileability pass")
    p.add_argument("--tile-offset-seam",     type=float, default=None,
                   help="Offset-seam projected gradient ratio threshold (Signal 3)")
    p.add_argument("--phash-hamming",        type=int,   default=None,
                   help="Maximum Hamming distance for pHash duplicate detection")
    p.add_argument("--min-resolution",       type=int,   default=None,
                   help="Minimum short-side resolution in pixels")
    p.add_argument("--auto-bin-tileability", action="store_true",
                   help="Bin tileability failures instead of sending to review")
    p.add_argument("--skip-quality-checks",  action="store_true",
                   help="Skip pre-filters 2–4 and tileability test (trusted source)")

    return p.parse_args()


def _build_config(args: argparse.Namespace) -> Config:
    out = Path(args.output)
    kwargs: dict = dict(
        input_dir             = Path(args.input),
        output_dir            = out,
        recycle_bin_dir       = Path(args.recycle_bin) if args.recycle_bin else out / "_recycle_bin",
        review_dir            = Path(args.review_dir)  if args.review_dir  else out / "_needs_review",
        db_path               = Path(args.db)           if args.db           else out / "pipeline_state.db",
        duplicate_report_path = out / "duplicate_report.txt",
    )
    # Optional overrides from CLI (GUI passes these when non-default)
    if args.ai_model is not None:
        kwargs["ai_model"] = args.ai_model
    if args.cpu_workers is not None:
        kwargs["cpu_workers"] = args.cpu_workers
    if args.blank_stddev is not None:
        kwargs["blank_image_stddev_bin"] = args.blank_stddev
    if args.product_edge_stddev is not None:
        kwargs["product_photo_edge_stddev_threshold"] = args.product_edge_stddev
    if args.line_art_threshold is not None:
        kwargs["line_art_white_pixel_threshold"] = args.line_art_threshold
    if args.tile_gradient is not None:
        kwargs["tileability_gradient_ratio_threshold"] = args.tile_gradient
    if args.tile_seam_diff is not None:
        kwargs["tileability_seam_diff_threshold"] = args.tile_seam_diff
    if args.tile_offset_seam is not None:
        kwargs["tileability_offset_seam_ratio_threshold"] = args.tile_offset_seam
    if args.phash_hamming is not None:
        kwargs["phash_hamming_threshold"] = args.phash_hamming
    if args.min_resolution is not None:
        kwargs["min_resolution_px"] = args.min_resolution
    if args.auto_bin_tileability:
        kwargs["auto_bin_tileability_failures"] = True
    if args.skip_quality_checks:
        kwargs["skip_quality_checks"] = True
    return Config(**kwargs)


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------

def _db_status(group_id: str, db: DatabaseManager) -> str:
    row = db.get_group(group_id)
    return row["status"] if row else ""


def _safe_dir_name(name: str) -> str:
    for ch in ["<", ">", ":", chr(34), "/", "|", "?", "*"]:
        name = name.replace(ch, "_")
    return name.strip() or "unnamed"


def _copy_files(files: List[Path], dst_dir: Path) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        if f.exists():
            shutil.copy2(str(f), str(dst_dir / f.name))


def _route_duplicates(
    groups: List[PBRGroup], db: DatabaseManager, config: Config
) -> None:
    dst_dir = Path(config.recycle_bin_dir) / "duplicates"
    count   = 0
    for g in groups:
        row = db.get_group(g.group_id)
        if row and row["is_duplicate"] and row["base_map_path"]:
            src = Path(row["base_map_path"])
            if src.exists():
                dst_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst_dir / src.name))
                count += 1
    if count:
        logger.info("Routed %d duplicate base map(s) to recycle bin.", count)


def _route_binned(
    groups: List[PBRGroup], db: DatabaseManager, config: Config
) -> None:
    dst_dir = Path(config.recycle_bin_dir) / "low_resolution"
    count   = 0
    for g in groups:
        row = db.get_group(g.group_id)
        if row and row["status"] == GroupStatus.BINNED_RESOLUTION.value:
            if row["base_map_path"]:
                src = Path(row["base_map_path"])
                if src.exists():
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src), str(dst_dir / src.name))
                    count += 1
    if count:
        logger.info("Routed %d low-resolution base map(s) to recycle bin.", count)


def _route_blank_images(
    groups: List[PBRGroup], db: DatabaseManager, config: Config
) -> None:
    dst_root = Path(config.recycle_bin_dir) / "blank_images"
    count    = 0
    for g in groups:
        row = db.get_group(g.group_id)
        if row and row["status"] == GroupStatus.BINNED_BLANK.value:
            _copy_files(
                g.image_files + g.demo_files + g.pat_files,
                dst_root / _safe_dir_name(g.base_name),
            )
            count += 1
    if count:
        logger.info("Routed %d blank/solid-colour group(s) to recycle bin.", count)


def _route_product_photo(
    groups: List[PBRGroup], db: DatabaseManager, config: Config
) -> None:
    """
    Copy all source files for product-photo flagged groups to
    _recycle_bin/product_photo/.

    These groups failed the edge-strip uniformity check: all four edge strips
    had near-zero pixel standard deviation, indicating the image is a product
    catalog photo of an isolated object on a clean studio background rather
    than a seamless material texture.
    """
    dst_root = Path(config.recycle_bin_dir) / "product_photo"
    count    = 0
    for g in groups:
        row = db.get_group(g.group_id)
        if row and row["status"] == GroupStatus.BINNED_PRODUCT_PHOTO.value:
            _copy_files(
                g.image_files + g.demo_files + g.pat_files,
                dst_root / _safe_dir_name(g.base_name),
            )
            count += 1
    if count:
        logger.info(
            "Routed %d product photo group(s) to recycle bin.", count
        )


def _route_line_art(
    groups: List[PBRGroup], db: DatabaseManager, config: Config
) -> None:
    """
    Copy all source files for line-art flagged groups to _needs_review/line_art/.

    These groups have >= line_art_white_pixel_threshold near-white pixels,
    indicating the image is likely a technical drawing, site plan, CAD output,
    or architectural document rather than a photographic material texture.
    """
    dst_root = Path(config.review_dir) / "line_art"
    count    = 0
    for g in groups:
        row = db.get_group(g.group_id)
        if row and row["status"] == GroupStatus.REVIEW_LINE_ART.value:
            _copy_files(
                g.image_files + g.demo_files + g.pat_files,
                dst_root / _safe_dir_name(g.base_name),
            )
            count += 1
    if count:
        logger.info(
            "Routed %d line-art / technical drawing group(s) to review.", count
        )


def _route_tileability_failures(
    groups: List[PBRGroup], db: DatabaseManager, config: Config
) -> None:
    if config.auto_bin_tileability_failures:
        dst_root = Path(config.recycle_bin_dir) / "tileability_failed"
        label    = "recycle bin"
    else:
        dst_root = Path(config.review_dir) / "tileability_failed"
        label    = "review"
    count = 0
    for g in groups:
        row = db.get_group(g.group_id)
        if row and row["status"] == GroupStatus.TILEABILITY_FAILED.value:
            _copy_files(
                g.image_files + g.demo_files + g.pat_files,
                dst_root / _safe_dir_name(g.base_name),
            )
            count += 1
    if count:
        logger.info("Routed %d tileability-failed group(s) to %s.", count, label)


def _route_ai_not_tileable(
    groups: List[PBRGroup], db: DatabaseManager, config: Config
) -> None:
    """
    Copy all source files for groups that passed Stage 3 geometry tests but
    were flagged as non-tileable by the AI to _needs_review/ai_not_tileable/.

    This is the secondary guard: the geometric tileability test has a blind
    spot for images where both opposite edge strips happen to be similar
    (e.g. a uniform background that is not a real seam). The AI provides an
    independent content-based judgment. Groups in this folder should be
    inspected manually -- some may be legitimate textures with an edge
    condition the AI misjudged; others may be product photos or renders the
    geometry test failed to catch.
    """
    dst_root = Path(config.review_dir) / "ai_not_tileable"
    count    = 0
    for g in groups:
        row = db.get_group(g.group_id)
        if row and row["status"] == GroupStatus.REVIEW_AI_NOT_TILEABLE.value:
            _copy_files(
                g.image_files + g.demo_files + g.pat_files,
                dst_root / _safe_dir_name(g.base_name),
            )
            count += 1
    if count:
        logger.info(
            "Routed %d AI-flagged non-tileable group(s) to "
            "_needs_review/ai_not_tileable/.",
            count,
        )


def _route_no_base_map(
    groups: List[PBRGroup], db: DatabaseManager, config: Config
) -> None:
    dst_root = Path(config.review_dir) / "no_base_map"
    count    = 0
    for g in groups:
        row = db.get_group(g.group_id)
        if row and row["status"] == GroupStatus.REVIEW_NO_BASE_MAP.value:
            _copy_files(
                g.image_files + g.demo_files + g.pat_files,
                dst_root / _safe_dir_name(g.base_name),
            )
            count += 1
    if count:
        logger.info("Routed %d no-base-map group(s) to review.", count)



def _route_mesh_asset(
    groups: List[PBRGroup], db: DatabaseManager, config: Config
) -> None:
    """
    Copy all source files for mesh-asset-flagged groups to
    _needs_review/mesh_asset/.

    These groups were found in a directory that also contained 3D mesh files
    (.fbx, .obj, .glb, etc.).  The textures are PBR maps bound to a specific
    mesh rather than standalone tileable materials.  They need manual review to
    decide whether any of the maps are also usable as general library textures.
    """
    dst_root = Path(config.review_dir) / "mesh_asset"
    count    = 0
    for g in groups:
        row = db.get_group(g.group_id)
        if row and row["status"] == GroupStatus.REVIEW_MESH_ASSET.value:
            _copy_files(
                g.image_files + g.demo_files + g.pat_files,
                dst_root / _safe_dir_name(g.base_name),
            )
            count += 1
    if count:
        logger.info(
            "Routed %d mesh-asset group(s) to _needs_review/mesh_asset/.", count
        )

def _route_review_files(groups: List[PBRGroup], config: Config) -> None:
    dst_dir = Path(config.review_dir) / "format_review"
    count   = 0
    for g in groups:
        for f in g.review_files:
            if f.exists():
                dst_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(f), str(dst_dir / f.name))
                count += 1
    if count:
        logger.info("Routed %d review-format file(s) to review.", count)


def _route_misc(
    groups: List[PBRGroup], db: DatabaseManager, config: Config
) -> None:
    """
    After Stage 4, log how many completed groups were tagged as Misc.
    Actual file writing to review_dir/misc/ is handled in file_ops._process_one.
    """
    import json as _json
    count = 0
    for g in groups:
        row = db.get_group(g.group_id)
        if row and row["status"] == GroupStatus.COMPLETED.value and row["ai_output"]:
            try:
                if _json.loads(row["ai_output"]).get("category") == "Misc":
                    count += 1
            except Exception:
                pass
    if count:
        logger.info(
            "%d group(s) tagged as Misc routed to _needs_review/misc/ "
            "for human review.",
            count,
        )


# ---------------------------------------------------------------------------
# Tileability AI override pass (Option A)
# ---------------------------------------------------------------------------

def _tileability_ai_override(
    groups: List[PBRGroup],
    db: DatabaseManager,
    config: Config,
    tagger: "AITagger",
    file_ops: "FileOps",
    process_results: Dict[str, "ProcessResult"],
) -> int:
    """
    Run the AI on every group that failed the tileability test.

    If the AI returns a category in config.tileability_override_categories
    (Art, Sky, Utility, Water by default), the tileability failure is
    overridden: the group is tagged and written to the library immediately.

    If the AI returns any other category, the group remains at
    TILEABILITY_FAILED and is picked up by the normal routing call that
    follows this function.

    Returns the number of groups rescued from the review queue.
    """
    override_set = set(config.tileability_override_categories)

    candidates = [
        g for g in groups
        if _db_status(g.group_id, db) == GroupStatus.TILEABILITY_FAILED.value
        and g.base_map_path is not None
    ]

    if not candidates:
        logger.info("Tileability AI override: no candidates.")
        return 0

    logger.info(
        "Tileability AI override: running AI on %d tileability-failed group(s).",
        len(candidates),
    )

    # Destination for confirmed tileability failures (matches _route_tileability_failures).
    if config.auto_bin_tileability_failures:
        _fail_dst = Path(config.recycle_bin_dir) / "tileability_failed"
        _fail_lbl = "recycle bin"
    else:
        _fail_dst = Path(config.review_dir) / "tileability_failed"
        _fail_lbl = "review"

    rescued   = 0
    confirmed = 0
    i         = 0
    try:
        for i, group in enumerate(candidates, 1):
            logger.info("[%d/%d] %s", i, len(candidates), group.base_name)
            result = tagger.tag_group(group)
            if result is None:
                # AI failed -- leave at TILEABILITY_FAILED so the post-loop routing
                # sends it to _needs_review and a future override pass can retry it.
                logger.warning(
                    "Override pass: AI failed for '%s', leaving in review queue.",
                    group.base_name,
                )
                db.update_group_status(
                    group.group_id, GroupStatus.TILEABILITY_FAILED,
                    detail="ai_override_failed",
                )
                continue

            category = result.get("category", "")
            if category in override_set:
                logger.info(
                    "Override pass: '%s' tagged as '%s' -- routing to library.",
                    group.base_name, category,
                )
                file_ops.process_one(group, process_results.get(group.group_id))
                rescued += 1
            else:
                logger.info(
                    "Override pass: '%s' tagged as '%s' -- confirmed tileability "
                    "failure, routing to %s.",
                    group.base_name, category, _fail_lbl,
                )
                # Route immediately and stamp with TILEABILITY_OVERRIDE_CONFIRMED so
                # a resumed override pass doesn't re-spend an API call on this group.
                _copy_files(
                    group.image_files + group.demo_files + group.pat_files,
                    _fail_dst / _safe_dir_name(group.base_name),
                )
                db.update_group_status(
                    group.group_id, GroupStatus.TILEABILITY_OVERRIDE_CONFIRMED,
                    detail=f"confirmed_failure_category_{category}",
                )
                confirmed += 1

    except KeyboardInterrupt:
        logger.warning(
            "Override pass interrupted at [%d/%d] (%d rescued, %d confirmed so far). "
            "Progress is saved — re-run with --override-pass to continue.",
            i, len(candidates), rescued, confirmed,
        )
        # Re-raise so _run_override_pass skips _route_tileability_failures;
        # unprocessed groups must stay at TILEABILITY_FAILED for the next resume.
        raise

    logger.info(
        "Tileability AI override complete: %d rescued, %d confirmed as failures.",
        rescued, confirmed,
    )
    return rescued


# ---------------------------------------------------------------------------
# File-ops mop-up (previous-run recovery)
# ---------------------------------------------------------------------------

def _mop_up_file_ops(db: DatabaseManager, config: Config) -> None:
    rows = db.get_groups_by_status(GroupStatus.FILE_OPS)
    if not rows:
        return

    logger.info("Mop-up: %d file_ops group(s) from a previous run.", len(rows))

    groups: List[PBRGroup] = []
    for row in rows:
        file_rows   = db.get_files_for_group(row["group_id"])
        image_files = [Path(r["source_path"]) for r in file_rows if not r["is_demo"] and not r["is_pat"]]
        demo_files  = [Path(r["source_path"]) for r in file_rows if r["is_demo"]]
        pat_files   = [Path(r["source_path"]) for r in file_rows if r["is_pat"]]
        map_types   = {r["source_path"]: r["map_type"] for r in file_rows}
        groups.append(PBRGroup(
            group_id      = row["group_id"],
            base_name     = row["base_name"],
            source_dir    = Path(row["source_dir"]),
            base_map_path = Path(row["base_map_path"]) if row["base_map_path"] else None,
            image_files   = image_files,
            demo_files    = demo_files,
            pat_files     = pat_files,
            map_types     = map_types,
        ))

    FileOps(config, db).process_groups(groups, {})


# ---------------------------------------------------------------------------
# Tileability override pass (independent mode, --override-pass flag)
# ---------------------------------------------------------------------------

def _run_override_pass(config: Config, db: DatabaseManager) -> None:
    """
    Standalone entry point for the tileability AI override pass.

    Queries the database for all TILEABILITY_FAILED groups, reconstructs
    PBRGroup objects from the stored rows, and runs the AI on each one.
    Groups whose AI category is in config.tileability_override_categories
    (Art, Sky, Utility, Water) are rescued and written to the library.
    All others remain at TILEABILITY_FAILED in the database.

    Crop bounding boxes are not persisted to the database, so rescued images
    are copied without cropping.  This is acceptable for the override
    categories (Art, Sky, Utility, Water), which are typically already square.
    """
    rows = db.get_groups_by_status(GroupStatus.TILEABILITY_FAILED)
    if not rows:
        logger.info("Override pass: no tileability-failed groups found.")
        return

    logger.info("Override pass: %d tileability-failed group(s) to process.", len(rows))

    groups: List[PBRGroup] = []
    for row in rows:
        file_rows   = db.get_files_for_group(row["group_id"])
        image_files = [Path(r["source_path"]) for r in file_rows if not r["is_demo"] and not r["is_pat"]]
        demo_files  = [Path(r["source_path"]) for r in file_rows if r["is_demo"]]
        pat_files   = [Path(r["source_path"]) for r in file_rows if r["is_pat"]]
        map_types   = {r["source_path"]: r["map_type"] for r in file_rows}
        groups.append(PBRGroup(
            group_id      = row["group_id"],
            base_name     = row["base_name"],
            source_dir    = Path(row["source_dir"]),
            base_map_path = Path(row["base_map_path"]) if row["base_map_path"] else None,
            image_files   = image_files,
            demo_files    = demo_files,
            pat_files     = pat_files,
            map_types     = map_types,
        ))

    tagger   = AITagger(config, db)
    file_ops = FileOps(config, db)
    try:
        rescued = _tileability_ai_override(groups, db, config, tagger, file_ops, {})
    except KeyboardInterrupt:
        # Unprocessed groups remain at TILEABILITY_FAILED -- don't route them now;
        # the next --override-pass invocation will pick them up.
        raise

    logger.info("Override pass complete: %d rescued.", rescued)

    # Route any groups still at TILEABILITY_FAILED (AI call failures) to review.
    _route_tileability_failures(groups, db, config)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _print_summary(db: DatabaseManager) -> None:
    counts = db.get_summary_counts()
    total  = sum(counts.values())
    logger.info("=" * 60)
    logger.info("Pipeline Summary")
    logger.info("=" * 60)
    for status in sorted(counts):
        logger.info("  %-30s %d", status, counts[status])
    logger.info("  %-30s %d", "TOTAL", total)
    logger.info("=" * 60)

    wf = db.get_workflow_type_counts()
    if wf:
        logger.info("Workflow Classification")
        logger.info("=" * 60)
        for wtype in ("PBR", "Diffuse"):
            if wtype in wf:
                logger.info("  %-30s %d", wtype, wf[wtype])
        logger.info("=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args   = _parse_args()
    config = _build_config(args)

    for d in [config.output_dir, config.recycle_bin_dir, config.review_dir]:
        Path(d).mkdir(parents=True, exist_ok=True)

    _setup_logging(config)
    logger.info("Texture Pipeline starting.")
    logger.info("Input : %s", config.input_dir)
    logger.info("Output: %s", config.output_dir)

    db = DatabaseManager(config.db_path)

    if args.override_pass:
        logger.info("Override pass starting.")
        try:
            _run_override_pass(config, db)
        except KeyboardInterrupt:
            logger.warning("Override pass interrupted by user. Progress saved.")
        finally:
            _print_summary(db)
            db.shutdown()
            logger.info("Override pass done.")
        return

    try:
        # -------------------------------------------------------------------
        # Stage 1: Scan
        # -------------------------------------------------------------------
        logger.info("=" * 60)
        logger.info("Stage 1: Scan")
        logger.info("=" * 60)
        groups = Scanner(config, db).scan()
        logger.info("Scan returned %d group(s) for processing.", len(groups))
        _route_review_files(groups, config)
        _route_mesh_asset(groups, db, config)

        if args.dry_run:
            logger.info("Dry run: stopping after scan.")
            _print_summary(db)
            return

        # -------------------------------------------------------------------
        # Stage 2: Deduplication
        # -------------------------------------------------------------------
        logger.info("=" * 60)
        logger.info("Stage 2: Deduplication")
        logger.info("=" * 60)
        Deduplicator(config, db).deduplicate(groups)
        _route_duplicates(groups, db, config)
        logger.info("Duplicate report: %s", config.duplicate_report_path)

        # -------------------------------------------------------------------
        # Stage 3: Image processing (concurrent CPU)
        # -------------------------------------------------------------------
        logger.info("=" * 60)
        logger.info("Stage 3: Image Processing")
        logger.info("=" * 60)
        pending = [
            g for g in groups
            if _db_status(g.group_id, db) == GroupStatus.PENDING.value
        ]
        logger.info("%d group(s) entering image processing.", len(pending))
        process_results: Dict[str, ProcessResult] = (
            ImageProcessor(config, db).process_groups(pending)
        )
        _route_binned(groups, db, config)
        _route_blank_images(groups, db, config)
        _route_product_photo(groups, db, config)
        _route_line_art(groups, db, config)
        _route_no_base_map(groups, db, config)
        _route_tileability_failures(groups, db, config)

        # -------------------------------------------------------------------
        # Stage 4: AI Tagging + File Operations (inline)
        # -------------------------------------------------------------------
        logger.info("=" * 60)
        logger.info("Stage 4: AI Tagging + File Operations")
        logger.info("=" * 60)

        tagger   = AITagger(config, db)
        file_ops = FileOps(config, db)

        ai_groups = [
            g for g in groups
            if _db_status(g.group_id, db) == GroupStatus.AI_TAGGING.value
        ]
        logger.info("%d group(s) to tag.", len(ai_groups))

        # Categories exempt from the ai_is_tileable secondary guard -- these
        # are intentionally non-tileable and must always reach the library.
        override_set = set(config.tileability_override_categories)

        for i, group in enumerate(ai_groups, 1):
            logger.info("[%d/%d] %s", i, len(ai_groups), group.base_name)

            # Check for a scan-time category_hint (e.g., "Paver" from keyword
            # match in scanner._register_group).  Groups with a hint bypass the
            # AI category entirely -- we synthesize a minimal result so that
            # file_ops can route the group correctly without an extra API call.
            group_row    = db.get_group(group.group_id)
            category_hint = group_row["category_hint"] if group_row else None

            if category_hint:
                logger.info(
                    "Category hint '%s' for '%s': bypassing AI category output.",
                    category_hint, group.base_name,
                )
                # Pull unit_aspect_ratio for the log line; already stored in DB.
                proc = process_results.get(group.group_id)
                if proc and proc.unit_aspect_ratio is not None:
                    logger.debug(
                        "  unit_aspect_ratio=%.3f (supporting evidence, "
                        "not used for hint-routed groups).",
                        proc.unit_aspect_ratio,
                    )
                # Synthesize a minimal AI result using the hint.
                result = {
                    "category":               category_hint,
                    "material":               "Unknown",
                    "material_type":          "Unknown",
                    "dominant_color":         "Grey",
                    "tags":                   [category_hint.lower()],
                    "is_tileable":            True,
                    "real_world_size_estimate": "unknown",
                }
                db.set_group_ai_output(group.group_id, result)
                db.update_group_status(group.group_id, GroupStatus.FILE_OPS)
            else:
                # Normal AI path -- pass unit_aspect_ratio as geometric context.
                proc             = process_results.get(group.group_id)
                unit_aspect_ratio = proc.unit_aspect_ratio if proc else None
                result = tagger.tag_group(group, unit_aspect_ratio=unit_aspect_ratio)
                if result is None:
                    continue

            # Secondary guard: if the AI says this image is not tileable but
            # it passed the Stage 3 geometry tests, route it to review instead
            # of writing to the library.  This catches product photos and
            # renders that fooled the geometric signals.
            #
            # Categories in tileability_override_categories (Art, Sky, Utility,
            # Water) are exempt -- they are intentionally non-tileable and
            # should always go to the library.
            # Hint-routed groups are also exempt (is_tileable is forced True).
            category = result.get("category", "")
            if (not category_hint
                    and not result.get("is_tileable", True)
                    and category not in override_set):
                logger.info(
                    "AI non-tileable guard: '%s' tagged '%s' with is_tileable=False "
                    "despite passing Stage 3. Routing to _needs_review/ai_not_tileable/.",
                    group.base_name, category,
                )
                db.update_group_status(
                    group.group_id, GroupStatus.REVIEW_AI_NOT_TILEABLE,
                    detail=f"ai_is_tileable_false_category_{category}",
                )
                continue

            file_ops.process_one(
                group,
                process_results.get(group.group_id),
            )

        _route_ai_not_tileable(groups, db, config)
        _route_misc(groups, db, config)

        # -------------------------------------------------------------------
        # Stage 5: Mop-up (previous-run recovery only)
        # -------------------------------------------------------------------
        logger.info("=" * 60)
        logger.info("Stage 5: File Operations Mop-up")
        logger.info("=" * 60)
        _mop_up_file_ops(db, config)

    except KeyboardInterrupt:
        logger.warning("Pipeline interrupted by user. Progress saved to database.")
    except Exception as exc:
        logger.exception("Unhandled exception: %s", exc)
        sys.exit(1)
    finally:
        _print_summary(db)
        db.shutdown()
        logger.info("Pipeline done.")


if __name__ == "__main__":
    main()
