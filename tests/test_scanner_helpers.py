"""
Characterization tests for the pure helper functions in scanner_helpers.py.
These lock in the suffix-stripping, base-map identification, dimension
scraping, and grouping behavior the pipeline currently relies on.
"""

from pathlib import Path

import pytest

from config import Config
from scanner_helpers import (
    FileClass,
    assign_pat_to_groups,
    build_known_suffixes,
    classify_file,
    identify_base_map,
    identify_map_type,
    is_demo_file,
    make_group_id,
    scrape_dimensions,
    strip_demo_keyword,
    strip_map_suffix,
)


@pytest.fixture(scope="module")
def config() -> Config:
    return Config()


@pytest.fixture(scope="module")
def known_suffixes(config) -> list:
    return build_known_suffixes(config)


# ---------------------------------------------------------------------------
# strip_map_suffix
# ---------------------------------------------------------------------------

class TestStripMapSuffix:

    @pytest.mark.parametrize("stem, base, suffix", [
        ("Brick_albedo",                  "Brick",           "_albedo"),
        ("Marble062_COL_4K",              "Marble062",       "_col"),
        ("ConcreteWall001_NRM_3K",        "ConcreteWall001", "_nrm"),
        ("ConcreteWall001_COL_VAR1_3K",   "ConcreteWall001", "_col"),
        ("Wood_ALBEDO",                   "Wood",            "_albedo"),
        ("Oak Floor Diffuse",             "Oak Floor",       " diffuse"),
    ])
    def test_strips_known_suffixes(self, known_suffixes, stem, base, suffix):
        assert strip_map_suffix(stem, known_suffixes) == (base, suffix)

    def test_lod_token_stripped_before_suffix_match(self, known_suffixes):
        base, suffix = strip_map_suffix(
            "Aset_wood_log_M_phyr5_4K_Normal_LOD0", known_suffixes
        )
        assert base == "Aset_wood_log_M_phyr5_4K"
        assert suffix == "_normal"

    def test_no_suffix_returns_stem_unchanged(self, known_suffixes):
        assert strip_map_suffix("RedBrick", known_suffixes) == ("RedBrick", "")

    def test_longest_suffix_wins(self, known_suffixes):
        # "_displacement" must match before its prefix "_disp" would.
        base, suffix = strip_map_suffix("Floor_displacement", known_suffixes)
        assert base == "Floor"
        assert suffix == "_displacement"


# ---------------------------------------------------------------------------
# identify_map_type
# ---------------------------------------------------------------------------

class TestIdentifyMapType:

    @pytest.mark.parametrize("suffix, map_type", [
        ("_albedo",   "albedo"),
        ("_basecolor","albedo"),
        ("_nrm",      "normal"),
        ("_rough",    "roughness"),
        ("_metallic", "metallic"),
        ("_height",   "displacement"),
        ("_ao",       "ao"),
        (" texture",  "albedo"),
    ])
    def test_known_suffixes(self, config, suffix, map_type):
        assert identify_map_type(suffix, config) == map_type

    def test_unknown_suffix(self, config):
        assert identify_map_type("_zzz", config) == "unknown"


# ---------------------------------------------------------------------------
# classify_file
# ---------------------------------------------------------------------------

class TestClassifyFile:

    @pytest.mark.parametrize("name, expected", [
        ("brick.jpg",   FileClass.IMAGE),
        ("brick.PNG",   FileClass.IMAGE),
        ("brick.tiff",  FileClass.IMAGE),
        ("hatch.pat",   FileClass.PASSTHROUGH),
        ("layered.psd", FileClass.REVIEW),
        ("anim.gif",    FileClass.REVIEW),
        ("notes.txt",   FileClass.SKIP),
        ("Thumbs.db",   FileClass.SKIP),
        ("desktop.ini", FileClass.SKIP),
    ])
    def test_classification(self, config, name, expected):
        assert classify_file(Path(name), config) == expected


# ---------------------------------------------------------------------------
# is_demo_file / strip_demo_keyword
# ---------------------------------------------------------------------------

class TestDemoDetection:

    @pytest.mark.parametrize("stem", [
        "141_light parquet DEMO",
        "brick_red_preview",
        "oak_sphere_4k",
        "wall-thumbnail",
    ])
    def test_demo_files_detected(self, config, stem):
        assert is_demo_file(stem, config) is True

    def test_token_match_is_exact_not_substring(self, config):
        # "renders" must NOT match the keyword "render" -- token-exact matching.
        assert is_demo_file("library_renders", config) is False

    def test_plain_texture_not_demo(self, config):
        assert is_demo_file("RedBrick_albedo", config) is False

    def test_strip_demo_keyword_at_end(self, config):
        assert strip_demo_keyword("141_light parquet DEMO", config) == "141_light parquet"

    def test_strip_demo_keyword_no_match_unchanged(self, config):
        assert strip_demo_keyword("RedBrick", config) == "RedBrick"


