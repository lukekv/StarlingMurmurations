"""
database.py
-----------
SQLite state manager for the pipeline.

Architecture:
- WAL mode enabled for concurrent reads during processing.
- All writes are serialized through a single dedicated writer thread
  fed by a thread-safe queue.Queue. Worker threads NEVER write to SQLite
  directly -- they enqueue an operation and immediately return to their task.
  This eliminates all lock contention under concurrent.futures parallelism.
- Short-lived read connections are used for queries; WAL mode allows these
  to proceed without blocking the writer thread.
"""

import json
import logging
import queue
import sqlite3
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status enumerations
# ---------------------------------------------------------------------------

class GroupStatus(str, Enum):
    PENDING                        = "pending"
    DEDUP_CHECK                    = "dedup_check"
    DUPLICATE                      = "duplicate"
    CROPPING                       = "cropping"
    TILEABILITY                    = "tileability"
    TILEABILITY_FAILED             = "tileability_failed"
    TILEABILITY_OVERRIDE_CONFIRMED = "tileability_override_confirmed"
    AI_TAGGING                     = "ai_tagging"
    AI_FAILED                      = "ai_failed"
    FILE_OPS                       = "file_ops"
    COMPLETED                      = "completed"
    BINNED_RESOLUTION              = "binned_resolution"
    BINNED_BLANK                   = "binned_blank"
    BINNED_PRODUCT_PHOTO           = "binned_product_photo"
    REVIEW_NO_BASE_MAP             = "review_no_base_map"
    REVIEW_FORMAT                  = "review_format"
    REVIEW_LOW_CONTRAST            = "review_low_contrast"
    REVIEW_LINE_ART                = "review_line_art"
    REVIEW_AI_NOT_TILEABLE         = "review_ai_not_tileable"
    REVIEW_MESH_ASSET              = "review_mesh_asset"
    REVIEW_RENDER_PREVIEW          = "review_render_preview"


class FileStatus(str, Enum):
    PENDING   = "pending"
    COMPLETED = "completed"
    SKIPPED   = "skipped"


# ---------------------------------------------------------------------------
# DatabaseManager
# ---------------------------------------------------------------------------

