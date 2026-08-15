"""Loads SOUL.md and hands each stage only the rules that stage needs.

Parsing the Markdown rather than duplicating the rules in Python keeps one
authoritative copy: editing SOUL.md changes agent behaviour, and the version
recorded in the manifest is the version that ran.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .models import Stage

_VERSION_RE = re.compile(r'soul_version:\s*"([^"]+)"')
_RULE_ROW_RE = re.compile(r"^\|\s*`([a-z0-9-]+)`\s*\|\s*(.+?)\s*\|$")
_STAGE_ROW_RE = re.compile(r"^\|\s*`([A-Z_]+)`\s*\|\s*(.+?)\s*\|$")


@dataclass(frozen=True)
class Soul:
    version: str
    rules: dict[str, str]
    stage_rules: dict[Stage, tuple[str, ...]]

    def for_stage(self, stage: Stage) -> list[str]:
        """The rule texts to inline into a stage prompt, in declared order."""
        return [self.rules[rid] for rid in self.stage_rules.get(stage, ()) if rid in self.rules]

    def rule_ids(self, stage: Stage) -> list[str]:
        return list(self.stage_rules.get(stage, ()))


def default_soul_path() -> Path:
    return Path(__file__).resolve().parents[2] / "SOUL.md"


@lru_cache(maxsize=8)
def load_soul(path: str | None = None) -> Soul:
    soul_path = Path(path) if path else default_soul_path()
    text = soul_path.read_text(encoding="utf-8")

    version_match = _VERSION_RE.search(text)
    if not version_match:
        raise ValueError(f"{soul_path} has no soul_version")

    rules: dict[str, str] = {}
    stage_rules: dict[Stage, tuple[str, ...]] = {}
    for line in text.splitlines():
        line = line.strip()
        rule_match = _RULE_ROW_RE.match(line)
        if rule_match:
            rules[rule_match.group(1)] = rule_match.group(2)
            continue
        stage_match = _STAGE_ROW_RE.match(line)
        if stage_match:
            try:
                stage = Stage(stage_match.group(1))
            except ValueError:
                continue
            ids = tuple(rid.strip(" `") for rid in stage_match.group(2).split(","))
            stage_rules[stage] = tuple(rid for rid in ids if rid)

    if not rules:
        raise ValueError(f"{soul_path} declares no rules")

    unknown = {rid for ids in stage_rules.values() for rid in ids} - set(rules)
    if unknown:
        raise ValueError(f"{soul_path} maps unknown rule ids: {sorted(unknown)}")

    return Soul(version=version_match.group(1), rules=rules, stage_rules=stage_rules)
