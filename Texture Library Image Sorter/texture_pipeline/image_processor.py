"""
image_processor.py
------------------
Phase 2: Square check and center crop.
Phase 3: Tileability test (Sobel gradient, self-calibrated against image noise floor).

Both phases operate on the base map. Crops are stored as a normalized bounding box
(fractions of original dimensions) so they can be applied proportionally to every
map in a mixed-resolution PBR set at output time.

Dependencies: Pillow, opencv-python, numpy (pulled in by opencv).
"""

import concurrent.futures
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

from config import Config
from database import DatabaseManager, GroupStatus
from scanner import PBRGroup

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class CropBbox:
    """
    Normalized crop bounding box expressed as fractions (0.0 to 1.0) of the
    original image dimensions. Multiply by actual pixel size to get pixel coords.
    Always a centered square crop on the longer axis.
    """
    left:   float
    top:    float
    right:  float
    bottom: float

    def apply_to(self, width: int, height: int) -> Tuple[int, int, int, int]:
        return (
            round(self.left   * width),
            round(self.top    * height),
            round(self.right  * width),
            round(self.bottom * height),
        )


@dataclass
class ProcessResult:
    group_id:          str
    crop_bbox:         Optional[CropBbox]
    is_tileable:       bool
    binned_resolution: bool
    base_dims:         Optional[Tuple[int, int]]
    unit_aspect_ratio: Optional[float] = None


# ---------------------------------------------------------------------------
# ImageProcessor
# ---------------------------------------------------------------------------

