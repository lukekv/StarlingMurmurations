#!/usr/bin/env python3
"""
serve_preview.py
----------------
Local HTTP server for reviewing and sorting _needs_review texture items.

HOW TO USE
----------
1. Run the pipeline:       run_pipeline.bat
2. Generate the preview:   python generate_preview.py --output "D:\\...\\output"
3. Start this server:      python serve_preview.py --output "D:\\...\\output"
4. Open:                   http://localhost:8765

The browser UI (Accept / Delete buttons) appears only when served through
this script.  Accept re-runs AI tagging and writes the texture to the chosen
category.  Delete moves the file or folder to _recycle_bin/manually_deleted/.

The server rescans the output directory on every page load so the view stays
current without restarting.

This server is local-only.  Do NOT expose it on the network.

Dependencies (already in requirements.txt):
    Pillow, openai, pydantic, opencv-python, numpy

Usage
-----
    python serve_preview.py --output "D:\\path\\to\\_output" [--port 8765]
"""

import argparse
import hashlib
import http.server
import json
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Path setup -- pipeline modules live inside the sibling sub-package
# ---------------------------------------------------------------------------

SCRIPT_DIR   = Path(__file__).parent.resolve()
PIPELINE_DIR = SCRIPT_DIR / "Texture Library Image Sorter" / "texture_pipeline"

sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(SCRIPT_DIR))

# Pipeline imports (after sys.path is configured)
try:
    from config import Config
    from database import DatabaseManager, GroupStatus
    from scanner import PBRGroup
    from ai_tagger import AITagger
    from file_ops import FileOps
    from image_processor import ProcessResult
except ImportError as _e:
    sys.exit(
        f"ERROR: Cannot import pipeline modules from {PIPELINE_DIR}\n"
        f"  {_e}\n"
        f"Ensure the pipeline directory exists and all dependencies are installed."
    )

try:
    from generate_preview import (
        scan_output,
        scan_needs_review,
        build_html,
        HTML_FILENAME,
        THUMB_DIR,
    )
except ImportError as _e:
    sys.exit(
        f"ERROR: Cannot import generate_preview.py from {SCRIPT_DIR}\n"
        f"  {_e}"
    )

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state (set in main() before the server starts)
# ---------------------------------------------------------------------------

OUTPUT_DIR: Path = Path(".")
_pipeline_config: Optional[Config] = None


def get_config() -> Config:
    """Return a Config instance derived from OUTPUT_DIR."""
    global _pipeline_config
    if _pipeline_config is None:
        _pipeline_config = Config(
            input_dir             = OUTPUT_DIR.parent,  # best guess; not used by FileOps
            output_dir            = OUTPUT_DIR,
            recycle_bin_dir       = OUTPUT_DIR / "_recycle_bin",
            review_dir            = OUTPUT_DIR / "_needs_review",
            db_path               = OUTPUT_DIR / "pipeline_state.db",
            duplicate_report_path = OUTPUT_DIR / "duplicate_report.txt",
        )
    return _pipeline_config


# ---------------------------------------------------------------------------
# HTML builder (rescans on every request so the view stays current)
# ---------------------------------------------------------------------------

def rebuild_html() -> str:
    """Scan output directory and return fresh HTML with SERVER_MODE injected."""
    thumb_dir = OUTPUT_DIR / THUMB_DIR
    thumb_dir.mkdir(exist_ok=True)

    categories  = scan_output(OUTPUT_DIR, thumb_dir)
    review_cats = scan_needs_review(OUTPUT_DIR, thumb_dir)
    all_cats    = {**categories, **review_cats}

    html = build_html(all_cats)

    config    = get_config()
    cats_json = json.dumps(config.categories)

    # Inject server-mode flags into the two placeholders added to HTML_TEMPLATE
    html = html.replace("const SERVER_MODE = false;", "const SERVER_MODE = true;", 1)
    html = html.replace("const CATEGORIES = [];",     f"const CATEGORIES = {cats_json};", 1)

    return html


# ---------------------------------------------------------------------------
# Variant numbering (mirrors FileOps._next_variant)
# ---------------------------------------------------------------------------

def _next_variant(category_dir: Path, base_slug: str) -> int:
    if not category_dir.exists():
        return 1
    pattern = re.compile(r"^" + re.escape(base_slug) + r"_(\d+)$")
    nums = [
        int(m.group(1))
        for entry in category_dir.iterdir()
        if entry.is_dir() and (m := pattern.match(entry.name))
    ]
    return max(nums) + 1 if nums else 1


