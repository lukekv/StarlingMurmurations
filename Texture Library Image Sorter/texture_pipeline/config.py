"""
config.py
---------
Single source of truth for all pipeline parameters.
Edit values here before running. Nothing is hardcoded elsewhere.

Paths are relative by default. Override with absolute paths for production runs.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


@dataclass
class Config:

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    input_dir: Path = Path(".")
    output_dir: Path = Path("./output")
    recycle_bin_dir: Path = Path("./_recycle_bin")
    review_dir: Path = Path("./_needs_review")
    db_path: Path = Path("./pipeline_state.db")
    duplicate_report_path: Path = Path("./duplicate_report.txt")

    # ------------------------------------------------------------------
    # Pre-filter
    # ------------------------------------------------------------------

    # Images below this on their shortest dimension go to recycle bin immediately.
    # Applied to the base map only.
    min_resolution_px: int = 512

    # ------------------------------------------------------------------
    # Blank / solid-colour image detection (Stage 3 pre-filter)
    # ------------------------------------------------------------------

    # Standard deviation of grayscale pixel values in the base map.
    # A perfectly solid-colour image has stddev=0.0; real textures are
    # typically 20+ stddev. Computed on the already-open PIL image at no
    # extra I/O cost.
    #
    # Images with stddev below this value are routed to
    # recycle_bin/blank_images/ as objectively unusable.
    #
    # Empirical calibration against a real library of 20k+ textures:
    # - Genuinely blank images (solid black/white exports, flat metalness
    #   maps, uniform AO): 0.0 to ~1.5 stddev.
    # - Solid-colour paint swatches and fabric samples with no surface
    #   detail: 1.5 to ~2.0 stddev.
    # - Legitimate subtle-detail textures (light plaster, pale parquet,
    #   polished marble, smooth leather): 2.0+ stddev.
    #
    # 2.0 is the empirically correct breakpoint. The prior value of 8.0
    # was based on an incorrect assumption that subtle textures sit at
    # 15+ stddev -- real library data shows they sit as low as 2.0.
    blank_image_stddev_bin: float = 2.0

    # ------------------------------------------------------------------
    # Product photo / isolated-object detection (Stage 3 pre-filter)
    # ------------------------------------------------------------------

    # Maximum per-strip grayscale standard deviation allowed across ALL
    # four edge strips before an image is classified as a product catalog
    # photo (an isolated object on a clean studio background).
    #
    # A seamless tileable texture has surface variation across its edges
    # because the edges represent real material content. A product photo
    # shot against a black, white, or neutral-grey studio background has
    # near-zero variation in all four edge strips (stddev approaching 0)
    # while the interior contains the isolated object.
    #
    # If the MAXIMUM of all four edge-strip stddevs is below this value
    # the image is routed to _recycle_bin/product_photo/.
    #
    # 10.0 is intentionally conservative. Real seamless textures -- even
    # dark ones -- have material surface variation at their edges and
    # will score above this. Manufacturer product catalog shots against
    # clean backgrounds score 0-3 and are comfortably caught.
    # Raise this value if you want to catch gradient-background product
    # photos as well (they score 5-15 depending on the falloff).
    product_photo_edge_stddev_threshold: float = 10.0

    # ------------------------------------------------------------------
    # Line-art / technical drawing detection (Stage 3 pre-filter)
    # ------------------------------------------------------------------

    # Fraction (0.0 to 1.0) of grayscale pixels that must be near-white
    # (>= 240 out of 255) before an image is flagged as a likely technical
    # drawing, site plan, CAD output, or architectural document.
    #
    # A real photographic texture has a spread-out histogram with very few
    # pure-white pixels. A drawing on a white background typically has 60%
    # or more near-white pixels.
    #
    # Flagged images are routed to _needs_review/line_art/ for human review.
    # Lower this value to catch more borderline cases; raise it if light
    # photographic textures (white plaster, snow) are being incorrectly flagged.
    line_art_white_pixel_threshold: float = 0.60

    # ------------------------------------------------------------------
    # Grouping (Phase 1)
    # ------------------------------------------------------------------

    fuzzy_match_threshold: int = 85

    # ------------------------------------------------------------------
    # Square check and cropping (Phase 2)
    # ------------------------------------------------------------------

    # 0.02 = 2% tolerance (e.g., 1024x1045 gets cropped to 1024x1024).
    square_tolerance: float = 0.02

    # ------------------------------------------------------------------
    # Tileability (Phase 3)
    # ------------------------------------------------------------------

    tileability_edge_strip_px: int = 8

    # Gradient spike ratio.  Edge strip gradient mean is compared to the
    # INTERIOR mean (edge strips excluded from the baseline).  1.8 gives
    # directional textures (parquet, corrugated metal) enough margin while
    # still catching hard seams introduced by mismatched tiling attempts.
    tileability_gradient_ratio_threshold: float = 1.8

    # Mean absolute pixel difference (0-255 scale, RGB) between opposite
    # edge strips -- left vs. right, top vs. bottom.  A seamless texture
    # wraps cleanly, so these strips should be near-identical.  Artwork,
    # renders, and non-tileable photography score 40-80+ here.
    # 25 is a deliberately conservative threshold: it passes textures with
    # subtle colour shift at the crop boundary while reliably rejecting
    # compositional images that have no relationship between opposite edges.
    #
    # NOTE: if tileability_seam_highpass_enabled is True (below), the diff
    # is computed on the high-pass (lighting-corrected) image rather than
    # raw RGB, and this threshold may need re-calibration.  Check debug
    # log seam diff values after first enabling the high-pass filter.
    tileability_seam_diff_threshold: float = 25.0

    # When True, the seam diff (Signal 2) is computed on a high-pass
    # version of the image -- the full RGB minus a large box-blur that
    # isolates low-frequency lighting.  This prevents directional studio
    # lighting (one side brighter than the other) from triggering false
    # non-tileable results on correctly seamless textures.
    #
    # Set to False to disable and compare raw RGB (original behaviour).
    tileability_seam_highpass_enabled: bool = True

    # Blur kernel size as a fraction of the shorter image axis.
    # blur_k = max(31, min(h_px, w_px) * fraction)
    # 0.125 = 12.5% of the shorter axis -- enough to capture lighting
    # gradients while leaving structural texture detail intact.
    tileability_seam_highpass_blur_fraction: float = 0.125

    # Offset-seam projected gradient ratio threshold (Signal 3).
    # The image is rolled 50 % in X and Y so the tile seam appears at centre.
    # A 1-D gradient magnitude profile is then projected along each axis;
    # this threshold compares the mean gradient at the centre-seam strip
    # (±edge_strip_px around the midpoint) to the mean across the remainder
    # of the image (excluding both the centre strip and the outer edges).
    #
    # 1.0 = centre is identical to the rest (perfectly seamless).
    # A value above ~1.5 indicates a visible seam -- e.g. brick courses
    # that are misaligned when tiled, creating a prominent ridge line where
    # the two halves meet.  The plain colour-difference test (Signal 2)
    # cannot catch this because opposite edges are the same colour; the
    # phase of the pattern is wrong but the pixel values are not.
    #
    # 2.0 is the empirically calibrated default (raised from 1.5 after a
    # first-run failure rate of ~65% showed 1.5 was too aggressive for real
    # professional libraries).  A clearly misaligned texture like a brick
    # sheet cut at the wrong row typically scores 2.5–4.0; legitimately
    # seamless textures with strong interior features (heavy grain, dominant
    # mortar lines) typically score 1.0–1.8.  Lower toward 1.5 to catch
    # subtler misalignments; raise above 2.5 if false positives persist.
    tileability_offset_seam_ratio_threshold: float = 2.0

    # Filenames containing any of these keywords (case-insensitive) bypass
    # both tileability signals and are treated as confirmed tileable.
    #
    # The first two ("seamless", "tileable") are quality confirmations --
    # the source confirms the texture wraps.
    #
    # The remainder are Option B category hints -- files whose names
    # indicate Utility overlays or Sky maps skip tileability because
    # those categories are inherently non-tileable by design.  Option A
    # (the AI override pass) catches any that slip through with
    # non-descriptive filenames.
    tileability_bypass_keywords: List[str] = field(default_factory=lambda: [
        # Quality confirmations
        "seamless",
        "tileable",
        # Utility / imperfection overlay hints
        "grunge",
        "scratch",
        "overlay",
        "imperfection",
        "fingerprint",
        "smudge",
        "edgewear",
        "edge_wear",
        "rustdrip",
        "rust_drip",
        "dirtmap",
        "dirt_map",
        # Sky / environment map hints
        "sky",
        "hdri",
        "hdr",
        "panorama",
        "pano",
    ])

    # When False (default): ALL tileability failures go to _needs_review.
    auto_bin_tileability_failures: bool = False

    # When True: skip pre-filters 2–4 (blank, line-art, product-photo) and
    # the tileability test entirely.  Use when the input source is known-good
    # (e.g. a professional seamless texture library) and you want maximum
    # throughput.  Pre-filter 1 (minimum resolution) still runs.
    skip_quality_checks: bool = False

    # ------------------------------------------------------------------
    # pHash deduplication
    # ------------------------------------------------------------------

    phash_hamming_threshold: int = 4

    # Maximum pixel count (width x height) the deduplicator will attempt
    # to load for pHash computation.  Images exceeding this are skipped
    # with a warning rather than triggering Pillow DecompressionBomb
    # errors or stalling for tens of seconds on very large renders.
    #
    # 100_000_000 = 100 MP (roughly a 10000x10000 image).
    # Real PBR textures are almost never above this; render outputs,
    # entourage sheets, and panoramas routinely are.
    max_pixels_for_phash: int = 100_000_000

    # ------------------------------------------------------------------
    # AI tagging (Phase 4)
    # ------------------------------------------------------------------

    ai_base_url: str = "http://localhost:11434/v1"

    # ---------------------------------------------------------------
    # Model selection -- change this field to switch local models.
    #
    # To find your installed models:
    #   ollama list
    #
    # Common model strings (use the exact Name from ollama list output):
    #   llama3.2-vision        -- 11B multimodal, good balance of speed and accuracy
    #   llama3.2-vision:90b    -- 90B variant, slower, higher accuracy
    #   gemma4:12b             -- Google Gemma 4 12B (requires ~10GB VRAM at Q4)
    #   gemma4:4b              -- Lighter fallback if VRAM is constrained
    #   llava:13b              -- Older multimodal option, lower JSON reliability
    #
    # The model MUST support vision (image input). Text-only models will
    # fail at the AI tagging stage with a 400 error.
    # ---------------------------------------------------------------
    ai_model: str = "gemma4:e4b"

    ai_input_resolution: int = 1024
    ai_max_retries: int = 3
    ai_retry_base_delay: float = 2.0
    ai_timeout: int = 120
    ai_api_key: str = "ollama"

    # ------------------------------------------------------------------
    # Performance
    # ------------------------------------------------------------------

    cpu_workers: int = 6
    file_ops_workers: int = 4

    # ------------------------------------------------------------------
    # Categories (must match Ollama prompt exactly)
    # ------------------------------------------------------------------

    categories: List[str] = field(default_factory=lambda: [
        "Art",
        "Brick",
        "Concrete",
        "Fabric",
        "Glass",
        "Ground",
        "Laminate",
        "Leather",
        "Metal",
        "Misc",
        "Patterns",
        "Paver",
        "Plaster and Stucco",
        "Rammed Earth",
        "Rug",
        "Shingle",
        "Sky",
        "Stone",
        "Tile",
        "Utility",
        "WallCovering",
        "Water",
        "Wood",
    ])

    # ------------------------------------------------------------------
    # Tileability override categories (Option A)
    # ------------------------------------------------------------------

    # After Stage 3, any group whose AI-returned category is in this list
    # has its tileability failure overridden and is routed to the library.
    # These categories are inherently non-tileable by nature and should
    # never be stuck in _needs_review/tileability_failed/.
    #
    # Add categories here to extend the override without touching code.
    tileability_override_categories: List[str] = field(default_factory=lambda: [
        "Art",
        "Sky",
        "Utility",
        "Water",
    ])

    # ------------------------------------------------------------------
    # Directory exclusions
    # ------------------------------------------------------------------

    # Folder names (case-insensitive, exact component match) that the
    # scanner skips entirely -- including all their subdirectories.
    # Use this to exclude entourage libraries, render caches, archive
    # folders, or any non-texture content mixed into the library root.
    #
    # Match is against the folder name only, not the full path, so
    # "Cut Out Libary" will exclude that folder wherever it appears.
    exclude_dirs: List[str] = field(default_factory=lambda: [
        "Cut Out Libary",          # entourage PNG cutouts, not PBR textures
        "ChaosGroupTextureCache",  # V-Ray internal render cache, not real images
        "Single planks",           # per-plank compositing source scans, not tileable textures
    ])

    # ------------------------------------------------------------------
    # File format handling
    # ------------------------------------------------------------------

    supported_image_formats: List[str] = field(default_factory=lambda: [
        ".jpg", ".jpeg", ".png", ".tif", ".tiff"
    ])

    # File extensions (case-insensitive) that flag a directory as a 3D mesh
    # asset rather than a flat tileable texture library.  If any file with
    # one of these extensions is found in the SAME directory as a texture
    # group, the entire group is routed to _needs_review/mesh_asset/ instead
    # of entering the processing pipeline.  The textures in that folder are
    # PBR maps bound to a specific mesh -- not standalone tileable materials.
    mesh_asset_extensions: List[str] = field(default_factory=lambda: [
        ".fbx", ".obj", ".glb", ".gltf", ".abc",
    ])

    review_formats: List[str] = field(default_factory=lambda: [
        ".psd", ".gif"
    ])

    passthrough_formats: List[str] = field(default_factory=lambda: [
        ".pat"
    ])

    skip_filenames: List[str] = field(default_factory=lambda: [
        "thumbs.db", "desktop.ini", ".ds_store"
    ])

    convert_tif_to_png: bool = True

    # ------------------------------------------------------------------
    # Base map identification
    # ------------------------------------------------------------------

    base_map_tier1_suffixes: List[str] = field(default_factory=lambda: [
        "_albedo", "_basecolor", "_base_color", "_diffuse", "_diff",
        "_col", "_color", "_bc", "_d",
    ])

    base_map_terminal_words: List[str] = field(default_factory=lambda: [
        "texture", "color", "colour", "diffuse", "albedo"
    ])

    non_base_map_suffixes: List[str] = field(default_factory=lambda: [
        "_normal", "_norm", "_nrm", "_n",
        "_roughness", "_rough", "_rgh",
        "_metallic", "_metal", "_met",
        "_displacement", "_disp", "_height",
        "_ao", "_ambientocclusion", "_ambient_occlusion",
        "_opacity", "_emissive", "_emit",
        "_bump", "_bmp",
        "_spec", "_specular",
        "_gloss", "_glossiness",
        "_fuzz",
        "_mask",
    ])

    demo_keywords: List[str] = field(default_factory=lambda: [
        "demo", "preview", "thumb", "thumbnail", "render",
        # 3D render preview geometry (sphere, cube mesh with material applied)
        "sphere", "cube",
    ])

    # ------------------------------------------------------------------
    # Paver keyword detection (scan-time category_hint)
    # ------------------------------------------------------------------

    # Any of these tokens appearing in the base_name (split on _ - and space,
    # case-insensitive, exact token match) cause the group to receive
    # category_hint = "Paver" at scan time, bypassing AI category output
    # at Stage 4.  Token-split matching avoids false positives such as
    # "cobalt" matching "cobble".
    paver_keywords: List[str] = field(default_factory=lambda: [
        "paver", "pavers", "paving",
        "cobble", "cobblestone", "cobblestones",
        "sett", "setts",
        "flagstone", "flagstones",
        "bluestone",
        "courtyard",
        "plaza",
    ])

    # ------------------------------------------------------------------
    # Filename category keyword hints (Stage 4 AI tagging)
    # ------------------------------------------------------------------

    # Per-category keyword lists used to score the original source filename.
    # At AI tagging time, the filename tokens are matched against these sets.
    # A match injects a HIGH-CONFIDENCE hint into the AI prompt: the model is
    # told the filename strongly indicates a specific category and should
    # classify accordingly unless the visual content clearly contradicts it.
    # The AI always makes the final call -- this is a weighted signal, not an
    # override.  Add or remove keywords freely; false-positive risk is low
    # because matching is token-exact (split on _, -, space).
    # Paver is handled separately via paver_keywords (hard scan-time override)
    # and does not need an entry here.
    category_keywords: Dict[str, List[str]] = field(default_factory=lambda: {
        "Metal":        ["metal", "steel", "brass", "copper", "iron", "aluminum",
                         "aluminium", "bronze", "chrome", "zinc", "corten", "tin"],
        "Wood":         ["wood", "timber", "oak", "pine", "walnut", "cedar",
                         "teak", "mahogany", "parquet", "plywood", "bamboo",
                         "hardwood", "birch", "maple", "ash", "beech"],
        "Brick":        ["brick", "masonry", "clinker"],
        "Concrete":     ["concrete", "cement"],
        "Stone":        ["marble", "granite", "limestone", "travertine", "slate",
                         "sandstone", "quartzite", "onyx", "basalt", "terrazzo"],
        "Fabric":       ["fabric", "linen", "cotton", "velvet", "tweed",
                         "textile", "woven", "wool", "silk"],
        "Leather":      ["leather", "suede", "nubuck", "hide"],
        "Glass":        ["glass", "glazing"],
        "Sky":          ["sky", "hdri", "panorama", "cloudscape"],
        "Rammed Earth": ["rammed", "adobe"],
        "Rug":          ["rug", "kilim", "sisal", "jute", "seagrass"],
        "Water":        ["water", "ocean", "river", "lake"],
    })

    # ------------------------------------------------------------------
    # Unit geometry analysis (Stage 3 geometric pipeline)
    # ------------------------------------------------------------------

    # Maximum pixel dimension the base map is resized to before gradient
    # computation.  Smaller = faster; 512 is sufficient for peak detection.
    unit_geometry_max_px: int = 512

    # Sobel gradient magnitude threshold multiplier for peak detection.
    # A column/row is considered a candidate mortar joint peak when its
    # gradient profile value exceeds:  mean + unit_geometry_peak_k * std
    # Higher values = only strong, clear joints register; lower = more
    # sensitive but may pick up surface texture noise.
    unit_geometry_peak_k: float = 0.5

    # Minimum number of peaks that must be detected in BOTH the row and
    # column profiles before a unit_aspect_ratio is recorded.  Fewer
    # peaks than this in either direction means the geometry signal is
    # too weak to trust and None is stored instead.
    unit_geometry_min_peaks: int = 2

    # Expected unit_aspect_ratio ranges (width / height of detected unit).
    # Used by ai_tagger.py to build the contextual note injected into the
    # prompt, and by main.py for optional post-AI disambiguation.
    #
    #  Standard running-bond brick (wall):   2.5 - 3.5
    #  Brick paver (square-ish orientation): 1.0 - 1.8
    #  Square floor tile:                    0.9 - 1.1
    #  Subway tile (2:1 format):             1.8 - 2.2
    #
    # If ratio is outside all ranges the geometry note still reports it
    # as "unclassified" so the AI has the raw number.
    unit_geometry_brick_ratio_min: float = 2.3
    unit_geometry_brick_ratio_max: float = 3.8
    unit_geometry_tile_square_ratio_min: float = 0.85
    unit_geometry_tile_square_ratio_max: float = 1.15
    unit_geometry_tile_subway_ratio_min: float = 1.6
    unit_geometry_tile_subway_ratio_max: float = 2.4
    unit_geometry_paver_ratio_min: float = 0.85
    unit_geometry_paver_ratio_max: float = 1.9

    # ------------------------------------------------------------------
    # Dimension scraping
    # ------------------------------------------------------------------

    dimension_units: List[str] = field(default_factory=lambda: [
        "inches", "inch", "in",
        "feet", "foot", "ft",
        "centimeters", "centimeter", "centimetres", "centimetre", "cm",
        "millimeters", "millimeter", "millimetres", "millimetre", "mm",
        "meters", "meter", "metres", "metre", "m",
    ])
