"""
ai_tagger.py
------------
Phase 4: AI-based texture classification and tagging via a local
Ollama or LM Studio instance.

Single-threaded by design -- GPU is the bottleneck and Ollama serialises
inference regardless. The main pipeline feeds groups to this module one
at a time from a dedicated worker thread.
"""

import base64
import json
import logging
import re
import time
from io import BytesIO
from pathlib import Path
from typing import List, Optional

from openai import OpenAI, APIConnectionError, APIStatusError, APITimeoutError
from PIL import Image
from pydantic import BaseModel, ValidationError, field_validator

from config import Config
from database import DatabaseManager, GroupStatus
from scanner import PBRGroup

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompt -- keep in sync with config.categories
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a professional texture library classifier for architectural "
    "visualization. Analyse the texture image and return a single JSON object "
    "with exactly these fields:\n"
    "  category              : one of the allowed category strings listed below\n"
    "  material              : the base substance, 1-2 words, title case "
    "(e.g. Cedar, Concrete, Slate, Steel)\n"
    "  material_type         : the form, finish, or application type, 1-2 words, "
    "title case (e.g. Planks, Block, Polished, Corrugated)\n"
    "  dominant_color        : the dominant visual color tone of the texture, "
    "exactly one value from the color list provided in the user message\n"
    "  tags                  : array of 3-8 lowercase tags using underscores\n"
    "  is_tileable           : true if the texture appears seamlessly tileable\n"
    "  is_render_preview     : true if this image is a 3D rendered preview of a material "
    "(e.g. a VRay, Corona, Blender, or Arnold render swatch, material ball, or any "
    "computer-generated preview image) rather than a flat seamless texture captured by "
    "photography, photogrammetry scan, or procedural generation as a tiling asset. Signs "
    "include baked directional lighting, specular highlights baked into the diffuse "
    "channel, ambient occlusion, render anti-aliasing, or a clearly synthetic rendered "
    "visual quality. Set false for real photographs and genuine seamless texture maps.\n"
    "  real_world_size_estimate : estimated real-world size e.g. '1m x 1m' or 'unknown'\n"
    "\n"
    "CRITICAL CLASSIFICATION RULES:\n"
    "1. Category must reflect the MATERIAL IDENTITY of the texture, not where it "
    "might be applied. A brick texture is always Brick even if bricks are used on "
    "walls. A tile texture is always Tile even if tiles are used on floors. Never "
    "assign WallCovering or Ground to a texture that has its own specific material "
    "category.\n"
    "2. Misc is NOT a quality bin. Use Misc ONLY when no specific material category "
    "fits. If the image is a technical drawing, site plan, floor plan, CAD output, "
    "blueprint, or any non-photographic illustration, assign Misc and set is_tileable "
    "to false. This includes architectural hatch patterns that visually resemble "
    "real material textures. In all other cases, choose the closest specific "
    "material category.\n"
    "3. Art is for deliberate decorative artwork only: paintings, murals, large-format "
    "printed wall panels, and canvas art. Art is NOT a catch-all for images that "
    "look unusual. Set is_tileable to false for Art.\n"
    "4. If the texture appears to be a siding or cladding product -- such as lap "
    "siding, board and batten, corrugated cladding, rainscreen panels, or shiplap -- "
    "always include the tag 'siding' in the tags array, regardless of which material "
    "category is assigned.\n"
    "5. TILE HARD OVERRIDE: If the image shows ANY visible grout lines, joint lines, "
    "or individual tile units at any scale -- regardless of how fine, ornate, or "
    "heavily textured the surface is -- the category is Tile unconditionally. This "
    "overrides all other category considerations including Plaster and Stucco, Stone, "
    "Concrete, and WallCovering.\n"
    "6. Utility images are imperfection overlays and masks, not base material textures. "
    "If the image is clearly a scratch map, dirt overlay, grunge mask, fingerprint "
    "map, edge-wear map, rust drip pattern, or similar weathering/wear asset, assign "
    "Utility and set is_tileable to false.\n"
    "7. BRICK vs TILE vs PAVER discrimination: "
    "Brick = elongated wall masonry with mortar joints, clear horizontal coursing, "
    "vertical viewing angle. "
    "Tile = interior ceramic/porcelain/terracotta with thin uniform grout lines, "
    "smooth factory face, vertical or oblique viewing angle. "
    "Paver = exterior hard-paving units viewed from ABOVE (top-down), coarser "
    "joints, ground-plane perspective -- even if the units look brick-like in shape.\n"
    "\n"
    "Return only valid JSON. No markdown, no code fences, no explanation."
)


