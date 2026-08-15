# Human-in-the-loop checkpoints

Gates on the pipeline in `PROJECT.md`. An agent may propose a candidate and fill
in the evidence, but only the named human flips a gate to `PASSED`. Do not
proceed past an `OPEN` gate — abstain and post a blocker instead.

Status: `OPEN` · `PASSED` · `FAILED` (say what to do differently).

---

## 1. TF shortlist — OPEN

**Signs off:** Kevin + Andrey
**Test:** hits are real disease dependencies, not just overexpression and not
broadly essential TFs.

- Evidence:
- Decision:
- Signed:

## 2. Mediator connection — OPEN

**Signs off:** Andrey
**Test:** the TF–Mediator relationship is genuinely supported and biologically
plausible.

- Evidence: round-01 triage, 2026-08-15. **Calibration only — no hero target is
  proposed here, and quantitative DepMap ranking has not run.** Round 01 used
  Mediator/MED23 as one worked test case for the evidence and interface stages of
  the general pipeline. Typed as `MediatorLink` in `examples/`, sources in
  `SOURCES.md`, full report in `outputs/research/round-01/` (not in Git).
  Derived involvement, not asserted:

  | Candidate | `involvement` | Region mapped | Why |
  |---|---|---|---|
  | ELK1 (positive control) | `direct` | yes — MBM 374–384 | 3.0 Å cryo-EM, Kd 42–81 nM, G382F separation-of-function |
  | RUNX2 | `direct` | yes — Runt + PST domains | endogenous co-IP + GST pull-down (doi:10.1038/ncomms11149) |
  | CEBPB | `indirect` | **no** | whole-protein Mediator exchange only; sole source never read at the source |
  | ETV1 | `predicted` | no | ETS family + KIT-MAPK-regulated stability; **no MED23 experiment exists** |
  | ERG, SPI1, EWSR1::FLI1, PAX8, SOX10, IRF4 | `unknown` | no | family prior or nothing at all |

  Shared-groove finding: ELK1 and ELF3 converge on approximately the same site on
  the MED23 concave face (HR2/HR3 interface; helices H19, H21, H28, H30). The
  ELF3 arm of that groove is already chemically validated at Ki 0.68 ± 0.08 µM
  (doi:10.7554/eLife.97051.3.sa4), and MED23 G382F abolishes ELK1 binding while
  still supporting E1A CR3 activation — so interface-selective inhibition is
  achievable without removing the subunit. That last point matters because
  whole-protein Med23 loss is tumour-*promoting* in Kras-G12D NSCLC
  (doi:10.1038/s41416-023-02556-9): inhibit the interface, do not degrade MED23.

  Three caveats before this gate is signed:
  1. RUNX2 passes the gate mechanically but its mapped region is two folded
     domains, not a short linear motif — poor small-molecule tractability, and
     the same axis is required in normal osteogenesis. The contract has no field
     for "mapped but not tractable"; recorded as an open schema requirement for
     Amir and Vraj in `status/andrey.md`.
  2. Nothing here is `ready_for_structural_modeling` except ELK1 (calibration)
     and RUNX2 (not recommended). No candidate is cleared for Proto.
  3. This gate is about whether a TF–Mediator relationship is genuinely supported.
     Signing it would not select a target: gate 1 needs quantitative DepMap
     ranking that has not run, and gate 3 is untouched.
- Decision:
- Signed:

## 3. Hero target selection — OPEN

**Signs off:** whole team, Andrey leads
**Test:** the agent ranks candidates; humans pick the one
indication–TF–Mediator pair to pursue.

- Evidence:
- Decision:
- Signed:

## 4. Structural model review — OPEN

**Signs off:** Andrey + Vraj
**Test:** the predicted interface is credible before docking.

- Evidence:
- Decision:
- Signed:

## 5. Virtual-screen hit review — OPEN

**Signs off:** Andrey + Vraj
**Test:** top poses and chemistry inspected by hand. Docking score is not
binding affinity.

- Evidence:
- Decision:
- Signed:

---

Record the reasoning behind a `PASSED` gate in `DECISIONS.md` too — the gate
here is the receipt, the decision log is the argument.