class DatabaseManager:
    """
    Manages all SQLite reads and writes for the pipeline.

    Usage:
        db = DatabaseManager(Path("pipeline_state.db"))
        db.insert_group(...)
        db.update_group_status(...)
        db.shutdown()   # call at end of pipeline run to flush all pending writes
    """

    _SENTINEL = object()

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._write_queue: queue.Queue = queue.Queue()
        self._init_schema()
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="db-writer",
            daemon=True,
        )
        self._writer_thread.start()
        logger.info(f"DatabaseManager ready: {db_path}")

    # ------------------------------------------------------------------
    # Schema initialisation
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        ddl_statements = [
            """
            CREATE TABLE IF NOT EXISTS groups (
                group_id                TEXT PRIMARY KEY,
                base_name               TEXT NOT NULL,
                source_dir              TEXT NOT NULL,
                base_map_path           TEXT,
                map_count               INTEGER DEFAULT 0,
                has_pat                 INTEGER DEFAULT 0,
                phash                   TEXT,
                status                  TEXT NOT NULL DEFAULT 'pending',
                status_detail           TEXT,
                is_duplicate            INTEGER DEFAULT 0,
                duplicate_of            TEXT,
                output_path             TEXT,
                ai_output               TEXT,
                real_world_dimensions   TEXT,
                processed_date          TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS files (
                file_id         TEXT PRIMARY KEY,
                group_id        TEXT NOT NULL,
                source_path     TEXT NOT NULL UNIQUE,
                map_type        TEXT,
                is_base_map     INTEGER DEFAULT 0,
                is_pat          INTEGER DEFAULT 0,
                is_demo         INTEGER DEFAULT 0,
                original_format TEXT,
                width           INTEGER,
                height          INTEGER,
                output_path     TEXT,
                status          TEXT DEFAULT 'pending',
                FOREIGN KEY (group_id) REFERENCES groups(group_id)
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_files_group ON files(group_id);",
            "CREATE INDEX IF NOT EXISTS idx_groups_status ON groups(status);",
            "CREATE INDEX IF NOT EXISTS idx_groups_phash ON groups(phash);",
        ]
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30)
        try:
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
            except sqlite3.OperationalError as e:
                logger.warning("WAL mode unavailable (%s); using default journal mode.", e)
            for stmt in ddl_statements:
                conn.execute(stmt)
            conn.commit()
            # Migration: add workflow_type column to existing databases.
            # ALTER TABLE ADD COLUMN fails silently if the column already exists.
            try:
                conn.execute("ALTER TABLE groups ADD COLUMN workflow_type TEXT;")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already present
            try:
                conn.execute("ALTER TABLE groups ADD COLUMN category_hint TEXT;")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already present
            try:
                conn.execute("ALTER TABLE groups ADD COLUMN unit_aspect_ratio REAL;")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already present
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Writer thread
    # ------------------------------------------------------------------

    def _writer_loop(self) -> None:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        except sqlite3.OperationalError as e:
            logger.warning("WAL mode unavailable in writer thread (%s).", e)
        try:
            while True:
                item = self._write_queue.get()
                if item is self._SENTINEL:
                    self._write_queue.task_done()
                    break
                sql, params = item
                try:
                    conn.execute(sql, params)
                    conn.commit()
                except sqlite3.Error as exc:
                    logger.error(
                        "DB write failed | sql=%s | params=%s | error=%s",
                        sql, params, exc,
                    )
                finally:
                    self._write_queue.task_done()
        finally:
            conn.close()
            logger.debug("DB writer thread exited cleanly.")

    def _enqueue(self, sql: str, params: tuple = ()) -> None:
        self._write_queue.put((sql, params))

    def shutdown(self) -> None:
        self._write_queue.join()
        self._write_queue.put(self._SENTINEL)
        self._writer_thread.join(timeout=10)
        logger.info("DatabaseManager shut down. All writes flushed.")

    # ------------------------------------------------------------------
    # Read helper
    # ------------------------------------------------------------------

    def _read(self, sql: str, params: tuple = ()) -> list:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Public API: groups
    # ------------------------------------------------------------------

    def insert_group(
        self,
        group_id: str,
        base_name: str,
        source_dir: str,
        base_map_path: Optional[str],
        map_count: int,
        has_pat: bool,
        workflow_type: Optional[str] = None,
    ) -> None:
        self._enqueue(
            """
            INSERT OR IGNORE INTO groups
                (group_id, base_name, source_dir, base_map_path, map_count,
                 has_pat, status, workflow_type)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (group_id, base_name, source_dir, base_map_path, map_count,
             int(has_pat), workflow_type),
        )

    def update_group_status(
        self,
        group_id: str,
        status: GroupStatus,
        detail: str = "",
    ) -> None:
        self._enqueue(
            """
            UPDATE groups
            SET status=?, status_detail=?, processed_date=?
            WHERE group_id=?
            """,
            (status.value, detail, self._now(), group_id),
        )

    def set_group_phash(self, group_id: str, phash: str) -> None:
        self._enqueue(
            "UPDATE groups SET phash=? WHERE group_id=?",
            (phash, group_id),
        )

    def mark_group_duplicate(self, group_id: str, duplicate_of: str) -> None:
        self._enqueue(
            """
            UPDATE groups
            SET is_duplicate=1, duplicate_of=?, status='duplicate', processed_date=?
            WHERE group_id=?
            """,
            (duplicate_of, self._now(), group_id),
        )

    def set_group_ai_output(self, group_id: str, ai_data: dict) -> None:
        self._enqueue(
            "UPDATE groups SET ai_output=? WHERE group_id=?",
            (json.dumps(ai_data), group_id),
        )

    def set_group_output_path(self, group_id: str, output_path: str) -> None:
        self._enqueue(
            "UPDATE groups SET output_path=? WHERE group_id=?",
            (output_path, group_id),
        )

    def set_group_dimensions(self, group_id: str, dimensions: dict) -> None:
        self._enqueue(
            "UPDATE groups SET real_world_dimensions=? WHERE group_id=?",
            (json.dumps(dimensions), group_id),
        )

    def set_group_category_hint(self, group_id: str, hint: str) -> None:
        self._enqueue(
            "UPDATE groups SET category_hint=? WHERE group_id=?",
            (hint, group_id),
        )

    def set_group_unit_aspect_ratio(self, group_id: str, ratio: float) -> None:
        self._enqueue(
            "UPDATE groups SET unit_aspect_ratio=? WHERE group_id=?",
            (ratio, group_id),
        )

    def get_group(self, group_id: str) -> Optional[sqlite3.Row]:
        rows = self._read("SELECT * FROM groups WHERE group_id=?", (group_id,))
        return rows[0] if rows else None

    def get_groups_by_status(self, status: GroupStatus) -> list:
        return self._read(
            "SELECT * FROM groups WHERE status=? ORDER BY base_name",
            (status.value,),
        )

    def get_all_phashes(self) -> list:
        return self._read(
            """
            SELECT group_id, phash FROM groups
            WHERE phash IS NOT NULL AND is_duplicate=0
            """,
        )

    def is_already_completed(self, group_id: str) -> bool:
        rows = self._read(
            "SELECT 1 FROM groups WHERE group_id=? AND status='completed'",
            (group_id,),
        )
        return len(rows) > 0

    # Terminal states: groups that must not be re-entered on a subsequent run.
    # completed                        -- fully processed, output files written
    # duplicate                        -- marked as duplicate, base map copied to recycle bin
    # file_ops                         -- AI tagging succeeded; FileOps will pick up on next run
    # tileability_failed               -- routed to _needs_review/tileability_failed
    # tileability_override_confirmed   -- override-pass AI confirmed non-rescue; already routed
    # review_no_base_map               -- routed to _needs_review/no_base_map
    # review_format                    -- routed to _needs_review/format_review
    # review_low_contrast              -- routed to _needs_review/low_contrast
    # review_line_art                  -- routed to _needs_review/line_art
    # review_ai_not_tileable           -- routed to _needs_review/ai_not_tileable
    # binned_resolution                -- routed to _recycle_bin/low_resolution
    # binned_blank                     -- routed to _recycle_bin/blank_images
    # binned_product_photo             -- routed to _recycle_bin/product_photo
    _TERMINAL_STATUSES = frozenset({
        "completed",
        "duplicate",
        "file_ops",
        "tileability_failed",
        "tileability_override_confirmed",
        "review_no_base_map",
        "review_format",
        "review_low_contrast",
        "review_line_art",
        "review_ai_not_tileable",
        "binned_resolution",
        "binned_blank",
        "binned_product_photo",
        "review_mesh_asset",
    })

    def is_terminal_state(self, group_id: str) -> bool:
        rows = self._read(
            "SELECT status FROM groups WHERE group_id=?", (group_id,)
        )
        if not rows:
            return False
        return rows[0]["status"] in self._TERMINAL_STATUSES

    def get_summary_counts(self) -> dict:
        rows = self._read(
            "SELECT status, COUNT(*) as count FROM groups GROUP BY status"
        )
        return {row["status"]: row["count"] for row in rows}

    def get_workflow_type_counts(self) -> dict:
        rows = self._read(
            """
            SELECT workflow_type, COUNT(*) as count FROM groups
            WHERE workflow_type IS NOT NULL
            GROUP BY workflow_type
            """
        )
        return {row["workflow_type"]: row["count"] for row in rows}

    # ------------------------------------------------------------------
    # Public API: files
    # ------------------------------------------------------------------

    def insert_file(
        self,
        file_id: str,
        group_id: str,
        source_path: str,
        map_type: str,
        is_base_map: bool,
        is_pat: bool,
        is_demo: bool,
        original_format: str,
        width: Optional[int],
        height: Optional[int],
    ) -> None:
        self._enqueue(
            """
            INSERT OR IGNORE INTO files
                (file_id, group_id, source_path, map_type, is_base_map, is_pat,
                 is_demo, original_format, width, height)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id, group_id, source_path, map_type,
                int(is_base_map), int(is_pat), int(is_demo),
                original_format, width, height,
            ),
        )

    def set_file_dimensions(self, file_id: str, width: int, height: int) -> None:
        self._enqueue(
            "UPDATE files SET width=?, height=? WHERE file_id=?",
            (width, height, file_id),
        )

    def set_file_output_path(self, file_id: str, output_path: str) -> None:
        self._enqueue(
            "UPDATE files SET output_path=?, status='completed' WHERE file_id=?",
            (output_path, file_id),
        )

    def update_file_status(self, file_id: str, status: FileStatus) -> None:
        self._enqueue(
            "UPDATE files SET status=? WHERE file_id=?",
            (status.value, file_id),
        )

    def get_files_for_group(self, group_id: str) -> list:
        return self._read(
            """
            SELECT * FROM files
            WHERE group_id=?
            ORDER BY is_base_map DESC, map_type ASC
            """,
            (group_id,),
        )

    def get_base_map_for_group(self, group_id: str) -> Optional[sqlite3.Row]:
        rows = self._read(
            "SELECT * FROM files WHERE group_id=? AND is_base_map=1",
            (group_id,),
        )
        return rows[0] if rows else None
