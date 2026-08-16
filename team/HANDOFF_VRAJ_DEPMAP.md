# DepMap table — handoff to Vraj, no USB needed

The blocker was never physical media. Both DepMap and the Lambert TF list are
public downloads; the pipeline scripts fetch and cache them over the network
themselves. Two independent things below — do the quick one first, it costs
nothing.

## 0. Quick win — your existing round-01 data already benefits

If `downloads/CRISPRGeneEffect.csv` and `downloads/Model.csv` are still on
your machine from round-01, you don't need to wait on anything. I just ported
the specificity-first gate into `src/dependency_scout/ranking.py::gate()`
(`ad18dd7`, pushed to `main`) — the old gate was median-only and structurally
cannot see a dependency that only shows up in a subset of a pooled context
(ASCL1/POU2F3 in SCLC is the proof case). The new gate is an OR: median path
**or** specificity path (>=10% of target models dependent, <=5% of everything
else). Re-running your existing command picks it up automatically, no new
download:

```bash
cd reAGENT-2026 && git pull
dependency-scout discover --gene-effect downloads/CRISPRGeneEffect.csv \
  --models downloads/Model.csv --context Lung --tf-list downloads/lambert_tfs.csv \
  --source-version "DepMap Public 24Q2" --output outputs/lung_tf_candidates_v2.json
```

Diff `outputs/lung_tf_candidates_v2.json` against your round-01 output — any
gene that's newly eligible is a specificity-path candidate the old gate was
blind to.

## 1. Full regeneration — my table, reproducible on your machine

This is the actual thing that was going to go over USB: `tests/re-agent_discovery`
is a separate, self-contained pipeline (24Q4, restricted to the Lambert TF
universe, two-pass lineage+subtype scan, same gate logic as above plus a
Mediator/literature evidence override). It downloads its own data, no auth,
no manual fetch:

```bash
cd reAGENT-2026/tests/re-agent_discovery
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # requests, pandas, numpy, scipy — small, no rdkit/pydantic

cd src
python stage0_tf_universe.py           # Lambert et al. TF list -> data/cache/tf_universe.csv
python stage1_depmap.py                # downloads CRISPRGeneEffect.csv + Model.csv from figshare (24Q4, ~400MB+, first run only), then runs both passes
```

`stage1_depmap.py` downloads are cached in `data/cache/` after the first run
(not re-fetched on subsequent runs). The two-pass scan itself takes about
80 seconds once the files are local. Output lands at
`data/results/stage1_dependency_hits.csv` — ~132,700 rows, one row per
TF x context (lineage and subtype level both), with `dependency_flag`,
`qvalue`, `target_dependent_fraction`/`other_dependent_fraction`, and
`confidence_flag` (`full` at n>=15, `low` down to the n>=5 floor).

Config/thresholds live in `src/config.py` if you want to sanity-check the
constants before trusting the run — pinned DepMap release, both gate paths,
and the two confidence cutoffs are all named there, not buried in the scan
code.

## 2. What that table unlocks

Once `data/results/stage1_dependency_hits.csv` exists, `discover.py` is live:

```bash
cd src
python discover.py context="Prostate Adenocarcinoma"
python discover.py tf=ELK1
```

`discover()` joins the dependency table against live STRING (Mediator
connectivity), HPA (tissue safety), UniProt/ChEMBL/PDB (via Paperclip cache —
see §3), and ENCODE (ChIP-seq existence), and returns one flat table: every
(TF, context, Mediator subunit) combination with its own gate verdict, no
composite score. `list_known_contexts()` / `list_known_genes()` enumerate what
a given run actually covers.

## 3. Two small files I have, that you don't — and don't need USB for either

Both are tiny (under 25KB combined) and excluded from git only because the
whole `tests/re-agent_discovery/data/` directory is gitignored as one blanket
rule sized for the 28MB+400MB DepMap files, not because these specific files
are large:

- `data/results/literature_search_cache.json` (7.7KB) — curated Paperclip
  literature findings, including ELK1-MED23's real structural claim (PDB
  9F6Y). This is what makes `inclusion_reason=mediator_literature_override`
  show ELK1 in a disease-context query even though it has zero DepMap
  dependency signal anywhere.
- `data/results/stage4_mediator_string_hits.csv` (16KB) — cached STRING
  Mediator-connectivity hits.

Since you're also on Claude Code: easiest is I just send you these two files
directly (Slack/Drive/email — they're small enough that USB was overkill even
in the original plan). If you'd rather regenerate them yourself instead of
waiting on me: `stage4_mediator_string_hits.csv` is a live public STRING API
call with no auth (`stage4_mediator_string.py`), cheap to redo per-TF. The
literature cache is not reproducible without redoing the actual Paperclip
searches — if you want to do that yourself rather than take my cache, say so
and I'll point you at which TF-subunit pairs still need it
(`agent_tools.record_literature_result()` is the write path).

## Traps that cost me time, so they don't cost you any

- DepMap's own portal API sits behind a Cloudflare bot-check that blocks
  headless/scripted access — that's why `config.py` pins exact figshare
  `file_id`s instead of resolving "latest" at runtime. If you bump
  `DEPMAP_RELEASE`, you have to manually re-verify the new file IDs via
  `https://api.figshare.com/v2/articles/<article_id>`, the API doesn't
  reliably surface the newest release by title search.
- `gate()`'s pan-essential rejection (GTF2B, CTCF, MYC, AHCTF1) still works
  correctly under the new OR-logic — verified by hand, not just asserted. The
  specificity path requires *both* a real target-fraction AND a near-zero
  other-fraction, so a gene that's essential everywhere still fails both
  paths. Don't re-loosen `SPECIFICITY_FIRST_MAX_OTHER_FRACTION` without
  checking against those four first.
- `dependency_scout` and `tests/re-agent_discovery` are two separate,
  unconnected pipelines with no shared types — the gate logic is now
  duplicated by hand between `src/dependency_scout/ranking.py` and
  `tests/re-agent_discovery/src/config.py`/`stage1_depmap.py`. If either
  changes, the other needs a matching edit; nothing enforces this.