# ---------------------------------------------------------------------------
# identify_base_map (3-tier identification)
# ---------------------------------------------------------------------------

class TestIdentifyBaseMap:

    def test_tier1_explicit_albedo_suffix(self, config, known_suffixes):
        files = [Path("Brick_normal.png"), Path("Brick_albedo.png")]
        assert identify_base_map(files, known_suffixes, config) == Path("Brick_albedo.png")

    def test_tier1_terminal_word(self, config, known_suffixes):
        files = [Path("Red Brick Normal.jpg"), Path("Red Brick Texture.jpg")]
        assert identify_base_map(files, known_suffixes, config) == Path("Red Brick Texture.jpg")

    def test_tier2a_single_unsuffixed_candidate(self, config, known_suffixes):
        files = [Path("RedBrick.png"), Path("RedBrick_normal.png")]
        assert identify_base_map(files, known_suffixes, config) == Path("RedBrick.png")

    def test_tier2b_shortest_stem_wins(self, config, known_suffixes):
        files = [Path("BrickWeathered.png"), Path("Brick.png")]
        assert identify_base_map(files, known_suffixes, config) == Path("Brick.png")

    def test_tier3_no_candidates_returns_none(self, config, known_suffixes):
        files = [Path("Wall_normal.png"), Path("Wall_rough.png")]
        assert identify_base_map(files, known_suffixes, config) is None

    def test_empty_list_returns_none(self, config, known_suffixes):
        assert identify_base_map([], known_suffixes, config) is None


# ---------------------------------------------------------------------------
# scrape_dimensions
# ---------------------------------------------------------------------------

class TestScrapeDimensions:

    def test_decimal_with_unit(self, config):
        result = scrape_dimensions("39.8 x 47.9 inches", config)
        assert result == {
            "width": 39.8, "height": 47.9,
            "unit": "inches", "raw": "39.8 x 47.9 inches",
        }

    def test_compact_metric(self, config):
        result = scrape_dimensions("600x300mm", config)
        assert result["width"] == 600.0
        assert result["height"] == 300.0
        assert result["unit"] == "mm"

    def test_missing_unit_assumes_inches_and_flags_ambiguous(self, config):
        result = scrape_dimensions("24 x 48", config)
        assert result["unit"] == "inches"
        assert result["unit_ambiguous"] is True

    def test_unit_normalisation(self, config):
        assert scrape_dimensions("10 x 20 ft", config)["unit"] == "feet"
        assert scrape_dimensions("10 x 20 centimetres", config)["unit"] == "cm"

    def test_no_dimensions_returns_none(self, config):
        assert scrape_dimensions("RedBrick_albedo", config) is None


# ---------------------------------------------------------------------------
# assign_pat_to_groups
# ---------------------------------------------------------------------------

class TestAssignPatToGroups:

    def test_single_group_gets_all_pats(self):
        groups = [{"group_id": "g1", "base_name": "red_brick"}]
        pats = [Path("a.pat"), Path("b.pat")]
        result = assign_pat_to_groups(pats, groups)
        assert result == {"a.pat": "g1", "b.pat": "g1"}

    def test_keyword_match_assigns_to_best_group(self):
        groups = [
            {"group_id": "g1", "base_name": "red_brick"},
            {"group_id": "g2", "base_name": "oak_wood"},
        ]
        result = assign_pat_to_groups([Path("red_brick_pattern.pat")], groups)
        assert result["red_brick_pattern.pat"] == "g1"

    def test_tie_leaves_pat_unassigned(self):
        groups = [
            {"group_id": "g1", "base_name": "brick_red"},
            {"group_id": "g2", "base_name": "brick_blue"},
        ]
        result = assign_pat_to_groups([Path("brick.pat")], groups)
        assert result["brick.pat"] is None

    def test_no_match_leaves_pat_unassigned(self):
        groups = [
            {"group_id": "g1", "base_name": "red_brick"},
            {"group_id": "g2", "base_name": "oak_wood"},
        ]
        result = assign_pat_to_groups([Path("marble.pat")], groups)
        assert result["marble.pat"] is None


# ---------------------------------------------------------------------------
# make_group_id
# ---------------------------------------------------------------------------

class TestMakeGroupId:

    def test_deterministic(self):
        d = Path("C:/textures/brick")
        assert make_group_id(d, "RedBrick") == make_group_id(d, "RedBrick")

    def test_sixteen_hex_chars(self):
        gid = make_group_id(Path("C:/textures"), "RedBrick")
        assert len(gid) == 16
        int(gid, 16)  # raises ValueError if not hex

    def test_case_and_whitespace_insensitive_base_name(self):
        d = Path("C:/textures")
        assert make_group_id(d, "RedBrick") == make_group_id(d, "  redbrick ")

    def test_different_inputs_differ(self):
        d = Path("C:/textures")
        assert make_group_id(d, "RedBrick") != make_group_id(d, "OakWood")
        assert make_group_id(Path("C:/a"), "x") != make_group_id(Path("C:/b"), "x")
