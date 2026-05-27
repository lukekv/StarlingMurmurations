"""
scanner.py
----------
Phase 1: Recursive directory scan, PBR group identification, SQLite registration.
Pure helpers live in scanner_helpers.py.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from config import Config
from database import DatabaseManager, GroupStatus
from scanner_helpers import (
    FileClass, assign_pat_to_groups, build_known_suffixes,
    identify_base_map, identify_map_type,
    make_group_id, scrape_dimensions, strip_map_suffix,
)

logger = logging.getLogger(__name__)


@dataclass
class PBRGroup:
    group_id:               str
    base_name:              str
    source_dir:             Path
    base_map_path:          Optional[Path]
    image_files:            List[Path]     = field(default_factory=list)
    pat_files:              List[Path]     = field(default_factory=list)
    demo_files:             List[Path]     = field(default_factory=list)
    review_files:           List[Path]     = field(default_factory=list)
    map_types:              Dict[str, str] = field(default_factory=dict)
    real_world_dimensions:  Optional[dict] = None
    base_map_warnings:      List[str]      = field(default_factory=list)
    has_mesh_files:         bool           = False


class Scanner:
    """
    Walks input_dir recursively, groups files by base name, identifies base
    maps, associates .pat files, and registers everything in SQLite.
    """

    def __init__(self, config: Config, db: DatabaseManager):
        self.config = config
        self.db = db
        self._known_suffixes = build_known_suffixes(config)
        # Precomputed lookup sets -- built once, reused for every file in every directory
        self._paver_kws        = frozenset(kw.lower() for kw in config.paver_keywords)
        self._demo_kws         = frozenset(kw.lower() for kw in config.demo_keywords)
        self._skip_names       = frozenset(n.lower() for n in config.skip_filenames)
        self._passthrough_exts = frozenset(f.lower() for f in config.passthrough_formats)
        self._review_exts      = frozenset(f.lower() for f in config.review_formats)
        self._image_exts       = frozenset(f.lower() for f in config.supported_image_formats)
        self._mesh_exts        = frozenset(e.lower() for e in config.mesh_asset_extensions)
        # Precompiled demo-strip patterns (sorted longest-first to prevent partial matches)
        self._demo_strip_patterns = [
            re.compile(r'[\s_\-]+' + re.escape(kw) + r'$', re.IGNORECASE)
            for kw in sorted(config.demo_keywords, key=len, reverse=True)
        ]

    def _classify_file(self, path: Path) -> str:
        name_lower = path.name.lower()
        if name_lower in self._skip_names:
            return FileClass.SKIP
        ext = path.suffix.lower()
        if ext in self._passthrough_exts:
            return FileClass.PASSTHROUGH
        if ext in self._review_exts:
            return FileClass.REVIEW
        if ext in self._image_exts:
            return FileClass.IMAGE
        return FileClass.SKIP

    def _is_demo_file(self, stem: str) -> bool:
        tokens = set(re.split(r"[_\-\s]+", stem.lower()))
        tokens.discard("")
        return bool(tokens & self._demo_kws)

    def _strip_demo_keyword(self, stem: str) -> str:
        lower = stem.lower()
        for pat in self._demo_strip_patterns:
            m = pat.search(lower)
            if m:
                return stem[: m.start()].strip(" _-")
        return stem

    def scan(self) -> List[PBRGroup]:
        input_dir = Path(self.config.input_dir)
        if not input_dir.is_dir():
            raise ValueError(f"input_dir does not exist: {input_dir}")

        # Build the set of pipeline-managed directories that live inside
        # input_dir and must be excluded from scanning.  This prevents the
        # scanner from treating previously written output files, review
        # copies, and recycle-bin copies as new texture sources when the
        # output tree is nested under the input directory.
        exclude_roots: set = set()
        for candidate in [
            Path(self.config.output_dir),
            Path(self.config.recycle_bin_dir),
            Path(self.config.review_dir),
        ]:
            try:
                candidate.relative_to(input_dir)
                exclude_roots.add(candidate.resolve())
            except ValueError:
                pass  # not under input_dir, no need to exclude

        exclude_names_lower = [d.lower() for d in self.config.exclude_dirs]

        def _is_excluded(p: Path) -> bool:
            resolved = p.resolve()
            # Pipeline-managed output directories
            if any(resolved == root or root in resolved.parents
                   for root in exclude_roots):
                return True
            # User-configured directory exclusions (match any path component)
            if exclude_names_lower:
                parts_lower = [part.lower() for part in resolved.parts]
                if any(excl in parts_lower for excl in exclude_names_lower):
                    return True
            return False

        all_groups: List[PBRGroup] = []
        dirs_to_scan = [input_dir] + sorted(
            p for p in input_dir.rglob("*")
            if p.is_dir() and not _is_excluded(p)
        )
        for dirpath in dirs_to_scan:
            all_groups.extend(self._process_directory(dirpath))
        logger.info("Scan complete. %d groups across %d directories.",
                    len(all_groups), len(dirs_to_scan))
        return all_groups

    def _process_directory(self, dirpath: Path) -> List[PBRGroup]:
        image_files:  List[Path] = []
        pat_files:    List[Path] = []
        review_files: List[Path] = []

        # Single pass: classify files and detect mesh assets simultaneously.
        has_mesh = False
        for entry in sorted(dirpath.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix.lower() in self._mesh_exts:
                has_mesh = True
            cls = self._classify_file(entry)
            if cls == FileClass.IMAGE:
                image_files.append(entry)
            elif cls == FileClass.PASSTHROUGH:
                pat_files.append(entry)
            elif cls == FileClass.REVIEW:
                review_files.append(entry)
        if has_mesh:
            logger.info(
                "Mesh asset directory detected: %s -- all groups will be "
                "routed to _needs_review/mesh_asset/.", dirpath
            )

        if not image_files and not pat_files:
            return []

        raw_groups: Dict[str, dict] = {}
        for img in image_files:
            demo = self._is_demo_file(img.stem)
            if demo:
                base_name = self._strip_demo_keyword(img.stem)
                suffix    = ""
            else:
                base_name, suffix = strip_map_suffix(img.stem, self._known_suffixes)
            map_type  = identify_map_type(suffix, self.config)
            group_id  = make_group_id(dirpath, base_name)
            if group_id not in raw_groups:
                raw_groups[group_id] = {
                    "group_id": group_id, "base_name": base_name,
                    "source_dir": dirpath, "images": [],
                    "demo_files": [], "map_types": {},
                }
            g = raw_groups[group_id]
            if demo:
                g["demo_files"].append(img)
            else:
                g["images"].append(img)
                g["map_types"][str(img)] = map_type

        group_list = list(raw_groups.values())
        pat_assignments = assign_pat_to_groups(pat_files, group_list)
        pbr_groups: List[PBRGroup] = []
        first = True

        for gdata in group_list:
            group_id  = gdata["group_id"]
            base_name = gdata["base_name"]
            if self.db.is_terminal_state(group_id):
                continue
            dims     = scrape_dimensions(base_name, self.config)
            base_map = identify_base_map(gdata["images"], self._known_suffixes, self.config)
            warnings: List[str] = []
            if base_map is None and gdata["images"]:
                warnings.append("base_map_not_identified")
                logger.warning("Could not identify base map for '%s' in %s",
                               base_name, dirpath)
            group_pats = [Path(p) for p, gid in pat_assignments.items() if gid == group_id]
            for p, gid in pat_assignments.items():
                if gid is None:
                    logger.warning("Unassigned PAT file: %s", p)
            rev   = review_files if first else []
            first = False
            group = PBRGroup(
                group_id=group_id, base_name=base_name, source_dir=dirpath,
                base_map_path=base_map, image_files=gdata["images"],
                pat_files=group_pats, demo_files=gdata["demo_files"],
                review_files=rev, map_types=gdata["map_types"],
                real_world_dimensions=dims, base_map_warnings=warnings,
                has_mesh_files=has_mesh,
            )
            pbr_groups.append(group)
            self._register_group(group)

        return pbr_groups

    def _register_group(self, group: PBRGroup) -> None:
        # A group with no image_files (e.g. all files matched as demo keywords)
        # has nothing to process. Treat it as no-base-map for review routing.
        no_images = len(group.image_files) == 0
        if group.has_mesh_files:
            status = GroupStatus.REVIEW_MESH_ASSET
        elif "base_map_not_identified" in group.base_map_warnings or no_images:
            status = GroupStatus.REVIEW_NO_BASE_MAP
        else:
            status = GroupStatus.PENDING

        # Classify workflow type based on image file count.
        # A group with exactly one image has no PBR companion maps -- it is
        # a legacy single-map diffuse texture.  Two or more images indicates
        # at least one non-base PBR map is present.
        if no_images:
            workflow_type = None
        elif len(group.image_files) == 1:
            workflow_type = "Diffuse"
        else:
            workflow_type = "PBR"

        self.db.insert_group(
            group_id=group.group_id, base_name=group.base_name,
            source_dir=str(group.source_dir),
            base_map_path=str(group.base_map_path) if group.base_map_path else None,
            map_count=len(group.image_files), has_pat=len(group.pat_files) > 0,
            workflow_type=workflow_type,
        )
        self.db.update_group_status(group.group_id, status)

        # Scan-time category hint: check base_name tokens against paver_keywords.
        # Token-split on _ - and whitespace, require exact lowercase match to
        # avoid false positives (e.g. "cobalt" matching "cobble").
        if status == GroupStatus.PENDING:
            base_tokens = set(re.split(r"[\s_\-]+", group.base_name.lower()))
            base_tokens.discard("")
            if base_tokens & self._paver_kws:
                self.db.set_group_category_hint(group.group_id, "Paver")
                logger.debug(
                    "category_hint=Paver stamped on '%s' (keyword match: %s)",
                    group.base_name, base_tokens & self._paver_kws,
                )
        if group.real_world_dimensions:
            self.db.set_group_dimensions(group.group_id, group.real_world_dimensions)

        for img in group.image_files:
            fid = hashlib.sha256(str(img).encode()).hexdigest()[:16]
            self.db.insert_file(
                file_id=fid, group_id=group.group_id, source_path=str(img),
                map_type=group.map_types.get(str(img), "unknown"),
                is_base_map=(group.base_map_path is not None and img == group.base_map_path),
                is_pat=False, is_demo=False,
                original_format=img.suffix.lstrip(".").lower(),
                width=None, height=None,
            )
        for demo in group.demo_files:
            fid = hashlib.sha256(str(demo).encode()).hexdigest()[:16]
            self.db.insert_file(
                file_id=fid, group_id=group.group_id, source_path=str(demo),
                map_type="demo", is_base_map=False, is_pat=False, is_demo=True,
                original_format=demo.suffix.lstrip(".").lower(), width=None, height=None,
            )
        for pat in group.pat_files:
            fid = hashlib.sha256(str(pat).encode()).hexdigest()[:16]
            self.db.insert_file(
                file_id=fid, group_id=group.group_id, source_path=str(pat),
                map_type="pat", is_base_map=False, is_pat=True, is_demo=False,
                original_format="pat", width=None, height=None,
            )
        logger.debug("Registered '%s': %d maps, %d pats, base=%s",
                     group.base_name, len(group.image_files), len(group.pat_files),
                     group.base_map_path.name if group.base_map_path else "NONE")
