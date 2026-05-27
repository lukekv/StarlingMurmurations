"""
file_ops.py
-----------
Phase 5: Write final output files for completed PBR groups.

For each group in FILE_OPS status:
  - Copy and rename all image maps to output/{Category}/{TextureName}/
  - Apply crop bounding box (from image_processor) during copy if present
  - Convert TIF to PNG during copy if config.convert_tif_to_png is True
  - Copy and rename any .pat files
  - Write a JSON sidecar alongside the images

Output naming convention:
  [Category]_[Material]_[Type]_[Color]_[V##]_[MAPCODE].[ext]

  The base/albedo map carries no MAPCODE suffix -- it is the default file
  that renderers such as Enscape load when pointed at the folder.
  All other PBR maps carry a standardised uppercase code:
    NORM, ROUGH, METAL, DISP, AO, OPAC, EMIS.

  Example for a single PBR set:
    Wood_Cedar_Planks_Blonde_01.png          <- base color, no suffix
    Wood_Cedar_Planks_Blonde_01_NORM.png
    Wood_Cedar_Planks_Blonde_01_ROUGH.png
    Wood_Cedar_Planks_Blonde_01_DISP.png
    Wood_Cedar_Planks_Blonde_01.json
    Wood_Cedar_Planks_Blonde_01.pat

Exception: groups with AI category 'Misc' are written to
  review_dir/misc/{TextureName}/ instead of the main output tree so
  non-texture content (technical drawings, site plans, etc.) does not
  pollute the organised library.

This module never modifies or deletes source files.
"""

import json
import logging
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image

from config import Config
from database import DatabaseManager, GroupStatus
from image_processor import CropBbox, ProcessResult
from scanner import PBRGroup

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------

