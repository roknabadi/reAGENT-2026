"""Agent workflow: disease/cell state to a defensible next experiment.

The pipeline is INGEST -> GATE -> SCORE -> HERO_CHECKPOINT -> STRUCTURE ->
NEXT_EXPERIMENT -> COMPLETE. State lives on disk under ``runs/<run_id>/``, so a
run resumes from the filesystem alone.
"""

import os as _os
import pathlib as _pathlib

# `activate.sh` exports .env; `.envrc` and a bare `python ui/serve.py` do not. A
# key that is present on disk and absent from the process is indistinguishable
# from no key at all -- `agent.available()` returned False, the pipeline took
# its deterministic path, and the API showed no traffic for a key that was
# configured correctly the whole time. Loaded here because every entry point
# imports this package. An already-exported value wins: the shell is explicit,
# the file is a default.
_ENV_FILE = _pathlib.Path(__file__).resolve().parents[2] / ".env"
if _ENV_FILE.is_file():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _key, _, _value = _line.partition("=")
        _key = _key.strip()
        if _key and not _key.startswith("#") and _key not in _os.environ:
            _os.environ[_key] = _value.strip().strip('"').strip("'")

from .config import RunConfig  # noqa: E402
from .models import SCHEMA_VERSION, Stage  # noqa: E402

__all__ = ["RunConfig", "Stage", "SCHEMA_VERSION"]
