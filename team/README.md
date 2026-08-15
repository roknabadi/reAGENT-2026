# Team coordination

Plain files. Humans and agents write the same way. Newest entry on top.

```
team/
  ROLES.md          who does what (reference; changes as the project changes)
  DECISIONS.md      shared, append-only: what we decided and why
  CHECKPOINTS.md    human-in-the-loop gates; a gate is open until someone signs it
  status/<name>.md  one file per person: did / next / blocked
```

`status/` is one file per person so two people never edit the same lines.
`DECISIONS.md` and `CHECKPOINTS.md` are shared — always add at the top, never
rewrite someone else's entry, and you will not get a conflict.

## Read the board

```bash
head -8 team/status/*.md                            # everyone's latest
grep '^Blocked:' team/status/*.md | grep -v 'none'  # open blockers
head -40 team/DECISIONS.md                          # recent decisions
grep '^## ' team/CHECKPOINTS.md                     # which gates are still OPEN
```

## Post an update

Add to the top of your own `team/status/<name>.md`:

```
## 2026-08-15 — Vraj
Did: wired Paperclip search into the candidate pipeline; typed output in src/...
Next: DepMap gate thresholds
Blocked: none
```

Keep the header lines intact — `head -8 team/status/*.md` shows the newest
entry only because the header is short.

`Blocked:` is required. Write `none` when you are not blocked. Name the person
who can unblock you: `Blocked: need Andrey to confirm the MED23 interface`.

## Post a decision

Add to the top of `team/DECISIONS.md`:

```
## 2026-08-15 — Hero pair: <indication> / <TF> / <Mediator>
Decided by: Andrey (whole team input)
Why: <evidence, with sources>
Alternatives rejected: <what and why>
Reversible: yes/no — <what would reverse it>
```

Agents: record the decision *and the evidence trace*, not just the conclusion.
Judging is on inspectability. Cite sources in `SOURCES.md`; keep observed
evidence, computed results, predictions, and hypotheses separate.

## Sign a checkpoint

Checkpoints in `CHECKPOINTS.md` are human gates. An agent may propose and fill
in evidence, but only the named human flips the status to `PASSED`. Do not
proceed past an open gate.

## The data contract is code, not a doc

What we hand each other is typed in `src/dependency_scout/models.py` —
`RankedCandidate`, `ProtoScreenSpec`, `SourceRecord`, `GateResult`. Read that
file, not a prose copy of it. Worked examples are in `examples/`. Changing a
shared type is a `DECISIONS.md` entry.

## Git

**Branch only. Never push to `main`** — not for code, not for a status entry,
not for a one-line typo fix. `main` moves only through a reviewed pull request,
and only when someone asks for it. Full workflow is in
[`../CONTRIBUTING.md`](../CONTRIBUTING.md).

```bash
git fetch origin
git switch -c feature/short-description origin/main
# work
git add team/status/vraj.md
git commit -m "status: vraj 2026-08-15"
git push -u origin HEAD
```

### Stay in sync — do this often

Six people on one repo drift apart in hours. Fetch before you start, and again
every time you sit back down:

```bash
git fetch origin
git log --oneline HEAD..origin/main    # what landed while you were heads-down
git merge origin/main                  # take it now, not at 2am
python -m unittest discover -s tests
```

Merging `origin/main` early and often turns one painful end-of-day conflict
into several trivial ones. If `git log HEAD..origin/main` is empty you are
current.

Never force-push `main` or a branch someone else is on.
