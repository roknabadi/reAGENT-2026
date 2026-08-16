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

# Same failure, different variable. proto-tools keeps its tool environments
# under PROTO_HOME, `activate.sh` points that at the repo's `.proto`, and a
# process started any other way silently falls back to ~/.proto — where nothing
# is installed. The symptom is not an error: the first docking call starts a
# multi-minute micromamba install of an environment that already exists twenty
# metres away. Pointed at the repo copy here, for the same reason the key is.
_ROOT = _pathlib.Path(__file__).resolve().parents[2]
if (_ROOT / ".proto").is_dir():
    _os.environ.setdefault("PROTO_HOME", str(_ROOT / ".proto"))
    _os.environ.setdefault("PROTO_MODEL_CACHE", str(_ROOT / ".proto" / "models"))

from .config import RunConfig  # noqa: E402
from .models import SCHEMA_VERSION, Stage  # noqa: E402

__all__ = ["RunConfig", "Stage", "SCHEMA_VERSION"]
