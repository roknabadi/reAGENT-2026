# Decisions

Append at the top. One entry per decision. Never edit an old entry — supersede
it with a new one that says what it replaces.

Template:

```
## YYYY-MM-DD — <one-line decision>
Decided by: <person>
Why: <evidence, with sources recorded in SOURCES.md>
Alternatives rejected: <what and why>
Reversible: yes/no — <what would reverse it>
```

Keep observed evidence, computed results, predictions, and hypotheses labeled
as such. No claims of binding, safety, efficacy, or experimental validation
from computational results.

---

## 2026-08-15 — Coordinate through `team/`, not chat
Decided by: Vraj
Why: judging weights inspectability; decisions made in chat leave no trace in
the repo. Per-person status files avoid merge conflicts; shared logs are
append-at-top for the same reason.
Alternatives rejected: an issue tracker (agents can't write to it as cheaply);
one shared status file (conflicts on every push).
Reversible: yes — the files are plain Markdown, delete the directory.
