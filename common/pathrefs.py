"""Path references used in task specs (shared by the compiler and the executor)."""

from __future__ import annotations


# `data:outputs/…` and the other areas (common/tools/areas.py) point into DATA_DIR;
# anything else is relative to WORKSPACE_DIR.
DATA_PREFIX = "data:"