# ---------------------------------------------------------------------------
# Per-category disambiguation notes injected into the user message.
# Keys must exactly match config.categories strings.
# ---------------------------------------------------------------------------

_CATEGORY_NOTES = {
    "Art":
        "deliberate decorative artwork applied to walls or surfaces: paintings, "
        "murals, large-format printed wall panels, canvas art, and photographic "
        "wall art. NOT a catch-all for unusual or unclassifiable textures -- use "
        "Misc for those. Set is_tileable to false.",
    "Brick":
        "clay, concrete, or calcium-silicate masonry units with visible mortar "
        "joints. Units are ELONGATED -- typically 2.5 to 3.5 times wider than "
        "tall -- laid in coursed horizontal rows on a WALL. The horizontal mortar "
        "joints run continuously across the image; vertical head joints are offset "
        "between courses (running bond, stack bond, etc.). "
        "DO NOT use for square or near-square paving units viewed from above -- "
        "use Paver. DO NOT confuse with Tile: brick mortar joints are rougher, "
        "deeper, and less uniform than tile grout.",
    "Concrete":
        "cast, precast, board-formed, or poured concrete surfaces. Includes "
        "polished concrete, exposed aggregate, stamped concrete, and asphalt "
        "paving. Not natural stone and not plaster or stucco.",
    "Fabric":
        "woven and textile materials used in upholstery, soft furnishings, "
        "curtains, and carpet: linen, cotton, tweed, velvet, bouclé, sheers, "
        "blackout fabric, broadloom carpet. For animal hide products use "
        "Leather. For area rugs and woven floor coverings use Rug.",
    "Glass":
        "architectural glazing and transparent or translucent panels. Includes "
        "clear float glass, low-iron glass, tinted glass, reflective glass, "
        "frosted glass, sandblasted glass, fluted or reeded glass, acid-etched "
        "glass, glass block, and transparent alternatives such as polycarbonate "
        "and acrylic sheet. Do NOT use for mirrors (Metal) or opaque panels.",
    "Ground":
        "natural unpaved ground and outdoor surface materials: soil, bare earth, "
        "gravel, crushed granite, grass, moss, bark, mulch, leaf litter, snow, "
        "mud, sand. Do NOT use for tile, stone paving, concrete, or asphalt -- "
        "those have their own categories.",
    "Laminate":
        "manufactured surfacing materials with no dominant natural material. "
        "Includes high-pressure laminate (HPL), melamine board, phenolic resin "
        "panels, solid surface materials such as Corian and Krion, rubber "
        "flooring, PVC wall panels, vinyl plank, epoxy floor coatings, and "
        "resin-based flooring. Use Laminate when the surface is clearly a "
        "man-made composite, not a photographic render of a natural material.",
    "Leather":
        "animal hide and synthetic hide products for upholstery and interior "
        "applications: full-grain leather, corrected-grain leather, suede, "
        "nubuck, faux leather, vinyl upholstery, and bonded leather. "
        "Do NOT use for woven textiles -- use Fabric instead.",
    "Metal":
        "ferrous and non-ferrous metals including steel, aluminium, copper, "
        "brass, bronze, iron, zinc, and Corten weathering steel. Includes metal "
        "cladding panels, corrugated metal sheeting, metal siding, and mirrored "
        "or polished metal surfaces. Use Metal even when the surface is heavily "
        "weathered, rusted, or patinated.",
    "Misc":
        "use ONLY when no specific material category fits. Do not use Misc for "
        "images that are simply unusual or hard to classify -- always try the "
        "closest specific category first. Reserve Misc for technical drawings, "
        "site plans, CAD output, blueprints, and architectural documents.",
    "Patterns":
        "abstract or geometric repeat patterns with no identifiable real-world "
        "material -- purely decorative motifs with no structural substance.",
    "Paver":
        "exterior hard-paving units installed in the horizontal ground plane: "
        "clay or concrete pavers, natural stone setts, cobblestones, granite "
        "cubes, flagstones, bluestone, brick pavers laid flat, and courtyard or "
        "plaza paving. Key distinguishers: the texture is viewed from ABOVE "
        "(top-down or near-flat camera angle), joints run in multiple directions "
        "without clear horizontal coursing, and units are often square or near-"
        "square rather than elongated. "
        "NOT Brick (which is wall-mounted and has clear horizontal coursing). "
        "NOT Tile (which is an interior finish with thin grout lines).",
    "Plaster and Stucco":
        "applied smooth or textured wall and ceiling finishes: lime render, "
        "gypsum plaster, cement stucco, sand finish, trowelled surfaces, "
        "Venetian plaster, Tadelakt, clay plaster, microcement, and EIFS. "
        "Not concrete and not bare masonry. Do NOT use for tile or mosaic "
        "patterns -- if grout lines, joint lines, or individual tile units "
        "are visible at any scale, use Tile instead.",
    "Rammed Earth":
        "compressed earth wall construction and related earth-based finishes: "
        "rammed earth, adobe, cob, compressed earth block. Characterised by "
        "visible horizontal stratification layers in warm earth tones. "
        "Not the same as Concrete or Plaster and Stucco.",
    "Rug":
        "area rugs and woven floor coverings: Persian and Oriental rugs, "
        "kilim, flatweave, hand-knotted rugs, shag, sisal, jute, seagrass, "
        "and coir mats. Distinct from broadloom carpet (Fabric) by being "
        "finished, bordered floor pieces rather than continuous roll goods.",
    "Shingle":
        "roofing shingle materials: asphalt shingles, composite shingles, "
        "cedar shake, slate shingles, and clay or terracotta roof tiles "
        "in shingle form. Characterised by overlapping unit construction "
        "intended for pitched roof applications.",
    "Sky":
        "sky and environment background images: blue sky, overcast sky, "
        "sunset, dusk, night sky, and HDRI-style panoramas used as environment "
        "maps or render backgrounds. These are not material textures and are "
        "not expected to tile. Set is_tileable to false.",
    "Stone":
        "natural and engineered stone: granite, marble, limestone, travertine, "
        "slate, onyx, sandstone, cobblestone, and quartzite. Also includes "
        "engineered stone products: terrazzo, quartz composite, and sintered "
        "stone such as Dekton. Not concrete, not tile, not rammed earth.",
    "Tile":
        "ceramic, porcelain, encaustic, mosaic, zellige, subway, and terracotta "
        "tile units for INTERIOR wall and floor applications. "
        "HARD OVERRIDE: if ANY grout lines, joint lines, or individual tile units "
        "are visible at any scale -- regardless of how fine, ornate, or heavily "
        "textured the surface appears -- the category is Tile unconditionally. "
        "Key distinguishers from Brick: tile units are generally square or "
        "rectangular with thin uniform grout joints, smoother factory-finished "
        "faces, and no visible coursing offset (or a 2:1 ratio for subway tile). "
        "Key distinguishers from Paver: tile is an INTERIOR finish with fine grout "
        "lines; pavers are EXTERIOR units with coarser, wider joints and a "
        "top-down ground-plane perspective.",
    "Utility":
        "imperfection overlays and weathering masks used as layered texture "
        "inputs in a renderer, not base material textures. Includes scratch "
        "maps, smudge and fingerprint overlays, edge-wear maps, rust drip "
        "patterns, dirt accumulation maps, water damage streaks, and general "
        "grunge maps. These are typically greyscale or low-saturation. "
        "Set is_tileable to false.",
    "WallCovering":
        "decorative surface treatments applied to walls with no dominant "
        "structural material of their own: wallpaper, vinyl wallcovering, "
        "printed films, acoustic felt panels, and paint texture effects. "
        "Do NOT use for brick, stone, tile, wood, metal, or plaster.",
    "Water":
        "the surface of standing or moving water viewed from above or at a "
        "shallow angle: swimming pool water, puddles, rivers, lakes, ocean "
        "surface, and rain-wet reflective pavement. Do NOT use for glass "
        "or transparent solid materials.",
    "Wood":
        "solid timber, engineered wood, and wood-based products: hardwood and "
        "softwood planks, parquet, herringbone, chevron, end-grain, plywood, "
        "OSB, MDF, wood veneer, charred wood (Shou Sugi Ban), reclaimed timber, "
        "bamboo, and raw bark. Includes wood siding and cladding products.",
}