class ImageProcessor:
    """
    Runs Phase 2 (square check and crop) and Phase 3 (tileability) on every
    PBR group passed to process_groups().

    Pre-filters run before Phase 2:
      1. Minimum resolution check        --> BINNED_RESOLUTION
      2. Blank / solid-colour check      --> BINNED_BLANK
      3. Line-art / drawing check        --> REVIEW_LINE_ART
      4. Product photo / isolated-object --> BINNED_PRODUCT_PHOTO

    CPU-bound work runs in a ThreadPoolExecutor. All DB writes go through the
    DatabaseManager queue -- worker threads never write to SQLite directly.
    """

    def __init__(self, config: Config, db: DatabaseManager):
        self.config = config
        self.db = db

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def process_groups(self, groups: List[PBRGroup]) -> Dict[str, ProcessResult]:
        results: Dict[str, ProcessResult] = {}

        processable = [g for g in groups if g.base_map_path is not None]
        no_base_map = [g for g in groups if g.base_map_path is None]

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.config.cpu_workers,
            thread_name_prefix="img-proc",
        ) as executor:
            future_to_group = {
                executor.submit(self._process_one, g): g
                for g in processable
            }
            for future in concurrent.futures.as_completed(future_to_group):
                group = future_to_group[future]
                try:
                    result = future.result()
                    results[result.group_id] = result
                except Exception as exc:
                    logger.error(
                        "Unhandled error processing group '%s': %s",
                        group.base_name, exc, exc_info=True,
                    )
                    self.db.update_group_status(
                        group.group_id, GroupStatus.TILEABILITY_FAILED,
                        detail=f"processing_error: {exc}",
                    )
                    results[group.group_id] = ProcessResult(
                        group_id=group.group_id, crop_bbox=None,
                        is_tileable=False, binned_resolution=False, base_dims=None,
                    )

        for group in no_base_map:
            results[group.group_id] = ProcessResult(
                group_id=group.group_id, crop_bbox=None,
                is_tileable=False, binned_resolution=False, base_dims=None,
            )

        logger.info(
            "Image processing complete. %d processed, %d skipped (no base map).",
            len(processable), len(no_base_map),
        )
        return results

    # ------------------------------------------------------------------
    # Per-group processing
    # ------------------------------------------------------------------

    def _process_one(self, group: PBRGroup) -> ProcessResult:
        self.db.update_group_status(group.group_id, GroupStatus.CROPPING)

        base = group.base_map_path
        try:
            img = Image.open(base)
            img.load()
            w, h = img.size
        except (UnidentifiedImageError, OSError, Exception) as exc:
            logger.error("Cannot open base map '%s': %s", base.name, exc)
            self.db.update_group_status(
                group.group_id, GroupStatus.TILEABILITY_FAILED,
                detail=f"unreadable_base_map: {exc}",
            )
            return ProcessResult(
                group_id=group.group_id, crop_bbox=None,
                is_tileable=False, binned_resolution=False, base_dims=None,
            )

        # --- Pre-filter 1: minimum resolution --------------------------------
        if min(w, h) < self.config.min_resolution_px:
            logger.info(
                "Binning '%s': base map %dx%d below %dpx minimum.",
                group.base_name, w, h, self.config.min_resolution_px,
            )
            self.db.update_group_status(
                group.group_id, GroupStatus.BINNED_RESOLUTION,
                detail=f"base_map_{w}x{h}_below_{self.config.min_resolution_px}px",
            )
            self._record_dims(group.base_map_path, w, h)
            img.close()
            return ProcessResult(
                group_id=group.group_id, crop_bbox=None,
                is_tileable=False, binned_resolution=True, base_dims=(w, h),
            )

        # --- Pre-filter 2: blank / solid-colour image ------------------------
        result = self._check_blank(img, group)
        if result is not None:
            img.close()
            return result

        # --- Pre-filter 3: line-art / technical drawing ----------------------
        result = self._check_line_art(img, group)
        if result is not None:
            img.close()
            return result

        # --- Pre-filter 4: product photo / isolated-object detection ---------
        result = self._check_product_photo(img, group)
        if result is not None:
            img.close()
            return result

        # --- Phase 2: square check and crop ----------------------------------
        crop_bbox = self._compute_crop_bbox(w, h)
        if crop_bbox:
            logger.debug(
                "Center crop queued for '%s': %dx%d (%.1f%% deviation from square).",
                group.base_name, w, h,
                (max(w, h) / min(w, h) - 1.0) * 100,
            )

        if crop_bbox:
            l, t, r, b = crop_bbox.apply_to(w, h)
            tile_img = img.crop((l, t, r, b))
        else:
            tile_img = img

        self._record_dims(group.base_map_path, w, h)

        # --- Phase 3: tileability --------------------------------------------
        self.db.update_group_status(group.group_id, GroupStatus.TILEABILITY)

        if self._has_tileability_bypass(group.base_name):
            logger.info(
                "Tileability bypassed for '%s': filename keyword match.",
                group.base_name,
            )
            is_tileable = True
        else:
            is_tileable = self._test_tileability(tile_img, group.base_name)

        if not is_tileable:
            detail = "auto_binned" if self.config.auto_bin_tileability_failures else "needs_review"
            self.db.update_group_status(
                group.group_id, GroupStatus.TILEABILITY_FAILED, detail=detail,
            )
            logger.info("Tileability FAILED: '%s'", group.base_name)
        else:
            self.db.update_group_status(group.group_id, GroupStatus.AI_TAGGING)

        # --- Stage 3 geometric pipeline: unit aspect ratio -------------------
        # Runs before img.close() so the already-decoded pixels are reused.
        # Only run on groups that are still going to AI tagging (tileable).
        # Failures here are non-fatal: geometry data is supplemental context.
        unit_aspect_ratio: Optional[float] = None
        if is_tileable and group.base_map_path is not None:
            unit_aspect_ratio = self._analyze_unit_geometry(group, img)
            if unit_aspect_ratio is not None:
                self.db.set_group_unit_aspect_ratio(
                    group.group_id, unit_aspect_ratio
                )

        if tile_img is not img:
            tile_img.close()
        img.close()

        return ProcessResult(
            group_id=group.group_id,
            crop_bbox=crop_bbox,
            is_tileable=is_tileable,
            binned_resolution=False,
            base_dims=(w, h),
            unit_aspect_ratio=unit_aspect_ratio,
        )

    # ------------------------------------------------------------------
    # Pre-filter: blank / solid-colour image detection
    # ------------------------------------------------------------------

    def _check_blank(
        self, img: Image.Image, group: PBRGroup
    ) -> Optional[ProcessResult]:
        """
        Grayscale pixel standard deviation check for solid-colour images.

        A perfectly solid-colour image has stddev=0.0. Real textures are
        typically 20+ stddev even when light-coloured. Images below
        blank_image_stddev_bin are objectively unusable (solid fills,
        corrupt renders) and are routed directly to recycle_bin/blank_images/.

        The threshold is intentionally conservative so that light-coloured
        but legitimate textures (pale parquet, white plaster, light fabric)
        are never caught here. Those materials sit well above the threshold.

        Returns a terminal ProcessResult if binned, or None to continue.
        """
        try:
            gray   = np.array(img.convert("L"), dtype=np.float32)
            stddev = float(gray.std())
        except Exception as exc:
            logger.warning(
                "Could not compute pixel stddev for '%s': %s "
                "-- skipping blank check.",
                group.base_name, exc,
            )
            return None

        threshold = self.config.blank_image_stddev_bin

        if stddev < threshold:
            logger.info(
                "Blank image binned '%s': stddev=%.2f < threshold %.1f.",
                group.base_name, stddev, threshold,
            )
            self.db.update_group_status(
                group.group_id, GroupStatus.BINNED_BLANK,
                detail=f"stddev={stddev:.2f}_below_threshold_{threshold}",
            )
            return ProcessResult(
                group_id=group.group_id, crop_bbox=None,
                is_tileable=False, binned_resolution=False, base_dims=img.size,
            )

        logger.debug(
            "Blank check passed '%s': stddev=%.2f", group.base_name, stddev
        )
        return None

    # ------------------------------------------------------------------
    # Pre-filter: line-art / technical drawing detection
    # ------------------------------------------------------------------

    def _check_line_art(
        self, img: Image.Image, group: PBRGroup
    ) -> Optional[ProcessResult]:
        """
        Detects technical drawings, site plans, CAD output, and architectural
        documents by measuring the fraction of near-white pixels.

        A real photographic texture has a spread-out histogram with very few
        pure-white pixels. A drawing on a white background typically has 60%
        or more near-white pixels (>= 240 out of 255).

        Images at or above line_art_white_pixel_threshold are routed to
        _needs_review/line_art/ for human review.

        Returns a terminal ProcessResult if flagged, or None to continue.
        """
        try:
            gray           = np.array(img.convert("L"), dtype=np.uint8)
            white_fraction = float((gray >= 240).sum()) / gray.size
        except Exception as exc:
            logger.warning(
                "Could not compute white pixel fraction for '%s': %s "
                "-- skipping line art check.",
                group.base_name, exc,
            )
            return None

        threshold = self.config.line_art_white_pixel_threshold

        if white_fraction >= threshold:
            logger.info(
                "Line art detected '%s': %.1f%% near-white pixels >= %.0f%% threshold. "
                "Routing to _needs_review/line_art/.",
                group.base_name, white_fraction * 100, threshold * 100,
            )
            self.db.update_group_status(
                group.group_id, GroupStatus.REVIEW_LINE_ART,
                detail=f"white_fraction={white_fraction:.3f}_above_threshold_{threshold}",
            )
            return ProcessResult(
                group_id=group.group_id, crop_bbox=None,
                is_tileable=False, binned_resolution=False, base_dims=img.size,
            )

        logger.debug(
            "Line art check passed '%s': %.1f%% near-white pixels",
            group.base_name, white_fraction * 100,
        )
        return None

    # ------------------------------------------------------------------
    # Pre-filter: product photo / isolated-object detection
    # ------------------------------------------------------------------

    def _check_product_photo(
        self, img: Image.Image, group: PBRGroup
    ) -> Optional[ProcessResult]:
        """
        Detects product catalog photography (isolated object on a uniform
        studio background) by measuring per-strip grayscale standard deviation
        across all four edge strips.

        A seamless tileable texture has real material content at its edges and
        will exhibit surface variation (stddev typically 15+) in at least some
        edge strips. A product photo shot against a clean black, white, or
        neutral-grey studio background has near-zero variation in ALL four edge
        strips (stddev 0-3) while only the interior contains the isolated object.

        The check: if the MAXIMUM of all four edge-strip stddevs is below
        product_photo_edge_stddev_threshold, the image is routed to
        _recycle_bin/product_photo/.

        The blank check runs first, so this function is only reached by images
        with real interior content -- a completely solid image is never passed
        here.

        Returns a terminal ProcessResult if binned, or None to continue.
        """
        try:
            strip      = self.config.tileability_edge_strip_px
            gray       = np.array(img.convert("L"), dtype=np.float32)
            h_px, w_px = gray.shape

            if h_px < strip * 4 or w_px < strip * 4:
                # Image too small to measure four independent edge strips.
                return None

            edge_stddevs = {
                "top":    float(gray[:strip,    :].std()),
                "bottom": float(gray[-strip:,   :].std()),
                "left":   float(gray[:,  :strip].std()),
                "right":  float(gray[:, -strip:].std()),
            }
            threshold    = self.config.product_photo_edge_stddev_threshold
            max_edge_std = max(edge_stddevs.values())

            if max_edge_std < threshold:
                logger.info(
                    "Product photo binned '%s': max edge stddev=%.2f < %.1f "
                    "(top=%.2f bottom=%.2f left=%.2f right=%.2f). "
                    "Routing to _recycle_bin/product_photo/.",
                    group.base_name, max_edge_std, threshold,
                    edge_stddevs["top"], edge_stddevs["bottom"],
                    edge_stddevs["left"], edge_stddevs["right"],
                )
                self.db.update_group_status(
                    group.group_id, GroupStatus.BINNED_PRODUCT_PHOTO,
                    detail=(
                        f"max_edge_stddev={max_edge_std:.2f}"
                        f"_below_threshold_{threshold}"
                    ),
                )
                return ProcessResult(
                    group_id=group.group_id, crop_bbox=None,
                    is_tileable=False, binned_resolution=False, base_dims=img.size,
                )

        except Exception as exc:
            logger.warning(
                "Could not run product photo check for '%s': %s -- skipping.",
                group.base_name, exc,
            )
            return None

        logger.debug("Product photo check passed '%s'.", group.base_name)
        return None

    # ------------------------------------------------------------------
    # Tileability helpers
    # ------------------------------------------------------------------

    def _has_tileability_bypass(self, base_name: str) -> bool:
        name_lower = base_name.lower()
        return any(kw in name_lower for kw in self.config.tileability_bypass_keywords)

    def _test_tileability(self, img: Image.Image, name: str) -> bool:
        """
        Two-signal tileability test.  Both signals must pass.

        Signal 1 -- Interior gradient spike
            Sobel magnitude at each 8px edge strip is compared to the
            INTERIOR mean (edge strips excluded from the baseline).  Using
            the interior as the denominator removes the circularity of the
            old whole-image mean and prevents directional textures with a
            strong feature near a border from being incorrectly flagged.

        Signal 2 -- Seam pixel difference
            Mean absolute RGB difference between the left strip and the
            right strip, and between the top strip and the bottom strip.
            A seamless texture wraps cleanly so opposite edges are nearly
            identical.  Artwork, renders, and non-seamless photography have
            no spatial relationship between opposite edges and score high.
            This is the primary catch for the Art category.

        Returns True (tileable) or False (seam or mismatch detected).
        """
        strip          = self.config.tileability_edge_strip_px
        grad_threshold = self.config.tileability_gradient_ratio_threshold
        seam_threshold = self.config.tileability_seam_diff_threshold

        gray = np.array(img.convert("L"),   dtype=np.float32)
        rgb  = np.array(img.convert("RGB"), dtype=np.float32)
        h_px, w_px = gray.shape

        if h_px < strip * 4 or w_px < strip * 4:
            logger.debug("Tileability skipped (image too small): '%s'", name)
            return True

        # ------------------------------------------------------------------
        # Signal 1: interior-calibrated gradient spike
        # ------------------------------------------------------------------
        gx        = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        gy        = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        magnitude = np.hypot(gx, gy)

        # Exclude edge strips from the baseline so the denominator is
        # honest -- interior content only.
        interior_magnitude = magnitude[strip:-strip, strip:-strip]
        interior_mean = float(interior_magnitude.mean())

        if interior_mean < 1e-6:
            # Completely flat interior -- gradient test is meaningless; pass it.
            grad_pass = True
            worst_ratio = 0.0
        else:
            edge_means = {
                "top":    float(magnitude[:strip,   :].mean()),
                "bottom": float(magnitude[-strip:,  :].mean()),
                "left":   float(magnitude[:,  :strip].mean()),
                "right":  float(magnitude[:, -strip:].mean()),
            }
            worst_ratio = max(v / interior_mean for v in edge_means.values())
            grad_pass   = worst_ratio <= grad_threshold

        # ------------------------------------------------------------------
        # Signal 2: opposite-edge pixel difference
        # High-pass filter removes low-frequency lighting gradients before
        # edge comparison so directionally-lit textures are not penalised.
        # ------------------------------------------------------------------
        if self.config.tileability_seam_highpass_enabled:
            blur_k = max(31, round(min(h_px, w_px) * self.config.tileability_seam_highpass_blur_fraction))
            rgb_cmp = rgb - cv2.blur(rgb, (blur_k, blur_k))
        else:
            rgb_cmp = rgb

        left_strip   = rgb_cmp[:,  :strip,  :]
        right_strip  = rgb_cmp[:, -strip:,  :]
        top_strip    = rgb_cmp[ :strip, :,  :]
        bottom_strip = rgb_cmp[-strip:, :,  :]

        h_seam_diff = float(np.abs(left_strip - right_strip).mean())
        v_seam_diff = float(np.abs(top_strip  - bottom_strip).mean())
        worst_seam  = max(h_seam_diff, v_seam_diff)
        seam_pass   = worst_seam <= seam_threshold

        is_tileable = grad_pass and seam_pass

        logger.debug(
            "Tileability '%s': interior_mean=%.2f worst_grad_ratio=%.3f "
            "grad=%s | h_seam=%.1f v_seam=%.1f worst_seam=%.1f seam=%s -> %s",
            name,
            interior_mean, worst_ratio,
            "PASS" if grad_pass else "FAIL",
            h_seam_diff, v_seam_diff, worst_seam,
            "PASS" if seam_pass else "FAIL",
            "PASS" if is_tileable else "FAIL",
        )
        return is_tileable

    # ------------------------------------------------------------------
    # Stage 3 geometric pipeline: unit aspect ratio
    # ------------------------------------------------------------------

    def _analyze_unit_geometry(
        self, group: PBRGroup, img: Image.Image
    ) -> Optional[float]:
        """
        Estimate the aspect ratio (width / height) of the repeating unit in a
        tileable texture by detecting mortar joint / grout line positions via
        Sobel gradient profiles.

        Algorithm:
          1. Convert the already-open base map to grayscale, resize to
             unit_geometry_max_px on the long axis (cheap; peak position does
             not need full resolution).
          2. Compute Sobel X and Y gradient magnitudes.
          3. Build two 1-D profiles:
               row_profile[r] = mean absolute Y-gradient across row r
               col_profile[c] = mean absolute X-gradient across column c
             Peaks in the row profile correspond to horizontal mortar joints
             (brick rows / tile rows); peaks in the column profile to vertical
             joints (brick/tile columns).
          4. Custom peak finder (no scipy): threshold at
               mean + unit_geometry_peak_k * std
             then extract local maxima with a minimum separation of
             max_px // 16 pixels (prevents double-counting wide joint edges).
          5. unit_aspect_ratio = median_col_spacing / median_row_spacing
             (horizontal unit width / vertical unit height).
          6. Return None if fewer than unit_geometry_min_peaks peaks are found
             in either direction -- geometry signal is too weak to trust.

        Returns the float ratio, or None on any failure.
        """
        max_px    = self.config.unit_geometry_max_px
        peak_k    = self.config.unit_geometry_peak_k
        min_peaks = self.config.unit_geometry_min_peaks

        try:
            gray_img = img.convert("L")
            iw, ih   = gray_img.size
            scale    = min(max_px / max(iw, ih), 1.0)
            if scale < 1.0:
                gray_img = gray_img.resize(
                    (max(1, round(iw * scale)), max(1, round(ih * scale))),
                    Image.LANCZOS,
                )
            gray = np.array(gray_img, dtype=np.float32)
            gray_img.close()
        except Exception as exc:
            logger.debug(
                "Unit geometry: could not convert '%s': %s",
                group.base_map_path.name, exc,
            )
            return None

        try:
            gx  = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
            gy  = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
            abs_gx = np.abs(gx)
            abs_gy = np.abs(gy)

            # Row profile: strong horizontal edges (brick rows, tile grout lines)
            row_profile = abs_gy.mean(axis=1)   # shape: (H,)
            # Col profile: strong vertical edges (brick/tile column joints)
            col_profile = abs_gx.mean(axis=0)   # shape: (W,)

            def _find_peak_spacing(profile: np.ndarray) -> Optional[float]:
                n = len(profile)
                if n < 4:
                    return None
                threshold   = profile.mean() + peak_k * profile.std()
                min_sep     = max(2, n // 16)
                peaks       = []
                i           = 0
                while i < n:
                    if profile[i] >= threshold:
                        # Find local maximum in the next min_sep window
                        window_end = min(i + min_sep, n)
                        local_max  = int(np.argmax(profile[i:window_end])) + i
                        if not peaks or (local_max - peaks[-1]) >= min_sep:
                            peaks.append(local_max)
                        i = local_max + min_sep
                    else:
                        i += 1
                if len(peaks) < min_peaks:
                    return None
                spacings = [peaks[j + 1] - peaks[j] for j in range(len(peaks) - 1)]
                return float(np.median(spacings))

            row_spacing = _find_peak_spacing(row_profile)   # vertical unit height
            col_spacing = _find_peak_spacing(col_profile)   # horizontal unit width

            if row_spacing is None or col_spacing is None:
                logger.debug(
                    "Unit geometry: insufficient peaks for '%s' "
                    "(row_spacing=%s col_spacing=%s). Skipping.",
                    group.base_name, row_spacing, col_spacing,
                )
                return None

            ratio = col_spacing / row_spacing
            logger.debug(
                "Unit geometry '%s': row_spacing=%.1f col_spacing=%.1f ratio=%.3f",
                group.base_name, row_spacing, col_spacing, ratio,
            )
            return round(ratio, 3)

        except Exception as exc:
            logger.debug(
                "Unit geometry analysis failed for '%s': %s",
                group.base_name, exc,
            )
            return None

    # ------------------------------------------------------------------
    # Phase 2 helpers
    # ------------------------------------------------------------------

    def _compute_crop_bbox(self, w: int, h: int) -> Optional[CropBbox]:
        if w == h:
            return None
        ratio = (max(w, h) / min(w, h)) - 1.0
        if ratio > self.config.square_tolerance:
            return None

        if w > h:
            offset = (w - h) / 2.0
            return CropBbox(
                left=offset / w, top=0.0,
                right=(offset + h) / w, bottom=1.0,
            )
        else:
            offset = (h - w) / 2.0
            return CropBbox(
                left=0.0, top=offset / h,
                right=1.0, bottom=(offset + w) / h,
            )

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _record_dims(self, path: Path, width: int, height: int) -> None:
        fid = hashlib.sha256(str(path).encode()).hexdigest()[:16]
        self.db.set_file_dimensions(fid, width, height)