# ---------------------------------------------------------------------------
# Accept: misc group (AI already tagged; just reclassify and move)
# ---------------------------------------------------------------------------

def accept_misc(folder_path: Path, target_category: str) -> dict:
    """
    Move a misc texture folder from _needs_review/misc/{name}/ to
    output/{category}/{new_name}/, rename all internal files, and
    update the JSON sidecar with the new category and texture_name.
    """
    if not folder_path.is_dir():
        return {"ok": False, "error": f"Folder not found: {folder_path}"}

    # Read sidecar
    sidecar: dict = {}
    for f in sorted(folder_path.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "error": f"Cannot read sidecar: {exc}"}
        # Skip non-dict JSON (e.g. third-party metadata stored as a list).
        if isinstance(data, dict):
            sidecar = data
            break

    material       = sidecar.get("material")       or "Unknown"
    material_type  = sidecar.get("material_type")  or "Unknown"
    dominant_color = sidecar.get("dominant_color") or "Grey"
    old_name       = sidecar.get("texture_name")   or folder_path.name

    config       = get_config()
    category_dir = Path(config.output_dir) / target_category
    base_slug    = f"{target_category}_{material}_{material_type}_{dominant_color}"
    variant      = _next_variant(category_dir, base_slug)
    texture_name = f"{base_slug}_{variant:02d}"
    out_dir      = category_dir / texture_name

    # Move the entire folder
    try:
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder_path), str(out_dir))
    except Exception as exc:
        return {"ok": False, "error": f"Move failed: {exc}"}

    # Rename every file inside the moved folder whose name starts with old_name
    if old_name != texture_name:
        for item in list(out_dir.iterdir()):
            if item.name.startswith(old_name):
                new_item_name = texture_name + item.name[len(old_name):]
                item.rename(out_dir / new_item_name)

    # Update the sidecar in the new location
    new_sidecar_path = out_dir / f"{texture_name}.json"
    sidecar["texture_name"] = texture_name
    sidecar["category"]     = target_category
    try:
        new_sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Sidecar update failed (non-fatal): %s", exc)

    logger.info(
        "Accepted misc '%s' -> %s/%s", old_name, target_category, texture_name
    )
    return {"ok": True, "texture_name": texture_name, "category": target_category}


# ---------------------------------------------------------------------------
# Accept: raw file (run AI tagging pipeline then write output)
# ---------------------------------------------------------------------------

def accept_raw(file_path: Path, target_category: str) -> dict:
    """
    Run AI tagging on a single raw review file and write output to
    the chosen category.  The user-selected category overrides whatever
    the model returns.
    """
    if not file_path.is_file():
        return {"ok": False, "error": f"File not found: {file_path}"}

    config = get_config()

    if not config.db_path.exists():
        return {
            "ok": False,
            "error": (
                f"Pipeline database not found at {config.db_path}. "
                "Run the pipeline at least once before using the review server."
            ),
        }

    db = DatabaseManager(config.db_path)

    try:
        base_name  = file_path.stem
        source_dir = file_path.parent
        group_id   = hashlib.sha256(
            f"{str(source_dir).lower()}::{base_name.lower().strip()}".encode()
        ).hexdigest()[:16]
        file_id = hashlib.sha256(str(file_path).encode()).hexdigest()[:16]

        # Register group and file (INSERT OR IGNORE is safe to call again)
        db.insert_group(
            group_id      = group_id,
            base_name     = base_name,
            source_dir    = str(source_dir),
            base_map_path = str(file_path),
            map_count     = 1,
            has_pat       = False,
        )
        db.insert_file(
            file_id         = file_id,
            group_id        = group_id,
            source_path     = str(file_path),
            map_type        = "unknown",
            is_base_map     = True,
            is_pat          = False,
            is_demo         = False,
            original_format = file_path.suffix.lstrip(".").lower(),
            width           = None,
            height          = None,
        )
        # Flush registrations before the AI call reads from the DB
        db._write_queue.join()

        group = PBRGroup(
            group_id      = group_id,
            base_name     = base_name,
            source_dir    = source_dir,
            base_map_path = file_path,
            image_files   = [file_path],
            map_types     = {str(file_path): "unknown"},
        )

        # Run AI tagger (calls Ollama -- may take 30-60 seconds)
        tagger = AITagger(config, db)
        result = tagger.tag_group(group)

        if result is None:
            return {
                "ok": False,
                "error": (
                    "AI tagging failed after all retries. "
                    "Confirm that Ollama is running and the model is available."
                ),
            }

        # Override category with the user's selection, then persist
        result["category"] = target_category
        db.set_group_ai_output(group_id, result)
        db.update_group_status(group_id, GroupStatus.FILE_OPS)
        db._write_queue.join()

        # Write output files via FileOps
        proc_result = ProcessResult(
            group_id      = group_id,
            crop_bbox     = None,
            is_tileable   = True,   # user accepted this item
            binned_resolution = False,
            base_dims     = None,
        )
        FileOps(config, db).process_one(group, proc_result)
        db._write_queue.join()

        logger.info(
            "Accepted raw '%s' -> %s (material=%s, type=%s, color=%s)",
            file_path.name, target_category,
            result.get("material"), result.get("material_type"),
            result.get("dominant_color"),
        )
        return {"ok": True, "category": target_category}

    finally:
        db.shutdown()


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_item(folder_path: Path, source_file: str, item_type: str) -> dict:
    """
    Move the item to _recycle_bin/manually_deleted/.

    misc:  moves the entire texture subfolder.
    raw:   moves the single image file (folder_path / source_file).
    """
    config  = get_config()
    dst_dir = Path(config.recycle_bin_dir) / "manually_deleted"
    dst_dir.mkdir(parents=True, exist_ok=True)

    if item_type == "misc":
        src = folder_path
        if not src.is_dir():
            return {"ok": False, "error": f"Folder not found: {src}"}
        dst = dst_dir / src.name
        try:
            shutil.move(str(src), str(dst))
        except Exception as exc:
            return {"ok": False, "error": f"Move failed: {exc}"}
        logger.info("Deleted misc folder '%s' -> recycle bin", src.name)

    else:
        src = folder_path / source_file if source_file else folder_path
        if not src.is_file():
            return {"ok": False, "error": f"File not found: {src}"}
        dst = dst_dir / src.name
        try:
            shutil.move(str(src), str(dst))
        except Exception as exc:
            return {"ok": False, "error": f"Move failed: {exc}"}
        logger.info("Deleted raw file '%s' -> recycle bin", src.name)

    return {"ok": True}


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------

class PreviewHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, format, *args):  # noqa: A002
        logger.info("%s - %s", self.address_string(), format % args)

    # ---- GET ---------------------------------------------------------------

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/index.html"):
            self._serve_html()
        elif path.startswith(f"/{THUMB_DIR}/"):
            self._serve_file(OUTPUT_DIR / path.lstrip("/"))
        elif path == "/api/status":
            self._json_ok({"ok": True, "mode": "server"})
        else:
            # Attempt to serve any relative path inside OUTPUT_DIR
            candidate = OUTPUT_DIR / path.lstrip("/")
            if candidate.is_file():
                self._serve_file(candidate)
            else:
                self._json_ok({"error": "Not found"}, status=404)

    # ---- POST --------------------------------------------------------------

    def do_POST(self):
        path = self.path.split("?")[0]

        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            self._json_ok({"ok": False, "error": f"Bad request body: {exc}"}, status=400)
            return

        if path == "/api/accept":
            self._handle_accept(body)
        elif path == "/api/delete":
            self._handle_delete(body)
        elif path == "/api/bulk-accept":
            self._handle_bulk_accept(body)
        elif path == "/api/bulk-delete":
            self._handle_bulk_delete(body)
        else:
            self._json_ok({"error": "Unknown endpoint"}, status=404)

    # ---- Endpoint handlers -------------------------------------------------

    def _handle_accept(self, body: dict):
        item_type       = body.get("item_type", "raw")
        folder_path_str = body.get("folder_path", "")
        source_file     = body.get("source_file", "")
        target_category = body.get("target_category", "")

        if not folder_path_str or not target_category:
            self._json_ok(
                {"ok": False, "error": "folder_path and target_category are required"},
                status=400,
            )
            return

        folder_path = Path(folder_path_str)

        if item_type == "misc":
            result = accept_misc(folder_path, target_category)
        else:
            file_path = folder_path / source_file if source_file else folder_path
            result    = accept_raw(file_path, target_category)

        self._json_ok(result)

    def _handle_delete(self, body: dict):
        item_type       = body.get("item_type", "raw")
        folder_path_str = body.get("folder_path", "")
        source_file     = body.get("source_file", "")

        if not folder_path_str:
            self._json_ok(
                {"ok": False, "error": "folder_path is required"},
                status=400,
            )
            return

        result = delete_item(Path(folder_path_str), source_file, item_type)
        self._json_ok(result)

    def _handle_bulk_accept(self, body: dict):
        """
        Accepts an array of items and moves each to the specified category.
        Items with a JSON sidecar (library items, misc review) use accept_misc().
        Raw review files (line_art, tileability_failed, etc.) use accept_raw()
        which re-runs AI tagging.  The user-selected category always overrides
        the AI result.
        """
        items           = body.get("items", [])
        target_category = body.get("target_category", "")
        if not items or not target_category:
            self._json_ok(
                {"ok": False, "error": "items and target_category are required"},
                status=400,
            )
            return

        results = []
        for item_data in items:
            item_type   = item_data.get("item_type", "raw")
            folder_path = Path(item_data.get("folder_path", ""))
            source_file = item_data.get("source_file", "")
            name        = item_data.get("name") or folder_path.name

            if item_type == "misc":
                result = accept_misc(folder_path, target_category)
            else:
                file_path = folder_path / source_file if source_file else folder_path
                result    = accept_raw(file_path, target_category)

            results.append({"item": name, **result})

        failed = [r for r in results if not r.get("ok")]
        logger.info(
            "Bulk accept: %d items -> %s (%d ok, %d failed)",
            len(items), target_category, len(results) - len(failed), len(failed),
        )
        self._json_ok({"ok": True, "results": results})

    def _handle_bulk_delete(self, body: dict):
        """
        Moves an array of items to _recycle_bin/manually_deleted/.
        Folder-based items (misc) move the entire texture subfolder.
        Raw items move the single source file.
        """
        items = body.get("items", [])
        if not items:
            self._json_ok(
                {"ok": False, "error": "items is required"},
                status=400,
            )
            return

        results = []
        for item_data in items:
            item_type   = item_data.get("item_type", "raw")
            folder_path = Path(item_data.get("folder_path", ""))
            source_file = item_data.get("source_file", "")
            name        = item_data.get("name") or folder_path.name

            result = delete_item(folder_path, source_file, item_type)
            results.append({"item": name, **result})

        failed = [r for r in results if not r.get("ok")]
        logger.info(
            "Bulk delete: %d items (%d ok, %d failed)",
            len(items), len(results) - len(failed), len(failed),
        )
        self._json_ok({"ok": True, "results": results})


    # ---- Response helpers --------------------------------------------------

    def _serve_html(self):
        try:
            html = rebuild_html()
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:
            logger.error("Error building HTML: %s", exc, exc_info=True)
            self._json_ok({"error": str(exc)}, status=500)

    def _serve_file(self, path: Path):
        if not path.is_file():
            self._json_ok({"error": "File not found"}, status=404)
            return

        content_types = {
            ".jpg":  "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png":  "image/png",
            ".gif":  "image/gif",
            ".html": "text/html; charset=utf-8",
            ".json": "application/json",
        }
        ct   = content_types.get(path.suffix.lower(), "application/octet-stream")
        data = path.read_bytes()

        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_ok(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global OUTPUT_DIR

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Local review server for the texture library preview."
    )
    parser.add_argument(
        "--output", required=True,
        help="Path to the pipeline output folder (must contain library_preview.html).",
    )
    parser.add_argument(
        "--port", type=int, default=8765,
        help="Port to listen on (default: 8765).",
    )
    args = parser.parse_args()

    OUTPUT_DIR = Path(args.output).resolve()

    if not OUTPUT_DIR.is_dir():
        sys.exit(f"ERROR: Output folder not found: {OUTPUT_DIR}")

    if not (OUTPUT_DIR / HTML_FILENAME).exists():
        sys.exit(
            f"ERROR: {HTML_FILENAME} not found in:\n"
            f"  {OUTPUT_DIR}\n\n"
            f"Run generate_preview.py first:\n"
            f"  python generate_preview.py --output \"{OUTPUT_DIR}\""
        )

    if not PIPELINE_DIR.is_dir():
        sys.exit(
            f"ERROR: Pipeline directory not found:\n"
            f"  {PIPELINE_DIR}\n\n"
            f"Expected at: Texture Library Image Sorter/texture_pipeline/"
        )

    logger.info("=" * 60)
    logger.info("Texture Library Review Server")
    logger.info("Output dir  : %s", OUTPUT_DIR)
    logger.info("Pipeline    : %s", PIPELINE_DIR)
    logger.info("URL         : http://localhost:%d", args.port)
    logger.info("Press Ctrl+C to stop.")
    logger.info("=" * 60)

    server = http.server.HTTPServer(("localhost", args.port), PreviewHandler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped.")


if __name__ == "__main__":
    main()
