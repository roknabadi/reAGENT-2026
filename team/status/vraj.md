# Vraj — software / data integration / virtual screening (@vraj00222)

Newest on top. Format: ../README.md

## 2026-08-15 (2) — Vraj
Did: closed Andrey's three round-01 schema gaps on `fix/schema-gaps-round01`. They
  were one root cause: `RankedCandidate` welded `DependencyEvidence` to
  `MediatorLink`, so 7 DepMap numbers were required before verified interface
  evidence could be recorded. `dependency` is now optional, `gene` carries the name
  without it, `awaiting_dependency_data` separates "stage has not run" from
  "failed". `InterfaceTractability` on `MediatorLink` (short_linear_motif /
  folded_domain / unknown) is the RUNX2-vs-ELK1 discriminator — RUNX2 keeps
  ready_for_structural_modeling=True and now states its folded-domain problem in
  `screening_concerns`. `calibration_only` keeps ELK1/ELF3 out of every shortlist.
  Branch also merges Andrey's handoff + Amir's agent-workflow; 79 tests green.
  Also made 6 BenchFlow/Proto tests skip instead of fail on a bare checkout — they
  failed on every machine that had not run setup.sh, so nobody but Amir could tell
  a regression from a missing install.
Next: adapter between `dependency_scout` and `reagent_workflow` models — they mean
  the same thing and share no types, so there is no single end-to-end object path.
Blocked: Kevin for quantitative DepMap numbers. Until then every round-01 candidate
  reads `awaiting quantitative dependency data` and gates 1 and 3 stay OPEN.

## 2026-08-15 — Vraj
Did: stage-1 output contract on branch `docs/project-brief`. `Claim` forces every
  statement to carry a SupportType (direct_experimental / genetic_functional /
  computational_prediction / inference) and >=1 citation; an enrichment score with
  no claim is now a validation error. `MediatorLink` types the TF-MED23 contact and
  *derives* involvement from the claims, so direct/indirect/predicted/unknown cannot
  drift from the evidence. `dependency-scout shortlist ... --markdown` emits Andrey's
  A/B/C/D table and refuses to shortlist a gate-failing candidate.
  Calibrated against ELK1-MED23 (PDB 9F6Y, doi:10.1038/s41467-025-59014-8):
  classifies `direct`, pinned by a test. Also fixed cli.py importing proto_bridge at
  module level, which made every command fail without Proto installed.
Next: attach real Paperclip claims to DepMap candidates so involvement stops
  reading `unknown`; then a negative control that must come out `indirect`.
Blocked: none. Need Kevin/Andrey at checkpoint 1 once real candidates exist.