# ---------------------------------------------------------------------------
# Allowed dominant_color values -- 50 architectural finish color names
# ---------------------------------------------------------------------------

_VALID_COLORS: frozenset = frozenset({
    # Whites and near-whites
    "White", "OffWhite", "Cream", "Ivory", "Alabaster",
    # Warm neutrals
    "Linen", "Beige", "Sand", "Greige", "Putty", "Khaki", "Mushroom",
    # Tans and light browns
    "Blonde", "Tan", "Honey", "Caramel", "LightBrown",
    # Browns
    "Brown", "Walnut", "DarkBrown", "Mahogany", "Espresso",
    # Earth tones
    "Ochre", "Gold", "Amber", "Rust", "Terracotta", "Sienna", "Umber",
    # Reds and pinks
    "Red", "Burgundy", "Blush", "Mauve", "Pink",
    # Yellows and oranges
    "Mustard", "Orange",
    # Greens
    "Sage", "Olive", "Forest", "Teal", "Green",
    # Blues
    "Blue", "Navy", "Slate", "Powder", "DarkBlue",
    # Greys, metals, and darks
    "LightGrey", "Grey", "Pewter", "DarkGrey", "Charcoal", "Graphite", "Black", "Silver",
    # Special
    "Multicolor",
})

# Case-insensitive lookup: normalised key (lowercase, no spaces/underscores) -> canonical
_COLOR_LOOKUP: dict = {
    c.lower().replace("_", ""): c for c in _VALID_COLORS
}


