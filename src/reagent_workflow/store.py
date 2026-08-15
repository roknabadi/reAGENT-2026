"""Filesystem run store — the source of truth for a run.

Everything a run needs to resume lives under ``runs/<run_id>/``. Nothing is
carried in conversation history. Writes are atomic, JSONL is append-only, and
concurrent access to one run is guarded by an advisory lock.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .models import ArtifactRef, RunManifest, RunState

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Redacted before anything reaches disk. Traces and reports must stay publishable.
SECRET_KEY_HINTS = (
    "token", "secret", "password", "passwd", "api_key", "apikey",
    "credential", "authorization", "auth", "private_key", "session_key",
)

# Key names that contain a hint substring but hold counts, not credentials.
# Without this, "token_usage" and "total_input_tokens" get redacted into strings
# and fail validation on the way back in.
SAFE_KEY_NAMES = frozenset({
    "token_usage", "token_estimate", "tokens", "total_input_tokens",
    "total_output_tokens", "input_tokens", "output_tokens", "token_count",
    "max_tokens", "prompt_tokens", "completion_tokens",
})
_SECRET_VALUE_RE = re.compile(
    r"(hf_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{12,})"
)

RUN_SUBDIRS = (
    "input", "evidence", "decisions", "traces",
    "structure", "structure/boltz2", "structure/esmfold2",
    "improvement", "context", "reports",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json(payload: Any) -> str:
    """Stable JSON for hashing: sorted keys, no incidental whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(payload: Any) -> str:
    return sha256_text(canonical_json(payload))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def git_commit(repo_root: Path | None = None) -> str | None:
    """Current commit, or None outside a repository. Never raises."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root or Path.cwd()),
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def redact(value: Any) -> Any:
    """Strip anything that looks like a credential, by key name or by shape."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered not in SAFE_KEY_NAMES and any(
                hint in lowered for hint in SECRET_KEY_HINTS
            ):
                out[key] = "[REDACTED]"
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub("[REDACTED]", value)
    return value


class RunLockError(RuntimeError):
    """Raised when another process holds this run."""


class RunExistsError(RuntimeError):
    """Raised rather than silently overwriting an existing run."""


