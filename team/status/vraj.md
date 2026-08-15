# Vraj — software / data integration / virtual screening (@vraj00222)

Newest on top. Format: ../README.md

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
