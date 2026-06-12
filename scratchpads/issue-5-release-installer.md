# Issue #5 — Tag-triggered release workflow building a Windows installer

**Issue:** https://github.com/lukekv/StarlingMurmurations/issues/5

## Task breakdown

1. Make `pipeline_gui.py` frozen-aware — the GUI launches pipeline scripts via `subprocess([sys.executable, script.py])`, which breaks under PyInstaller (`sys.executable` is the exe, not Python):
   - `--run-pipeline` / `--run-rescan` / `--run-preview` dispatch flags: the frozen exe re-enters its bundled pipeline modules via `runpy.run_module(..., run_name="__main__")`
   - `_spawn_cmd()` / `_spawn_cwd()` helpers: dev mode unchanged (python + script path), frozen mode uses the exe + flag
   - `gui_settings.json` moves to `%LOCALAPPDATA%\StarlingMurmurations\` when frozen (Program Files is read-only)
2. `installer/StarlingMurmurations.spec` — one-folder windowed build, `collect_all("customtkinter")` for theme data, pipeline modules as hiddenimports
3. `installer/installer.iss` — Inno Setup: Program Files install, Start Menu shortcut, optional desktop icon
4. `.github/workflows/release.yml` — windows-latest; on `v*` tag: build → dispatch smoke test → ISCC → `gh release create` with the installer attached; on `workflow_dispatch`: same but artifact-only
5. README: release procedure + Ollama prerequisite. `.gitignore`: `build/`, `dist/`, `installer/Output/`

## Files touched

- `pipeline_gui.py` (frozen-aware launch/settings)
- `installer/StarlingMurmurations.spec`, `installer/installer.iss` (new)
- `.github/workflows/release.yml` (new)
- `README.md`, `.gitignore`

## Test / verification strategy

Verified locally on Python 3.14 + PyInstaller 6.20:
- Build succeeds (257 MB one-folder app)
- `StarlingMurmurations.exe --run-pipeline --help` → exit 0, prints main.py's full argparse help (bundled modules import correctly)
- `--run-rescan --help` and `--run-preview --help` → exit 0 with correct usage lines
- Non-frozen import check: `_spawn_cmd`/`_spawn_cwd`/settings path identical to pre-change dev behavior

NOT yet verified (requires the workflow on the default branch): the Actions run itself, the Inno compile on the runner, and an install on a Python-less machine. After merge: trigger workflow_dispatch from the Actions tab, download the artifact, install on a clean machine.

## Notes

- Windowed exes still pipe stdout correctly when launched with redirected handles — the GUI's log streaming works for frozen children (confirmed via the smoke tests' captured output).
- Unsigned installer → SmartScreen warning on first run; accepted as out of scope.
