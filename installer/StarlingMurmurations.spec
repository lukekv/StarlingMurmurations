# PyInstaller spec for the StarlingMurmurations one-folder build.
# Build from the repo root:  pyinstaller installer/StarlingMurmurations.spec --noconfirm

import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (SPECPATH injected by PyInstaller)
PIPELINE = os.path.join(ROOT, "Texture Library Image Sorter", "texture_pipeline")

datas, binaries, hiddenimports = [], [], []
# customtkinter ships theme JSON files that must be collected explicitly.
for pkg in ("customtkinter",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# The pipeline scripts are bundled as importable modules; the frozen exe
# re-enters them via its --run-* dispatch flags (pipeline_gui._frozen_dispatch).
hiddenimports += [
    "main",
    "config",
    "database",
    "scanner",
    "scanner_helpers",
    "deduplicator",
    "image_processor",
    "ai_tagger",
    "file_ops",
    "rescan_library",
    "generate_preview",
]

a = Analysis(
    [os.path.join(ROOT, "pipeline_gui.py")],
    pathex=[ROOT, PIPELINE],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="StarlingMurmurations",
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="StarlingMurmurations",
)
