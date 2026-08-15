# re:AGENT project instructions

## Purpose

Build a small, working, scientifically defensible hackathon prototype using
only public information and code created for this project. Keep the repository
easy for any collaborator to clone, understand, run, and change.

## Working principles

- Start by reading `README.md`, `SOURCE_POLICY.md`, and the relevant code.
- Prefer the smallest end-to-end implementation that produces a verifiable
  artifact.
- Do not encode contributors, ownership, or personal filesystem paths in code
  or documentation.
- Keep domain logic in importable modules rather than notebooks or one-off
  shell commands.
- Define shared inputs and outputs as typed models before connecting modules.
- Preserve existing work. Make focused changes and avoid unrelated rewrites.
- Add or update a test for behavior that affects the scientific result.
- Run the narrowest relevant tests, then the full test suite before handoff.
- Record important assumptions and unresolved questions in the output; do not
  silently fill missing evidence.

## Scientific and data boundaries

- Never use Therna or BioReasonRNA code, data, prompts, weights, outputs,
  credentials, schemas, unpublished results, or derived proprietary knowledge.
- Use only public sources, sponsor-provided services, and general scientific
  knowledge.
- Label synthetic fixtures as synthetic. They test software and are not
  scientific evidence.
- Record public datasets, papers, models, and structures in `SOURCES.md` with
  URLs, versions, dates, licenses or terms, and hashes when practical.
- Distinguish observed evidence, computed results, model predictions, and
  hypotheses in both code and user-facing output.
- Do not claim experimental validation, binding, efficacy, selectivity, or
  safety from computational results alone.

## External computation

- Tamarind is available through the project MCP configuration.
- Inspect available tool schemas and validate inputs before submitting jobs.
- Do not submit paid, long-running, batch, or destructive jobs unless the user
  has explicitly requested that execution.
- Give jobs unique, descriptive names and save the input configuration, job ID,
  status, returned artifacts, and relevant version information.
- Prefer a cheap smoke test before a larger run.
- Never place API keys or credentials in tracked files, prompts, logs, or
  generated reports.

## Completion checklist

Before presenting work as complete:

1. The relevant command runs from the repository root.
2. Tests pass.
3. Public sources and generated outputs are distinguishable.
4. No secret or proprietary material is present.
5. Another collaborator can understand the change from the README and commit.