def _safe_name(text: str) -> str:
    """Lowercase underscore slug. Kept for any legacy callers."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s\-]", "", slug)
    slug = re.sub(r"[\s\-]+", "_", slug)
    slug = re.sub(r"_+", "_", slug)
    return slug.strip("_") or "unnamed"


def _title_slug(text: str) -> str:
    """
    Convert AI-returned text to a TitleCase underscore slug.

    Examples:
      'board formed'  -> 'Board_Formed'
      'Alaskan Cedar' -> 'Alaskan_Cedar'
      'PLANKS'        -> 'Planks'
    """
    text = re.sub(r"[^\w\s]", "", str(text).strip())
    words = text.split()
    return "_".join(w.capitalize() for w in words) or "Unknown"


# ---------------------------------------------------------------------------
# PBR map type constants
# ---------------------------------------------------------------------------

# Map types that identify the base/albedo image.
# Files at group.base_map_path are always treated as the base map regardless
# of map_type; this set provides a secondary guard for edge cases.
_BASE_MAP_TYPES: frozenset = frozenset({
    "albedo", "base_color", "basecolor", "diffuse", "diff",
    "color", "col", "bc", "d", "texture", "base",
})

# Standardised output map codes for all non-base PBR maps.
_MAP_CODES: dict = {
    "normal":            "NORM",
    "norm":              "NORM",
    "nrm":               "NORM",
    "n":                 "NORM",
    "bump":              "NORM",
    "roughness":         "ROUGH",
    "rough":             "ROUGH",
    "rgh":               "ROUGH",
    "spec":              "ROUGH",
    "specular":          "ROUGH",
    "metallic":          "METAL",
    "metal":             "METAL",
    "met":               "METAL",
    "displacement":      "DISP",
    "disp":              "DISP",
    "height":            "DISP",
    "ao":                "AO",
    "ambient_occlusion": "AO",
    "opacity":           "OPAC",
    "opac":              "OPAC",
    "emissive":          "EMIS",
    "emis":              "EMIS",
}


# ---------------------------------------------------------------------------
# FileOps
# ---------------------------------------------------------------------------

class FileOps:
    """
    Writes all output files for PBR groups that have completed AI tagging.

    Usage::

        ops = FileOps(config, db)
        ops.process_groups(groups, process_results)
    """

    def __init__(self, config: Config, db: DatabaseManager) -> None:
        self.config = config
        self.db     = db
        self._variant_cache: dict = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def process_groups(
        self,
        groups:          List[PBRGroup],
        process_results: Dict[str, ProcessResult],
    ) -> None:
        eligible = [g for g in groups if self._should_process(g.group_id)]
        logger.info(
            "File ops: %d group(s) eligible, %d skipped.",
            len(eligible), len(groups) - len(eligible),
        )

        with ThreadPoolExecutor(
            max_workers=self.config.file_ops_workers,
            thread_name_prefix="fileops",
        ) as pool:
            future_to_group = {
                pool.submit(self._process_one, g, process_results.get(g.group_id)): g
                for g in eligible
            }
            for future in as_completed(future_to_group):
                group = future_to_group[future]
                try:
                    future.result()
                except Exception as exc:
                    logger.error("File ops failed for '%s': %s", group.base_name, exc)
                    self.db.update_group_status(
                        group.group_id, GroupStatus.AI_FAILED,
                        f"file_ops error: {exc}"
                    )

    def process_one(
        self,
        group:       PBRGroup,
        proc_result: Optional[ProcessResult],
    ) -> None:
        """
        Write output files for a single group in the calling thread.

        Called inline from the AI tagging loop immediately after each
        successful tag so output files appear on disk continuously.
        """
        if not self._should_process(group.group_id):
            return
        try:
            self._process_one(group, proc_result)
        except Exception as exc:
            logger.error("File ops failed for '%s': %s", group.base_name, exc)
            self.db.update_group_status(
                group.group_id, GroupStatus.AI_FAILED,
                f"file_ops error: {exc}",
            )

    # ------------------------------------------------------------------
    # Single-group processing (internal)
    # ------------------------------------------------------------------

    def _process_one(
        self,
        group:       PBRGroup,
        proc_result: Optional[ProcessResult],
    ) -> None:
        """Write all output files for one group."""
        group_row = self.db.get_group(group.group_id)
        ai_output = (
            json.loads(group_row["ai_output"])
            if group_row and group_row["ai_output"] else {}
        )
        file_rows: Dict[str, object] = {
            row["source_path"]: row
            for row in self.db.get_files_for_group(group.group_id)
        }

        # --- Build output name tokens ----------------------------------------
        material       = _title_slug(ai_output.get("material") or group.base_name)
        material_type  = _title_slug(ai_output.get("material_type") or "Unknown")
        dominant_color = ai_output.get("dominant_color") or "Grey"
        category       = ai_output.get("category") or "_Untagged"

        # base_slug is the name without the variant number
        base_slug = f"{category}_{material}_{material_type}_{dominant_color}"

        # Groups tagged as Misc go to review_dir/misc/ to keep the organised
        # library clean.
        if category == "Misc":
            category_dir = Path(self.config.review_dir) / "misc"
        else:
            category_dir = Path(self.config.output_dir) / category

        variant      = self._next_variant(category_dir, base_slug)
        texture_name = f"{base_slug}_{variant:02d}"
        out_dir      = category_dir / texture_name
        out_dir.mkdir(parents=True, exist_ok=True)

        source_files: Dict[str, str] = {}
        maps_written: List[str]      = []

        # --- Image files -----------------------------------------------------
        demo_counter = 0
        base_map_resolved = (
            group.base_map_path.resolve() if group.base_map_path else None
        )

        for img_path in group.image_files + group.demo_files:
            file_row = file_rows.get(str(img_path))
            map_type = (
                file_row["map_type"] if file_row
                else group.map_types.get(str(img_path), "unknown")
            )
            is_demo = file_row["is_demo"] if file_row else False

            if is_demo:
                demo_counter += 1
                suffix = (
                    f"_demo_{demo_counter:02d}"
                    if len(group.demo_files) > 1
                    else "_demo"
                )
                out_name = f"{texture_name}{suffix}{self._output_ext(img_path)}"

            elif (
                base_map_resolved is not None
                and img_path.resolve() == base_map_resolved
            ):
                # Base/albedo map: no map code suffix
                out_name = f"{texture_name}{self._output_ext(img_path)}"
                maps_written.append("base")

            else:
                # Non-base PBR map: look up standardised code
                code = _MAP_CODES.get(
                    map_type.lower() if map_type else "",
                    (map_type.upper() if map_type else "MAP"),
                )
                out_name = f"{texture_name}_{code}{self._output_ext(img_path)}"
                if not is_demo:
                    maps_written.append(code)

            out_path = out_dir / out_name
            self._write_image(img_path, out_path, proc_result)

            if file_row:
                self.db.set_file_output_path(file_row["file_id"], str(out_path))

            if not is_demo:
                source_files[map_type] = str(img_path)

        # --- PAT files -------------------------------------------------------
        has_pat = len(group.pat_files) > 0
        for pat_path in group.pat_files:
            out_path = out_dir / f"{texture_name}.pat"
            shutil.copy2(str(pat_path), str(out_path))
            file_row = file_rows.get(str(pat_path))
            if file_row:
                self.db.set_file_output_path(file_row["file_id"], str(out_path))

        # --- JSON sidecar ----------------------------------------------------
        sidecar_path = out_dir / f"{texture_name}.json"
        self._write_sidecar(
            path           = sidecar_path,
            group          = group,
            ai_output      = ai_output,
            proc_result    = proc_result,
            source_files   = source_files,
            maps           = maps_written,
            texture_name   = texture_name,
            material       = material,
            material_type  = material_type,
            dominant_color = dominant_color,
            category       = category,
            has_pat        = has_pat,
        )

        # --- Finalise in DB --------------------------------------------------
        self.db.set_group_output_path(group.group_id, str(out_dir))
        self.db.update_group_status(group.group_id, GroupStatus.COMPLETED)
        logger.info(
            "Completed: '%s' -> %s/%s", group.base_name, category, texture_name
        )

    # ------------------------------------------------------------------
    # Variant detection
    # ------------------------------------------------------------------

    def _next_variant(self, category_dir: Path, base_slug: str) -> int:
        """
        Return the next unused variant number for this base_slug.

        First call per key: scans category_dir for existing subdirectories
        matching '{base_slug}_{digits}' and caches the result. Subsequent
        calls for the same key increment the cached counter, avoiding
        repeated directory scans in large single-category runs.
        """
        cache_key = f"{category_dir}::{base_slug}"
        if cache_key in self._variant_cache:
            self._variant_cache[cache_key] += 1
            return self._variant_cache[cache_key]

        if not category_dir.exists():
            self._variant_cache[cache_key] = 1
            return 1

        pattern = re.compile(r"^" + re.escape(base_slug) + r"_(\d+)$")
        nums = []
        for entry in category_dir.iterdir():
            if not entry.is_dir():
                continue
            m = pattern.match(entry.name)
            if m:
                nums.append(int(m.group(1)))
        next_v = max(nums) + 1 if nums else 1
        self._variant_cache[cache_key] = next_v
        return next_v

    # ------------------------------------------------------------------
    # Image writing
    # ------------------------------------------------------------------

    def _write_image(
        self,
        src:         Path,
        dst:         Path,
        proc_result: Optional[ProcessResult],
    ) -> None:
        needs_crop    = proc_result is not None and proc_result.crop_bbox is not None
        is_tif        = src.suffix.lower() in (".tif", ".tiff")
        needs_convert = is_tif and self.config.convert_tif_to_png

        if needs_crop or needs_convert:
            img = Image.open(src)
            if needs_crop:
                w, h = img.size
                box  = proc_result.crop_bbox.apply_to(w, h)
                img  = img.crop(box)
            if needs_convert:
                img.save(str(dst), format="PNG")
            else:
                img.save(str(dst))
        else:
            shutil.copy2(str(src), str(dst))

    # ------------------------------------------------------------------
    # JSON sidecar
    # ------------------------------------------------------------------

    def _write_sidecar(
        self,
        path:           Path,
        group:          PBRGroup,
        ai_output:      dict,
        proc_result:    Optional[ProcessResult],
        source_files:   Dict[str, str],
        maps:           List[str],
        texture_name:   str,
        material:       str,
        material_type:  str,
        dominant_color: str,
        category:       str,
        has_pat:        bool,
    ) -> None:
        sidecar = {
            "texture_name":             texture_name,
            "material":                 material,
            "material_type":            material_type,
            "dominant_color":           dominant_color,
            "category":                 category,
            "tags":                     ai_output.get("tags", []),
            "gradient_test_passed":     (
                proc_result.is_tileable if proc_result is not None else None
            ),
            "ai_is_tileable":           ai_output.get("is_tileable"),
            "real_world_size_estimate": ai_output.get(
                "real_world_size_estimate", "unknown"
            ),
            "real_world_dimensions":    group.real_world_dimensions,
            "maps":                     sorted(set(maps)),
            "has_pat":                  has_pat,
            "source_files":             source_files,
            "processed_date":           datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _output_ext(self, src: Path) -> str:
        ext = src.suffix.lower()
        if ext in (".tif", ".tiff") and self.config.convert_tif_to_png:
            return ".png"
        if ext == ".jpeg":
            return ".jpg"
        return ext

    def _should_process(self, group_id: str) -> bool:
        row = self.db.get_group(group_id)
        if row is None:
            return False
        if row["is_duplicate"]:
            return False
        return row["status"] == GroupStatus.FILE_OPS.value
