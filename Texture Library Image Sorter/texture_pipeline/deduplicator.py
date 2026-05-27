"""
deduplicator.py
---------------
pHash deduplication pass.  Runs after Phase 1 (scan) and before Phase 2
(image processing / cropping).

Architecture
------------
Pass 1 -- concurrent pHash computation
    ThreadPoolExecutor (cpu_workers) opens each group's base map with
    Pillow, computes imagehash.phash(), and stores the hex string in the
    database via db.set_group_phash().  Groups with no base map are
    skipped silently.

Pass 2 -- serial BK-tree Hamming search
    A BK-tree (implemented inline; no new dependencies) is built over
    all computed hashes.  Every hash is queried against the tree with
    phash_hamming_threshold as the search radius.  Duplicate pairs are
    resolved: the group with the larger base-map pixel area is kept;
    ties break alphabetically on base_name.  Losers are written to the
    database via db.mark_group_duplicate() and logged to the duplicate
    report file.

The module never deletes or moves any files.  It only writes database
state and the plain-text report.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import imagehash
from PIL import Image, UnidentifiedImageError

from config import Config
from database import DatabaseManager
from scanner import PBRGroup

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BK-tree (inline implementation -- no extra dependency)
# ---------------------------------------------------------------------------

def _hamming(a: int, b: int) -> int:
    """Popcount of XOR -- number of differing bits."""
    return (a ^ b).bit_count()


class _BKNode:
    __slots__ = ("group_id", "phash_int", "children")

    def __init__(self, group_id: str, phash_int: int) -> None:
        self.group_id  = group_id
        self.phash_int = phash_int
        self.children: Dict[int, "_BKNode"] = {}


class _BKTree:
    """
    Metric tree for Hamming distance queries.

    add()    -- O(log n) average
    search() -- O(log n) average for small thresholds
    """

    def __init__(self) -> None:
        self.root: Optional[_BKNode] = None

    def add(self, group_id: str, phash_int: int) -> None:
        if self.root is None:
            self.root = _BKNode(group_id, phash_int)
            return
        node = self.root
        while True:
            d = _hamming(node.phash_int, phash_int)
            if d == 0:
                # Exact hash collision -- already indexed under a different
                # group_id.  The collision will surface naturally when the
                # inserting group_id is later queried.
                return
            if d not in node.children:
                node.children[d] = _BKNode(group_id, phash_int)
                return
            node = node.children[d]

    def search(self, phash_int: int, threshold: int) -> List[Tuple[str, int]]:
        """
        Return list of (group_id, hamming_distance) for all entries
        within *threshold* of *phash_int*.
        """
        if self.root is None:
            return []
        results: List[Tuple[str, int]] = []
        stack = [self.root]
        while stack:
            node = stack.pop()
            d = _hamming(node.phash_int, phash_int)
            if d <= threshold:
                results.append((node.group_id, d))
            lo = max(0, d - threshold)
            hi = d + threshold
            for dist, child in node.children.items():
                if lo <= dist <= hi:
                    stack.append(child)
        return results


# ---------------------------------------------------------------------------
# Deduplicator
# ---------------------------------------------------------------------------

class Deduplicator:
    """
    Identifies perceptually duplicate PBR groups and marks losers in the
    database.

    Usage::

        dedup = Deduplicator(config, db)
        n_dupes = dedup.deduplicate(groups)
    """

    def __init__(self, config: Config, db: DatabaseManager) -> None:
        self.config = config
        self.db     = db

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def deduplicate(self, groups: List[PBRGroup]) -> int:
        """
        Run the full deduplication pass.

        Returns the number of groups marked as duplicates.
        """
        phash_map = self._compute_all_phashes(groups)
        if not phash_map:
            logger.info("Deduplication: no hashes computed (no groups with base maps).")
            return 0

        pairs = self._find_duplicate_pairs(phash_map)
        if not pairs:
            logger.info("Deduplication: no duplicates found.")
            self._write_report([])
            return 0

        logger.info("Deduplication: %d duplicate pair(s) found.", len(pairs))
        self._mark_duplicates(pairs, phash_map)
        self._write_report(pairs)
        return len(pairs)

    # ------------------------------------------------------------------
    # Pass 1: concurrent pHash computation
    # ------------------------------------------------------------------

    def _compute_all_phashes(
        self, groups: List[PBRGroup]
    ) -> Dict[str, Tuple[str, str, int]]:
        """
        Compute pHash for every group that has a base map.

        Returns a dict: group_id -> (base_name, phash_hex, pixel_area).
        pixel_area is cached here so Pass 2 resolution never re-opens images.
        Also writes each hash to the database immediately.
        """
        eligible = [g for g in groups if g.base_map_path is not None]
        logger.info(
            "Deduplication Pass 1: computing pHash for %d group(s) "
            "(%d skipped -- no base map).",
            len(eligible), len(groups) - len(eligible),
        )

        results: Dict[str, Tuple[str, str, int]] = {}

        with ThreadPoolExecutor(
            max_workers=self.config.cpu_workers, thread_name_prefix="phash"
        ) as pool:
            future_to_group = {
                pool.submit(self._compute_one_phash, g): g
                for g in eligible
            }
            for future in as_completed(future_to_group):
                group = future_to_group[future]
                try:
                    result = future.result()
                except Exception as exc:
                    logger.warning(
                        "pHash failed for '%s': %s", group.base_name, exc
                    )
                    continue
                if result is not None:
                    phash_hex, pixel_area = result
                    results[group.group_id] = (group.base_name, phash_hex, pixel_area)
                    self.db.set_group_phash(group.group_id, phash_hex)

        logger.info(
            "Deduplication Pass 1 complete: %d hash(es) stored.", len(results)
        )
        return results

    def _compute_one_phash(self, group: PBRGroup) -> Optional[Tuple[str, int]]:
        """Worker: open base map, compute pHash. Returns (phash_hex, pixel_area)."""
        try:
            with Image.open(group.base_map_path) as img:
                w, h = img.size
                pixel_area = w * h
                if pixel_area > self.config.max_pixels_for_phash:
                    logger.warning(
                        "Skipping pHash for '%s': image too large (%dx%d = %dMP, limit %dMP).",
                        group.base_name, w, h,
                        pixel_area // 1_000_000,
                        self.config.max_pixels_for_phash // 1_000_000,
                    )
                    return None
                return str(imagehash.phash(img.convert("RGB"))), pixel_area
        except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
            logger.warning(
                "Cannot open base map for '%s' (%s): %s",
                group.base_name, group.base_map_path, exc,
            )
            return None

    # ------------------------------------------------------------------
    # Pass 2: BK-tree duplicate pair detection
    # ------------------------------------------------------------------

    def _find_duplicate_pairs(
        self,
        phash_map: Dict[str, Tuple[str, str, int]],
    ) -> List[Tuple[str, str, int]]:
        """
        Build BK-tree, query each hash, collect unique duplicate pairs.

        Returns list of (group_id_a, group_id_b, hamming_distance).
        Pairs are de-duplicated so each (a, b) appears once with a < b
        lexicographically.

        Cross-run / merge support: the BK-tree is pre-seeded with all
        pHashes already stored in the database (from previous pipeline runs
        or a prior processing of the same library).  This means a new file
        that is visually identical to an entry already in the library will
        be detected and marked as the duplicate loser, regardless of whether
        the two files were processed in the same run.
        """
        threshold    = self.config.phash_hamming_threshold
        new_group_ids = set(phash_map.keys())

        tree = _BKTree()

        # ── Seed tree with existing DB pHashes (previous / other runs) ──────
        # Skip any group_id that is part of the current batch -- those are
        # added below with full metadata.
        existing_in_db: Set[str] = set()
        for row in self.db.get_all_phashes():
            gid       = row["group_id"]
            phash_hex = row["phash"]
            if gid in new_group_ids:
                continue  # current-batch group; added below
            try:
                tree.add(gid, int(phash_hex, 16))
                existing_in_db.add(gid)
            except (ValueError, TypeError):
                continue  # malformed hash in DB -- skip silently

        if existing_in_db:
            logger.debug(
                "Deduplication: seeded BK-tree with %d existing library hash(es).",
                len(existing_in_db),
            )

        # ── Add current-batch hashes ─────────────────────────────────────────
        for gid in new_group_ids:
            _, phash_hex, _ = phash_map[gid]
            tree.add(gid, int(phash_hex, 16))

        seen:  Set[Tuple[str, str]]      = set()
        pairs: List[Tuple[str, str, int]] = []

        # Only query new groups -- existing library groups are already decided.
        for gid in new_group_ids:
            _, phash_hex, _ = phash_map[gid]
            matches = tree.search(int(phash_hex, 16), threshold)
            for match_gid, dist in matches:
                if match_gid == gid:
                    continue
                key = (min(gid, match_gid), max(gid, match_gid))
                if key in seen:
                    continue
                seen.add(key)
                pairs.append((key[0], key[1], dist))

        return pairs

    # ------------------------------------------------------------------
    # Resolve keeper vs loser, write database
    # ------------------------------------------------------------------

    def _mark_duplicates(
        self,
        pairs: List[Tuple[str, str, int]],
        phash_map: Dict[str, Tuple[str, str, int]],
    ) -> None:
        """
        For each duplicate pair, determine which group to keep (higher
        base-map resolution; tie-break: alphabetical base_name) and mark
        the other as a duplicate in the database.

        For cross-run pairs where one group is from a previous run, the
        existing library entry is always the keeper and the incoming file
        is always the loser.  base_name for DB-only groups is read from
        the database row rather than phash_map.
        """
        for gid_a, gid_b, dist in pairs:
            keeper, loser = self._resolve_keeper(gid_a, gid_b, phash_map)
            # phash_map only contains current-batch groups; existing DB
            # groups are resolved by reading their database row.
            if keeper in phash_map:
                base_name_keeper = phash_map[keeper][0]
            else:
                row = self.db.get_group(keeper)
                base_name_keeper = row["base_name"] if row else keeper
            if loser in phash_map:
                base_name_loser = phash_map[loser][0]
            else:
                row = self.db.get_group(loser)
                base_name_loser = row["base_name"] if row else loser
            self.db.mark_group_duplicate(loser, keeper)
            logger.info(
                "Duplicate (hamming=%d): KEEP '%s' | DISCARD '%s'",
                dist, base_name_keeper, base_name_loser,
            )

    def _resolve_keeper(
        self,
        gid_a: str,
        gid_b: str,
        phash_map: Dict[str, Tuple[str, str, int]],
    ) -> Tuple[str, str]:
        """
        Return (keeper_group_id, loser_group_id).

        Decision order:
          1. Cross-run priority: if one group is from a previous run (not in
             the current phash_map), it is always the keeper.  The incoming
             file is the duplicate, not the already-processed library entry.
          2. Larger base-map pixel area (cached from pHash pass -- no re-open).
          3. Alphabetical base_name (first alphabetically is kept).
        """
        in_batch_a = gid_a in phash_map
        in_batch_b = gid_b in phash_map

        # Cross-run: existing library entry wins unconditionally
        if in_batch_a and not in_batch_b:
            return (gid_b, gid_a)   # gid_b is existing keeper, gid_a is new loser
        if in_batch_b and not in_batch_a:
            return (gid_a, gid_b)   # gid_a is existing keeper, gid_b is new loser

        # Same-batch: resolve by pixel area then name
        _, _, area_a = phash_map.get(gid_a, ("", "", 0))
        _, _, area_b = phash_map.get(gid_b, ("", "", 0))

        if area_a != area_b:
            return (gid_a, gid_b) if area_a >= area_b else (gid_b, gid_a)

        name_a = phash_map[gid_a][0].lower() if gid_a in phash_map else gid_a
        name_b = phash_map[gid_b][0].lower() if gid_b in phash_map else gid_b
        return (gid_a, gid_b) if name_a <= name_b else (gid_b, gid_a)

    # ------------------------------------------------------------------
    # Duplicate report
    # ------------------------------------------------------------------

    def _write_report(self, pairs: List[Tuple[str, str, int]]) -> None:
        """
        Write a plain-text summary to duplicate_report_path.
        Creates parent directories if necessary.
        """
        report_path = Path(self.config.duplicate_report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            "Texture Pipeline -- Duplicate Report",
            "=" * 60,
            f"Total duplicate pairs found: {len(pairs)}",
            "",
        ]

        for gid_a, gid_b, dist in pairs:
            row_a = self.db.get_group(gid_a)
            row_b = self.db.get_group(gid_b)
            if row_a is None or row_b is None:
                continue
            if row_a["is_duplicate"]:
                keeper_row, loser_row = row_b, row_a
            else:
                keeper_row, loser_row = row_a, row_b

            lines += [
                f"Hamming distance : {dist}",
                f"KEPT   : {keeper_row['base_name']}",
                f"         {keeper_row['base_map_path'] or '(no base map path)'}",
                f"BINNED : {loser_row['base_name']}",
                f"         {loser_row['base_map_path'] or '(no base map path)'}",
                "",
            ]

        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Duplicate report written to: %s", report_path)