class RunStore:
    """Typed reader/writer for one run directory."""

    def __init__(self, root: Path, run_id: str) -> None:
        if not RUN_ID_RE.match(run_id):
            raise ValueError(f"invalid run_id {run_id!r}")
        self.root = Path(root)
        self.run_id = run_id
        self.run_dir = self.root / run_id
        self._lock_handle = None

    # ---------------------------------------------------------------- paths
    @property
    def state_path(self) -> Path:
        return self.run_dir / "run_state.json"

    @property
    def manifest_path(self) -> Path:
        return self.run_dir / "manifest.json"

    @property
    def internal_trace_path(self) -> Path:
        return self.run_dir / "traces" / "internal_trace.jsonl"

    @property
    def benchflow_trace_path(self) -> Path:
        return self.run_dir / "traces" / "benchflow_trace.jsonl"

    def path(self, *parts: str) -> Path:
        return self.run_dir.joinpath(*parts)

    def relative(self, path: Path) -> str:
        """Manifest paths are run-relative so a run directory can be moved."""
        return str(Path(path).resolve().relative_to(self.run_dir.resolve()))

    # ----------------------------------------------------------- lifecycle
    def exists(self) -> bool:
        return self.state_path.exists()

    def create(self, *, force: bool = False) -> None:
        """Lay out the run directory. Refuses to clobber an existing run.

        ``force`` clears the previous run's artifacts. It used to leave them in
        place, so a re-inited run inherited the old decisions, traces and
        reports: append-only JSONL logs carried both runs' records, and a run
        that now rejects a candidate still showed the previous run's approval.
        """
        if self.exists() and not force:
            raise RunExistsError(
                f"run {self.run_id!r} already exists at {self.run_dir}; "
                "choose another run id or pass force"
            )
        if self.exists() and force:
            import shutil  # noqa: PLC0415

            for child in self.run_dir.iterdir():
                if child.name == ".lock":
                    continue  # held by this process; removing it drops the guard
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        for sub in RUN_SUBDIRS:
            (self.run_dir / sub).mkdir(parents=True, exist_ok=True)

    @contextmanager
    def lock(self, *, timeout_note: str = "") -> Iterator[None]:
        """Advisory, process-safe, released even if this process is killed."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.run_dir / ".lock"
        handle = lock_path.open("a+")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.seek(0)
                holder = handle.read().strip() or "unknown process"
                raise RunLockError(
                    f"run {self.run_id!r} is locked by {holder}. {timeout_note}".strip()
                ) from exc
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()} at={utc_now()}")
            handle.flush()
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    # --------------------------------------------------------------- writes
    def write_atomic(self, path: Path, text: str) -> Path:
        """Write via a temp file in the same directory, then rename."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".part")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return path

    def write_json(self, path: Path, payload: Any) -> Path:
        return self.write_atomic(path, json.dumps(redact(payload), indent=2, default=str) + "\n")

    def write_model(self, path: Path, model: BaseModel) -> Path:
        return self.write_json(path, model.model_dump(mode="json"))

    def append_jsonl(self, path: Path, payload: Any) -> Path:
        """Append one record. O_APPEND keeps concurrent appends line-atomic."""
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(redact(payload), sort_keys=False, default=str) + "\n"
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return path

    def append_model(self, path: Path, model: BaseModel) -> Path:
        return self.append_jsonl(path, model.model_dump(mode="json"))

    # ---------------------------------------------------------------- reads
    def read_json(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
        return records

    def load_state(self) -> RunState:
        if not self.exists():
            raise FileNotFoundError(f"no run at {self.run_dir}")
        return RunState.model_validate(self.read_json(self.state_path))

    def save_state(self, state: RunState) -> None:
        state.updated_at = utc_now()
        self.write_model(self.state_path, state)

    # ------------------------------------------------------------- manifest
    def rebuild_manifest(self, **fields: Any) -> RunManifest:
        """Hash every artifact currently on disk.

        The manifest and the lock file are excluded: the manifest cannot contain
        its own hash, and the lock is transient process state.
        """
        existing = None
        if self.manifest_path.exists():
            existing = RunManifest.model_validate(self.read_json(self.manifest_path))

        artifacts: list[ArtifactRef] = []
        for file_path in sorted(self.run_dir.rglob("*")):
            if not file_path.is_file():
                continue
            name = file_path.name
            if name in {"manifest.json", ".lock"} or name.startswith(".tmp-"):
                continue
            artifacts.append(
                ArtifactRef(
                    path=self.relative(file_path),
                    sha256=sha256_file(file_path),
                    bytes=file_path.stat().st_size,
                )
            )

        manifest = RunManifest(
            run_id=self.run_id,
            created_at=existing.created_at if existing else utc_now(),
            updated_at=utc_now(),
            artifacts=artifacts,
            **{
                **({} if existing is None else {
                    "git_commit": existing.git_commit,
                    "config_hash": existing.config_hash,
                    "soul_version": existing.soul_version,
                    "schema_versions": existing.schema_versions,
                    "tool_versions": existing.tool_versions,
                    "fixture_run": existing.fixture_run,
                }),
                **fields,
            },
        )
        self.write_model(self.manifest_path, manifest)
        return manifest

    def verify_manifest(self) -> list[str]:
        """Return a list of drift descriptions; empty means the run is intact."""
        manifest = RunManifest.model_validate(self.read_json(self.manifest_path))
        problems: list[str] = []
        for ref in manifest.artifacts:
            target = self.run_dir / ref.path
            if not target.exists():
                problems.append(f"missing artifact: {ref.path}")
            elif sha256_file(target) != ref.sha256:
                problems.append(f"hash mismatch: {ref.path}")
        return problems


def list_runs(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "run_state.json").exists())