# ---------------------------------------------------------------------------
# Pydantic response model
# ---------------------------------------------------------------------------

class AITagResult(BaseModel):
    category:                  str
    material:                  str
    material_type:             str
    dominant_color:            str
    tags:                      List[str]
    is_tileable:               bool
    is_render_preview:         bool = False
    real_world_size_estimate:  Optional[str] = "unknown"

    @field_validator("tags", mode="before")
    @classmethod
    def normalise_tags(cls, v: object) -> List[str]:
        if isinstance(v, str):
            v = [v]
        return [
            str(tag).lower().replace(" ", "_").strip()
            for tag in v
            if str(tag).strip()
        ]

    @field_validator("real_world_size_estimate", mode="before")
    @classmethod
    def coerce_size(cls, v: object) -> str:
        if v is None or str(v).strip() == "":
            return "unknown"
        return str(v).strip()

    @field_validator("dominant_color", mode="before")
    @classmethod
    def validate_color(cls, v: object) -> str:
        if not isinstance(v, str) or not str(v).strip():
            return "Grey"
        stripped = str(v).strip()
        # Exact match
        if stripped in _VALID_COLORS:
            return stripped
        # Normalise: lowercase, remove spaces and underscores, then lookup
        normalised = stripped.lower().replace(" ", "").replace("_", "")
        canonical = _COLOR_LOOKUP.get(normalised)
        if canonical:
            return canonical
        logger.warning("Unknown dominant_color '%s'; defaulting to Grey.", stripped)
        return "Grey"

    @field_validator("material", "material_type", mode="before")
    @classmethod
    def coerce_text_field(cls, v: object) -> str:
        if v is None or str(v).strip() == "":
            return "Unknown"
        return str(v).strip()


