"""Agent workflow: disease/cell state to a defensible next experiment.

The pipeline is INGEST -> GATE -> SCORE -> HERO_CHECKPOINT -> STRUCTURE ->
NEXT_EXPERIMENT -> COMPLETE. State lives on disk under ``runs/<run_id>/``, so a
run resumes from the filesystem alone.
"""

from .config import RunConfig
from .models import SCHEMA_VERSION, Stage

__all__ = ["RunConfig", "Stage", "SCHEMA_VERSION"]
