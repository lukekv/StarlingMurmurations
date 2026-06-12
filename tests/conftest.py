"""
Pipeline modules use flat imports (`from config import Config`), so the
texture_pipeline directory itself must be on sys.path before any test
imports a pipeline module.
"""

import sys
from pathlib import Path

PIPELINE_DIR = (
    Path(__file__).resolve().parent.parent
    / "Texture Library Image Sorter"
    / "texture_pipeline"
)
sys.path.insert(0, str(PIPELINE_DIR))
