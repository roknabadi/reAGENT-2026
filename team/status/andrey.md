# Andrey — scientific lead / Mediator (@andreyf2022)

Newest on top. Format: ../README.md

## 2026-08-15 — Andrey
Did: round-01 evidence handoff. **Scope: this is a calibration exercise on the
general pipeline** (disease state → target discovery → quantitative ranking →
specificity / therapeutic window → druggable site or mechanism → structural
evaluation → screening → next experiment). Round 01 exercised the *evidence and
interface* stages using **Mediator/MED23 as one worked biological test case**,
chosen because ELK1–MED23 and ELF3–MED23 give us known positives to calibrate
against. It is not a claim that the pipeline is Mediator-specific, and the
architecture is unchanged.

**No hero target has been selected.** Gate 3 is untouched.

Literature triage: 16 primary sources, dependency-first ordering, ELK1/ELF3 held
out as calibration-only. Verified every citation at the source — all 21
retrievable PMCIDs resolve and titles match; re-read the load-bearing line anchors
for the ELK1 structure, the RUNX2 co-IP, and the ELF3 chalcone probe. Added
verified rows to `SOURCES.md`, including a "not retrievable as full text" table
for the seven primaries we are carrying on someone else's citation. Typed three
candidates into `MediatorLink` in `examples/`: RUNX2 → `direct`, CEBPB →
`indirect`, ETV1 → `predicted`. Fixed a helix mis-assignment in the ELK1 positive
control (V533/M537 are H28, not H30).

What the test case showed: MED23 presents one shared, chemically validated
TF-binding groove (concave face, HR2/HR3 interface, helices H19/H21/H28/H30)
rather than a diffuse surface. ELK1 and ELF3 converge on approximately the same
site; the ELF3 arm already has a small molecule at Ki 0.68 µM. The G382F
separation-of-function mutant shows interface-selective inhibition is achievable
without removing the subunit — which matters, because whole-protein Med23 loss is
tumour-*promoting* in Kras-G12D NSCLC. As a calibration result this says the
"druggable site or mechanism" stage can discriminate; it does not nominate a target.

Two findings worth reading before any hero-target discussion:

1. **RUNX2 is the only non-calibration candidate that passes the Mediator gate.**
   Its interacting region is mapped (Runt + PST domains), so `MediatorLink`
   derives `direct` and `ready_for_structural_modeling=True`. But those are large
   folded domains, not a short linear motif — poor small-molecule tractability —
   and the same axis is load-bearing in normal bone development (cleidocranial
   dysplasia). It passes on paper and should not proceed on the science.
2. **CEBPB is a negative control in disguise.** Round 01 called it `DIRECT`; the
   contract derives `indirect`, because no interacting region is mapped. It is
   exactly the case `PROJECT.md` says to reject out loud, and its sole source
   (Mo 2004) is one we have never read at the source.

### Open schema requirements — for Amir and Vraj

Round 01 hit three limits in `src/dependency_scout/models.py`. Recording them as
requirements only; **I am not proposing a redesign** — changing a shared type is
your call and a `DECISIONS.md` entry.

1. **Interface tractability has nowhere to live.** The contract records *whether*
   an interacting region is mapped, never whether it is *druggable*. A short
   linear motif in a groove and a large folded domain interface are both
   `interacting_region_mapped: true`, but only the first is a realistic
   small-molecule target. This is what makes RUNX2 pass a gate it should fail.
   `EnrichmentEvidence` has `interface_support` / `tractability_support`, but they
   are only reachable through `RankedCandidate`, which needs quantitative
   dependency numbers — so an interface finding cannot be recorded until the
   dependency stage has run, even though the two are independent.
2. **Calibration-only candidates cannot be marked as such.** ELK1 and ELF3 are
   deliberately excluded from ranking — they define what a positive looks like.
   `Shortlist` has no way to say "this is a control, never a result"; right now it
   is convention and prose in `SOURCES.md`.
3. **Quantitative dependency evidence and interface evidence are entangled.**
   `RankedCandidate` requires `DependencyEvidence`, so there is no way to carry a
   verified interface finding for a candidate whose dependency has not yet been
   quantified. Round 01 produced exactly that: real interface evidence, no DepMap
   numbers.

Next: retrieve Mo 2004, Asada 2002 and Hwang 2023 in full text; run the MBM motif
scan over real TF activation-domain sequences (UniProt FASTA — Paperclip's protein
VFS exposes no raw sequence).

Blocked: need Kevin for quantitative DepMap ranking on the round-01 candidates.
Round-01 dependency grades are my qualitative A–D calibration, **not** Chronos
scores, so no candidate can be built as a `RankedCandidate` yet —
`DependencyEvidence` requires seven numeric fields we do not have. Quantitative
ranking is required before hero-target selection, so gates 1 and 3 cannot be
signed and nothing is cleared for structural evaluation or screening.
