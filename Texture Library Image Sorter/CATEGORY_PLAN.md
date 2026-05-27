# Category Expansion Plan

## Summary of Changes

Current category count: 16
Proposed category count: 22
Net additions: 6 new categories (Glass, Laminate, Leather, Sky, Utility, Water)
Removals: None
Redefinitions: Art, Concrete, Misc, Plaster and Stucco, Stone, Tile

---

## Proposed Final Category List

| Category | Status | Notes |
|---|---|---|
| Art | Redefined | Paintings, murals, decorative printed panels. No longer a failure catch-all. |
| Brick | Keep | Unchanged. |
| Concrete | Expand | Explicitly includes asphalt paving and site concrete in AI prompt. |
| Fabric | Keep | Wovens, upholstery textiles, sheers, broadloom carpet. Leather is now separate. |
| Glass | New | Architectural glass: clear, frosted, fluted, tinted, reflective, polycarbonate, acrylic. |
| Ground | Keep | Natural unpaved surfaces: soil, gravel, grass, moss, bark, mulch, snow. |
| Laminate | New | Man-made surfacing: HPL, melamine, Corian, solid surface, rubber flooring, PVC panels, epoxy floors. |
| Leather | New | Natural leather, suede, synthetic leather, vinyl hide, faux leather. |
| Metal | Keep | All ferrous and non-ferrous metals, all finishes. Unchanged. |
| Misc | Redefined | True catch-all for anything that does not fit any other category. Not a quality filter. |
| Patterns | Keep | Abstract geometric repeats with no identifiable real-world material. |
| Plaster and Stucco | Expand | Adds microcement, Tadelakt, clay plaster, EIFS to AI prompt notes. |
| Rammed Earth | Keep | Rammed earth, adobe, compressed earth wall finishes. |
| Rug | Keep | Area rugs, sisal, jute, kilim, woven floor coverings. |
| Shingle | Keep | Asphalt, composite, and multi-material roofing shingles. |
| Sky | New | HDRI sky panoramas, sky background images, overcast sky maps. |
| Stone | Expand | Explicitly includes engineered stone: terrazzo, quartz, sintered stone (Dekton). |
| Tile | Expand | Explicitly includes mosaic, zellige, terracotta, encaustic, subway tile. |
| Utility | New | Imperfection overlays and masks: scratches, dirt streaks, fingerprints, rust drips, edge wear. |
| WallCovering | Keep | Wallpaper, vinyl wallcovering, acoustic felt, paint effects. |
| Water | New | Water surfaces: swimming pools, puddles, rivers, lakes, rain-wet pavement. |
| Wood | Expand | Explicitly includes charred (Shou Sugi Ban), bamboo, engineered wood, reclaimed in AI prompt. |

---

## AI Prompt Changes Required

### New disambiguation notes to write (`_CATEGORY_NOTES` in ai_tagger.py)

**Glass:** architectural glazing and transparent panels. Includes clear float glass, low-iron glass, tinted glass, reflective glass, frosted glass, sandblasted glass, fluted/reeded glass, acid-etched glass, glass block, and transparent alternatives such as polycarbonate and acrylic sheet. Do not use for mirrors (Metal), tile (Tile), or opaque panels.

**Laminate:** manufactured surfacing with no dominant natural material. Includes high-pressure laminate (HPL), melamine board, phenolic resin panels, solid surface materials (Corian, Krion), rubber flooring, PVC wall panels, vinyl plank, epoxy floor coatings, and resin-based flooring. Use Laminate when the surface is clearly a man-made composite, not a photographic render of a natural material.

**Leather:** animal hide and synthetic hide products used in upholstery and interior applications. Includes full-grain and corrected-grain leather, suede, nubuck, faux leather, vinyl upholstery, and bonded leather. Do not use for woven textiles — use Fabric instead.

**Sky:** environment background images and sky maps. Includes blue sky, overcast sky, sunset, night sky, and HDRI-style panoramas. These are not material textures and are not expected to tile.

**Utility:** imperfection overlays and weathering masks used as layered texture inputs, not base material textures. Includes scratch maps, smudge and fingerprint overlays, edge wear maps, rust drip patterns, dirt accumulation maps, water damage streaks, and general-purpose grunge maps. These are grayscale or low-saturation masks intended to be layered over a base material in a renderer.

**Water:** the surface of standing or moving water as seen from above or at a shallow angle. Includes swimming pool water, puddles, rivers, lakes, ocean surface, and wet pavement reflections. Do not use for glass or transparent materials.

### Modified classification rules to update

**Art:** currently defined as non-photographic illustration. Must be redefined to mean deliberate decorative artwork: paintings, murals, large-format printed panels, canvas art, photographic wall art. Art is no longer a catch-all for images that fail other checks — that role belongs to Misc.

**Concrete:** extend disambiguation note to explicitly include asphalt paving, cracked site concrete, and exposed aggregate, so the AI does not route site-paving textures to Ground.

**Misc:** remove any implication that Misc is for low-quality or unclassifiable pipeline failures. It is simply the bucket for anything that does not fit a specific material category.

