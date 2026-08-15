"""Input pinning and determinism classification.

Two different things get called "the pipeline", and only one of them can be
deterministic:

* **Computed** — DepMap statistics, gates, ranking, structure parsing. Pure
  functions of pinned files. Same inputs must give byte-identical outputs, and
  `scripts/verify_pipeline.py` fails the build if they ever do not.
* **Retrieved** — Paperclip search, any LLM reader. The corpus grows, retrieval
  is ranked, and models are sampled. These cannot be pinned and must never be
  treated as though they were.

The failure this guards against is real and already happened once: a parser bug
made a live search return nothing, and the interface reported it as a genuine
null result. Anything retrieved carries `deterministic=False` so it can never be
silently promoted to evidence.
"""
from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Reproducibility(StrEnum):
    COMPUTED = "computed"      # pure function of pinned inputs; byte-reproducible
    RETRIEVED = "retrieved"    # corpus/network dependent; pinned only by timestamp
    SAMPLED = "sampled"        # model output; pinned only by seed, if that


class InputFile(BaseModel):
    """A pinned input. The digest is what makes a result checkable later."""
    model_config = ConfigDict(extra="forbid")
    name: str
    path: str
    sha256: str
    bytes: int

    @classmethod
    def pin(cls, name: str, path: str | Path) -> "InputFile":
        p = Path(path)
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return cls(name=name, path=str(p), sha256=h.hexdigest(), bytes=p.stat().st_size)


class RunProvenance(BaseModel):
    """Everything needed to decide whether two runs are comparable."""
    model_config = ConfigDict(extra="forbid")
    inputs: list[InputFile] = Field(default_factory=list)
    reproducibility: Reproducibility = Reproducibility.COMPUTED
    code_version: str | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        """One digest over every input. Two runs sharing it saw the same data."""
        h = hashlib.sha256()
        for f in sorted(self.inputs, key=lambda x: x.name):
            h.update(f.name.encode()); h.update(f.sha256.encode())
        return h.hexdigest()

    def matches(self, other: "RunProvenance") -> bool:
        return self.fingerprint == other.fingerprint


def digest_payload(obj) -> str:
    """Stable digest of a JSON-serialisable result, for regression comparison."""
    import json
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True).encode()).hexdigest()