# ---------------------------------------------------------------------------
# AITagger
# ---------------------------------------------------------------------------

class AITagger:
    """
    Classifies and tags PBR texture groups using a local vision model.

    Usage::

        tagger = AITagger(config, db)
        result = tagger.tag_group(group)   # returns dict or None
    """

    def __init__(self, config: Config, db: DatabaseManager) -> None:
        self.config = config
        self.db = db
        self._client = OpenAI(
            base_url=config.ai_base_url,
            api_key=config.ai_api_key,
        )
        self._valid_categories = set(config.categories)
        self._category_lookup  = {c.lower(): c for c in config.categories}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def tag_group(
        self,
        group: PBRGroup,
        unit_aspect_ratio: Optional[float] = None,
    ) -> Optional[dict]:
        if group.base_map_path is None:
            logger.warning("Skipping AI tag for '%s': no base map.", group.base_name)
            return None

        self.db.update_group_status(group.group_id, GroupStatus.AI_TAGGING)

        try:
            image_b64 = self._prepare_image(group.base_map_path)
        except Exception as exc:
            detail = f"Image preparation failed: {exc}"
            logger.error("'%s': %s", group.base_name, detail)
            self.db.update_group_status(group.group_id, GroupStatus.AI_FAILED, detail)
            return None

        source_filename = group.base_map_path.name if group.base_map_path else None
        filename_hint   = self._score_filename(group.base_name) if group.base_name else None
        if filename_hint:
            logger.debug(
                "Filename hint for '%s': category=%s keywords=%s",
                group.base_name, filename_hint[0], filename_hint[1],
            )

        result = self._retry_with_backoff(
            group, image_b64, unit_aspect_ratio, source_filename, filename_hint
        )

        if result is not None:
            self.db.set_group_ai_output(group.group_id, result)
            self.db.update_group_status(group.group_id, GroupStatus.FILE_OPS)

        return result

    # ------------------------------------------------------------------
    # Image preparation
    # ------------------------------------------------------------------

    def _prepare_image(self, base_map_path: Path) -> str:
        img     = Image.open(base_map_path).convert("RGB")
        max_dim = max(img.size)
        target  = self.config.ai_input_resolution
        if max_dim > target:
            scale    = target / max_dim
            new_size = (round(img.width * scale), round(img.height * scale))
            img      = img.resize(new_size, Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    # ------------------------------------------------------------------
    # Filename keyword scoring
    # ------------------------------------------------------------------

    def _score_filename(self, base_name: str) -> Optional[tuple]:
        """
        Tokenize base_name and match against config.category_keywords.

        Returns (matched_category, matched_keywords) for the category with
        the most keyword hits, or None if no category keywords are detected.
        Ties are broken by config dict order (first category wins).

        Tokenisation steps:
          1. Split camelCase boundaries so e.g. "BronzeCopper" → "Bronze Copper".
          2. Split on whitespace, underscore, and hyphen.
          3. Strip trailing digits from each token so e.g. "RUG1" → "rug"
             and bare numeric tokens (e.g. "0076") are discarded entirely.
        """
        # Step 1: insert a space at every lowercase→uppercase transition
        spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", base_name)
        # Step 2: split on whitespace / underscore / hyphen and lowercase
        raw_tokens = re.split(r"[\s_\-]+", spaced.lower())
        # Step 3: strip trailing digits; discard empty results
        tokens: set = set()
        for tok in raw_tokens:
            tok = tok.rstrip("0123456789")
            if tok:
                tokens.add(tok)
        best_cat: Optional[str] = None
        best_kws: list = []
        for category, keywords in self.config.category_keywords.items():
            kw_set  = frozenset(kw.lower() for kw in keywords)
            matched = sorted(tokens & kw_set)
            if len(matched) > len(best_kws):
                best_kws = matched
                best_cat = category
        return (best_cat, best_kws) if best_cat else None

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        image_b64: str,
        unit_aspect_ratio: Optional[float] = None,
        source_filename: Optional[str] = None,
        filename_hint: Optional[tuple] = None,
    ) -> list:
        """
        Build the system + user message list for the API call.

        The user message lists every allowed category with disambiguation notes,
        the full list of allowed dominant_color values, and (when available)
        a geometric context note with the computed unit aspect ratio to assist
        Brick / Tile / Paver disambiguation.
        """
        category_lines = []
        for c in sorted(self.config.categories):
            note = _CATEGORY_NOTES.get(c)
            if note:
                category_lines.append(f"  {c}: {note}")
            else:
                category_lines.append(f"  {c}")
        categories_str = "\n".join(category_lines)

        colors_str = ", ".join(sorted(_VALID_COLORS))

        # Build the geometry context note when a ratio is available.
        # Map the numeric ratio to a descriptive label to make the hint
        # immediately readable for the model.
        geometry_note = ""
        if unit_aspect_ratio is not None:
            cfg = self.config
            if (cfg.unit_geometry_brick_ratio_min
                    <= unit_aspect_ratio
                    <= cfg.unit_geometry_brick_ratio_max):
                label = "elongated (consistent with wall brick)"
            elif (cfg.unit_geometry_tile_subway_ratio_min
                    <= unit_aspect_ratio
                    <= cfg.unit_geometry_tile_subway_ratio_max):
                label = "moderately elongated (consistent with subway tile or thin brick)"
            elif (cfg.unit_geometry_tile_square_ratio_min
                    <= unit_aspect_ratio
                    <= cfg.unit_geometry_tile_square_ratio_max):
                label = "square (consistent with square tile or square paver)"
            elif (cfg.unit_geometry_paver_ratio_min
                    <= unit_aspect_ratio
                    <= cfg.unit_geometry_paver_ratio_max):
                label = "near-square (consistent with paver or brick paver)"
            else:
                label = "unclassified by standard ranges"

            geometry_note = (
                f"\n\nGEOMETRIC CONTEXT (measured from image gradient analysis):\n"
                f"  Detected unit aspect ratio (width / height): "
                f"{unit_aspect_ratio:.2f} -- {label}.\n"
                f"  Use this as supporting evidence alongside the visual content. "
                f"It is not authoritative -- weigh it against what you can see."
            )

        # Build filename hint note.
        filename_note = ""
        if source_filename:
            if filename_hint:
                matched_cat, matched_kws = filename_hint
                kw_str = ", ".join(f"'{k}'" for k in matched_kws)
                filename_note = (
                    f"\n\nFILENAME HINT — HIGH CONFIDENCE: The source file was "
                    f"named '{source_filename}'. It contains the keyword(s) "
                    f"{kw_str}, which strongly indicate this is a {matched_cat} "
                    f"texture. Classify as {matched_cat} unless the visual content "
                    f"clearly and unambiguously contradicts this."
                )
            else:
                filename_note = (
                    f"\n\nFILENAME CONTEXT: The source file was named "
                    f"'{source_filename}'. Use as light supporting evidence "
                    f"alongside the visual content."
                )

        user_text = (
            "Classify this texture. The category field must be exactly one of "
            "the following (use the name before the colon exactly as written):\n\n"
            f"{categories_str}\n\n"
            "The dominant_color field must be exactly one of the following "
            "(use the name exactly as written):\n\n"
            f"  {colors_str}\n\n"
            "Return JSON only: category, material, material_type, dominant_color, "
            f"tags, is_tileable, is_render_preview, real_world_size_estimate.{geometry_note}{filename_note}"
        )
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
                    {"type": "text", "text": user_text},
                ],
            },
        ]

    # ------------------------------------------------------------------
    # API call and response validation
    # ------------------------------------------------------------------

    def _call_api(self, messages: list) -> str:
        response = self._client.chat.completions.create(
            model=self.config.ai_model,
            messages=messages,
            response_format={"type": "json_object"},
            timeout=self.config.ai_timeout,
        )
        return response.choices[0].message.content

    def _normalize_response(self, data: dict) -> dict:
        """
        Normalise the raw parsed JSON dict before Pydantic validation.

        Handles common vision model failure modes:
        1. PascalCase or mixed-case keys.
        2. Category strings with wrong capitalisation or whitespace.
        3. Missing is_tileable or dominant_color fields.
        4. Model returning legacy 'material_name' instead of split fields.
        5. Model returning 'type' key instead of 'material_type'.
        """
        normalised: dict = {
            k.lower().replace(" ", "_").strip(): v
            for k, v in data.items()
        }

        # Rename 'type' to 'material_type' to avoid Python builtin collision
        if "type" in normalised and "material_type" not in normalised:
            normalised["material_type"] = normalised.pop("type")

        # Backward compat: if model returns legacy 'material_name' but no split
        # fields, use it as a fallback for material. material_type will default
        # via the Pydantic validator.
        if "material_name" in normalised:
            if "material" not in normalised:
                normalised["material"] = normalised["material_name"]
            del normalised["material_name"]

        # Category normalisation
        raw_cat = normalised.get("category")
        if isinstance(raw_cat, str):
            stripped  = raw_cat.strip()
            canonical = self._category_lookup.get(stripped.lower())
            normalised["category"] = canonical if canonical else stripped

        # Missing field defaults
        if "is_tileable" not in normalised:
            logger.debug("is_tileable absent; defaulting to True.")
            normalised["is_tileable"] = True

        if "is_render_preview" not in normalised:
            logger.debug("is_render_preview absent; defaulting to False.")
            normalised["is_render_preview"] = False

        if "dominant_color" not in normalised:
            logger.debug("dominant_color absent; defaulting to Grey.")
            normalised["dominant_color"] = "Grey"

        return normalised

    def _validate_response(self, raw: str) -> dict:
        data   = json.loads(raw)
        data   = self._normalize_response(data)
        result = AITagResult.model_validate(data)
        if result.category not in self._valid_categories:
            raise ValueError(
                f"Model returned unknown category '{result.category}'. "
                f"Expected one of: {sorted(self._valid_categories)}"
            )
        return result.model_dump()

    # ------------------------------------------------------------------
    # Retry loop with exponential backoff
    # ------------------------------------------------------------------

    def _retry_with_backoff(
        self,
        group: PBRGroup,
        image_b64: str,
        unit_aspect_ratio: Optional[float] = None,
        source_filename: Optional[str] = None,
        filename_hint: Optional[tuple] = None,
    ) -> Optional[dict]:
        messages   = self._build_messages(image_b64, unit_aspect_ratio, source_filename, filename_hint)
        delay      = self.config.ai_retry_base_delay
        last_error = ""

        for attempt in range(1, self.config.ai_max_retries + 1):
            try:
                raw = self._call_api(messages)
                return self._validate_response(raw)
            except (APITimeoutError, APIConnectionError) as exc:
                last_error = f"Connection/timeout: {exc}"
            except APIStatusError as exc:
                last_error = f"API status {exc.status_code}: {exc.message}"
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = f"Validation: {exc}"

            logger.warning(
                "AI attempt %d/%d failed for '%s': %s",
                attempt, self.config.ai_max_retries, group.base_name, last_error,
            )
            if attempt < self.config.ai_max_retries:
                logger.info("Retrying in %.1fs...", delay)
                time.sleep(delay)
                delay *= 2.0

        logger.error(
            "AI tagging failed for '%s' after %d attempt(s). Last error: %s",
            group.base_name, self.config.ai_max_retries, last_error,
        )
        self.db.update_group_status(
            group.group_id, GroupStatus.AI_FAILED, last_error[:500],
        )
        return None