**Tile hard override:** keep unchanged. Any visible grout lines or tile units at any scale means Tile, unconditionally.

---

## Pipeline Behavior Changes Required

### Utility and Sky categories bypass tileability

Utility and Sky textures will fail the two-signal tileability test by design. A scratch map has no seamless edge relationship. A sky panorama is not a repeating tile. Under the current pipeline, both would be routed to `_needs_review/tileability_failed` and never reach the library.

Two options:

**Option A (recommended):** Add the AI-tagged category as a post-tileability override. If the AI classifies an image as Utility or Sky, override the tileability failure and route it to the library regardless. This requires the AI to run before the routing decision, which means restructuring Stage 3 and Stage 4 slightly.

**Option B (simpler, less accurate):** Add common Utility and Sky filename keywords to `tileability_bypass_keywords` in config so the tileability test is skipped for files whose names contain terms like `scratch`, `grunge`, `overlay`, `imperfection`, `mask`, `dirt`, `sky`, `hdri`. This is imprecise but requires no architectural change.

**Recommendation:** Discuss Option A vs Option B before implementing. Option A is correct. Option B is a patch that depends on filenames being descriptive.

### Asphalt under Concrete

Asphalt paving is currently unrepresented. It will go under Concrete with a strong disambiguation note in the AI prompt. No separate Asphalt category is needed.

---

## Changes by File

### `config.py`

Replace `categories` list with the 22 categories above. No other changes.

### `ai_tagger.py`

1. Add 5 new entries to `_CATEGORY_NOTES` (Glass, Laminate, Leather, Sky, Utility, Water).
2. Update existing notes for Art, Concrete, Misc.
3. Update the system prompt's classification rule for Art (no longer a quality catch-all).
4. Keep the Tile hard override rule and all other existing rules unchanged.

### `image_processor.py` or `main.py`

Resolve the Utility and Sky tileability bypass question (Option A or B above) before implementing.

---

## Resolved: Utility and Sky Tileability — Both Option A and Option B

Both approaches will be implemented. Option B runs first as an early filter. Option A catches anything that slips through.

---

### Option B — Keyword bypass in config (implemented first)

Add the following keywords to `tileability_bypass_keywords` in `config.py`. Files whose names contain any of these skip the tileability test entirely and are treated as confirmed tileable for routing purposes:

```
"grunge", "scratch", "overlay", "mask", "imperfection",
"dirt", "smudge", "fingerprint", "wear", "rust",
"sky", "hdri", "hdr", "panorama", "pano"
```

This is a config-only change. No logic changes anywhere.

---

### Option A — AI category override pass (implemented second)

**The problem:** The pipeline runs Stage 3 (tileability) before Stage 4 (AI tagging). Groups that fail tileability never reach the AI and go directly to `_needs_review/tileability_failed/`. A Utility overlay or Sky panorama with a non-descriptive filename will be stuck in review forever.

**The fix:** Insert a new pass between Stage 3 and the tileability failure routing call in `main.py`. This pass runs the AI on tileability-failed groups, checks the returned category, and either overrides the failure or confirms the routing.

**New config field:**

```python
tileability_override_categories: List[str] = ["Art", "Sky", "Utility", "Water"]
```

This list controls which AI-returned categories trigger an override. It is configurable so you can add or remove categories without touching code.

**New function in `main.py`: `_tileability_ai_override()`**

Execution order in the pipeline:

```
Stage 3: image_processor runs, sets TILEABILITY_FAILED on failing groups
   |
   v
_tileability_ai_override()   <-- NEW STEP
   |-- gets all TILEABILITY_FAILED groups
   |-- runs AI tagger on each one
   |-- if AI category is in tileability_override_categories:
   |     run file_ops.process_one() immediately
   |     mark group COMPLETED
   |-- if AI category is anything else:
   |     leave status as TILEABILITY_FAILED (normal routing picks it up)
   v
_route_tileability_failures()  <-- only sees groups still at TILEABILITY_FAILED
   v
Stage 4: normal AI tagging for groups at AI_TAGGING status
```

**Why Art and Water are also in the override list:**

Art (paintings, murals) is intentionally not a seamlessly tileable material. Without the override, every painting texture would end up in the review folder. Water surfaces are sometimes tileable and sometimes not — including Water in the override list ensures water textures that fail tileability still get tagged and saved rather than sent to review.

**What changes by file:**

- `config.py` — add `tileability_override_categories` list and new Option B keywords
- `main.py` — add `_tileability_ai_override()` function, call it between Stage 3 and routing

No changes to `image_processor.py`, `ai_tagger.py`, or `file_ops.py` for this feature.

---

## Implementation Order (complete, ready to code)

1. `config.py` — update categories list, add tileability_override_categories, add Option B keywords
2. `ai_tagger.py` — add new _CATEGORY_NOTES entries, update system prompt
3. `main.py` — add _tileability_ai_override() function and wire it into the stage sequence
4. Verify on a small test batch
