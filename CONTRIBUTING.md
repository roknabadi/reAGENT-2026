# Collaboration guide

## First-time setup

```bash
git clone git@github.com:roknabadi/reAGENT-2026.git
cd reAGENT-2026
./scripts/setup.sh
```

Add your personal Tamarind key to `.env`, then start a session:

```bash
source ./activate.sh
claude
```

Approve the project MCP server when Claude asks. Check the connection with
`/mcp`. Never commit `.env` or paste credentials into prompts, issues, logs, or
reports.

## Start a change

Create a small branch from the latest `main`:

```bash
git switch main
git pull --ff-only
git switch -c feature/short-description
```

Keep one logical change per branch. Do not assign permanent directories to
people; coordinate through stable typed inputs and outputs.

## Test locally

```bash
source ./activate.sh
python -m unittest discover -s tests -v
git diff --check
```

For comparable agent runs and trace publication, follow `benchflow/README.md`.

Optional services or models may skip their own integration tests when they are
not installed. A pull request must state which checks actually ran.

## Review and commit

Inspect exactly what will be committed:

```bash
git status --short
git diff
git add path/to/intended-file another/intended-file
git diff --cached
git commit -m "Describe the change"
```

Stage explicit paths instead of `git add .` so local data and unrelated work do
not enter a commit accidentally.

## Sync and push

Before pushing, incorporate current `main` and rerun tests:

```bash
git fetch origin
git merge origin/main
python -m unittest discover -s tests -v
git push -u origin HEAD
```

Open a draft pull request:

```bash
gh pr create --draft --fill
```

The pull request should say what changed, why, which public sources or data were
used, how it was tested, and what remains uncertain. Another collaborator can
then review and merge it on GitHub.

## Pull someone else's work

```bash
git fetch origin
git switch branch-name
git pull --ff-only
```

To test a pull request without merging it:

```bash
gh pr checkout PR_NUMBER
python -m unittest discover -s tests -v
```

## Repository rules

- Use only public or hackathon-authorized material; follow `SOURCE_POLICY.md`.
- Record external evidence and versions in `SOURCES.md`.
- Keep generated outputs and downloaded datasets out of Git.
- Use synthetic fixtures only for software tests and label them clearly.
- Do not claim experimental validation from computational results.
- Never push directly to `main`; use a short branch and pull request.
