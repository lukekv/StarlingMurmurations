"""
Tests for ImageProcessor pre-filters, tileability signals, and crop math.

All images are synthetic, generated in-memory with numpy -- no fixture files,
no reads from the texture library, no SQLite, no network. Database writes go
to a stub that records status updates.
"""

from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from config import Config
from database import GroupStatus
from image_processor import CropBbox, ImageProcessor


class StubDB:
    """Records DatabaseManager calls without touching SQLite."""

    def __init__(self):
        self.status_updates = []

    def update_group_status(self, group_id, status, detail=None):
        self.status_updates.append((group_id, status, detail))

    def set_file_dimensions(self, file_id, width, height):
        pass

    def set_group_unit_aspect_ratio(self, group_id, ratio):
        pass

    @property
    def last_status(self):
        return self.status_updates[-1][1] if self.status_updates else None


def make_processor(**config_overrides):
    db = StubDB()
    return ImageProcessor(Config(**config_overrides), db), db


def make_group(name="synthetic"):
    return SimpleNamespace(group_id="g-test", base_name=name, base_map_path=None)


def to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(arr.astype(np.uint8))


def noise_image(size=256, low=0, high=256, seed=42) -> Image.Image:
    rng = np.random.default_rng(seed)
    return to_image(rng.integers(low, high, (size, size), dtype=np.uint8))


# ---------------------------------------------------------------------------
# Pre-filter: blank / solid-colour detection
# ---------------------------------------------------------------------------

class TestCheckBlank:

    def test_solid_colour_image_is_binned(self):
        proc, db = make_processor()
        img = to_image(np.full((256, 256), 128))
        result = proc._check_blank(img, make_group())
        assert result is not None
        assert result.is_tileable is False
        assert db.last_status == GroupStatus.BINNED_BLANK

    def test_textured_image_passes(self):
        proc, db = make_processor()
        result = proc._check_blank(noise_image(), make_group())
        assert result is None
        assert db.status_updates == []


# ---------------------------------------------------------------------------
# Pre-filter: line-art / technical drawing detection
# ---------------------------------------------------------------------------

class TestCheckLineArt:

    def test_drawing_on_white_background_is_flagged(self):
        proc, db = make_processor()
        # White sheet with a black line every 20th row: 95% near-white pixels.
        arr = np.full((200, 200), 255)
        arr[::20, :] = 0
        result = proc._check_line_art(to_image(arr), make_group())
        assert result is not None
        assert db.last_status == GroupStatus.REVIEW_LINE_ART

    def test_midtone_texture_passes(self):
        proc, db = make_processor()
        # Noise capped below the near-white cutoff (240) -- zero white pixels.
        result = proc._check_line_art(noise_image(high=240), make_group())
        assert result is None
        assert db.status_updates == []


# ---------------------------------------------------------------------------
# Pre-filter: product photo / isolated-object detection
# ---------------------------------------------------------------------------

class TestCheckProductPhoto:

    def test_object_on_uniform_background_is_binned(self):
        proc, db = make_processor()
        # Flat grey studio background, detailed object in the centre:
        # all four 8px edge strips have stddev 0.
        rng = np.random.default_rng(7)
        arr = np.full((256, 256), 180)
        arr[96:160, 96:160] = rng.integers(0, 256, (64, 64))
        result = proc._check_product_photo(to_image(arr), make_group())
        assert result is not None
        assert db.last_status == GroupStatus.BINNED_PRODUCT_PHOTO

    def test_full_frame_texture_passes(self):
        proc, db = make_processor()
        result = proc._check_product_photo(noise_image(), make_group())
        assert result is None
        assert db.status_updates == []

    def test_image_too_small_to_measure_is_skipped(self):
        proc, db = make_processor()
        result = proc._check_product_photo(to_image(np.full((16, 16), 180)), make_group())
        assert result is None


# ---------------------------------------------------------------------------
# Tileability (3-signal test)
# ---------------------------------------------------------------------------

class TestTileability:

    def test_perfectly_periodic_texture_passes(self):
        proc, _ = make_processor()
        # A texture whose period (8px) divides both the image size (512) and
        # the opposite-strip offset (512 - 8 = 504): the left/right and
        # top/bottom 8px strips are pixel-identical and rolling 50% maps the
        # image onto itself -- seamless by construction for all three signals.
        rng = np.random.default_rng(42)
        patch = rng.integers(0, 256, (8, 8), dtype=np.uint8)
        img = to_image(np.tile(patch, (64, 64)))
        assert proc._test_tileability(img, "periodic") is True

    def test_hard_phase_seam_fails(self):
        proc, _ = make_processor()
        # Left half dark, right half light. Rolling 50% puts the hard
        # boundary at centre, which Signal 3 (offset seam) must catch.
        arr = np.zeros((512, 512))
        arr[:, 256:] = 255
        img = to_image(arr)
        assert proc._test_tileability(img, "half-and-half") is False

    def test_raw_seam_mismatch_fails_without_highpass(self):
        proc, _ = make_processor(tileability_seam_highpass_enabled=False)
        # Opposite edges with completely unrelated content (independent noise)
        # must fail Signal 2 when comparing raw RGB.
        assert proc._test_tileability(noise_image(size=512), "raw-noise") is False

    def test_tiny_image_skips_test_and_passes(self):
        proc, _ = make_processor()
        # Below 4x the edge strip size the test is skipped entirely.
        assert proc._test_tileability(noise_image(size=16), "tiny") is True

    def test_bypass_keywords(self):
        proc, _ = make_processor()
        assert proc._has_tileability_bypass("Brick_seamless_4k") is True
        assert proc._has_tileability_bypass("sky_panorama") is True
        assert proc._has_tileability_bypass("RedBrick") is False


# ---------------------------------------------------------------------------
# Crop bbox math
# ---------------------------------------------------------------------------

class TestComputeCropBbox:

    def test_square_image_needs_no_crop(self):
        proc, _ = make_processor()
        assert proc._compute_crop_bbox(1024, 1024) is None

    def test_within_tolerance_gets_centered_crop(self):
        proc, _ = make_processor()
        # 1024x1040 is 1.5625% off square -- inside the 2% tolerance.
        bbox = proc._compute_crop_bbox(1024, 1040)
        assert bbox is not None
        assert bbox.apply_to(1024, 1040) == (0, 8, 1024, 1032)

    def test_wide_image_within_tolerance(self):
        proc, _ = make_processor()
        bbox = proc._compute_crop_bbox(1040, 1024)
        assert bbox.apply_to(1040, 1024) == (8, 0, 1032, 1024)

    def test_far_from_square_is_not_cropped(self):
        proc, _ = make_processor()
        # 2:1 image is NOT crop-rescued; it proceeds uncropped.
        assert proc._compute_crop_bbox(2048, 1024) is None

    def test_apply_to_round_trips_to_square(self):
        bbox = CropBbox(left=0.0, top=0.25, right=1.0, bottom=0.75)
        left, top, right, bottom = bbox.apply_to(100, 200)
        assert (right - left) == (bottom - top) == 100
