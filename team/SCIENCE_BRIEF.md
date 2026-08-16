# Science brief — how the pipeline should work

**Author: Andrey. Shared 2026-08-16.** This is the specification the pipeline is
judged against, not background reading. Where code and this document disagree,
this document is right and the code is a bug.

Read with `PROJECT.md` (what we are building) and `docs/PIPELINE.md` (how the
stages are wired). `SOURCE_POLICY.md` still governs what may be cited.

---

## 1. The core idea

Some cancers become unusually dependent on specific transcription factors (TFs)
to maintain the gene-expression program that keeps them malignant. TFs are often
hard to drug directly because many of their regulatory regions are flexible and
lack classical small-molecule pockets. So instead of drugging the TF, ask:

1. Which TF is this cancer selectively dependent on?
2. Which coactivator does that TF use?
3. Is there a specific physical interface responsible for that interaction?
4. Does the **coactivator side** of that interface form a localized, chemically
   tractable site?
5. If yes, can we prioritize molecules that might perturb it?

```
Disease → selective TF dependency → TF/coactivator mechanism → physical interface
       → localized receptor-side site → compound prioritization → next experiment
```

**Every arrow is conditional.** The system stops or abstains when the evidence is
not sufficient.

## 2. Basic biology

TFs control which genes are on. They carry a **DNA-binding domain** and an
**activation domain**; activation domains are frequently **intrinsically
disordered regions (IDRs)** with no single stable fold, which is a large part of
why TFs resist conventional drugs.

TFs recruit **coactivators** that transmit their signal to RNA polymerase II.
**Mediator** is a major coactivator complex, and many TF activation domains
contact the **Mediator Tail**.

The mechanistic hypothesis: Mediator is not a passive bridge. TF binding may
change its conformational state and so modulate transcription. For drug
discovery this means:

> Do not try to inhibit Mediator globally. Perturb a **disease-relevant
> TF–Mediator interface** or conformational program.

MED23 is one worked example. **The pipeline must stay generic** — the relevant
coactivator could be MED23, MED25, POU2AF2, another subunit, or something else.

## 3. Why disease dependency comes first

Not "which TFs bind MED23?" but **which TF does the cancer actually need?** That
is what DepMap answers: knock out genes across hundreds of lines, and if losing
TF X selectively hurts disease Y, TF X may be a dependency.

The critical word is **selective**. A gene essential everywhere may be
biologically important and therapeutically unattractive. The ideal signal is a
strong dependency inside the disease and little dependency outside it.

## 4. Why subtype resolution matters

Cancer categories are heterogeneous. A TF may be essential in only 20–40 % of a
subtype, and a median across a broad lineage erases that. This happened with
small-cell lung cancer.

Two valid routes through the dependency gate:

- **Median path** — the context as a whole is dependent.
- **Specificity-first path** — a meaningful fraction inside is dependent while
  almost nothing outside is. Current thresholds: **≥ 10 % dependent inside,
  ≤ 5 % outside.**

This is what lets subtype-restricted master regulators (POU2F3, ASCL1) surface
when the median is weak.

**Keep these separate, do not collapse them into one field:**

| | Meaning |
|---|---|
| `dependency_flag` | the biological gate passed |
| q-value / significance | the strength of statistical support |

## 5. DepMap is not safety

DepMap compares cancer cell lines against cancer cell lines. A TF selective for
melanoma versus other **cancer lines** is not thereby safe to inhibit in normal
skin, brain or liver.

- May claim: *disease-selective cancer-cell dependency*.
- May **not** claim: *therapeutic window*, *normal-tissue safety*.

## 6. Disease evidence and mechanistic evidence are separate axes

A TF can have a strong disease dependency and no known MED23 interaction. Or
excellent MED23 structural evidence and no selective dependency.

- **Disease axis** — is the cancer selectively dependent on TF X?
- **Mechanism axis** — does TF X depend on coactivator Y?

Only the **intersection** is a strong therapeutic hypothesis. This is exactly why
ELK1 is a useful structural control without being a disease target.

## 7. The evidence ladder for a TF–coactivator interaction

| Rung | What it is | What it does not give you |
|---|---|---|
| **Association** | co-IP, same complex, genetic relationship | where the proteins touch |
| **Direct biochemical** | purified-protein binding, SPR/BLI, mutational disruption | the location of the contact |
| **Mapped** | a specific TF region or motif is known to matter | the partner-side surface |
| **Structural interface** | experimental structure or credible model, contacts on both proteins | tractability |
| **Localized receptor site** | partner-side contacts form a compact, plausibly targetable region | — |

**Automatic docking requires the last rung**, not merely "these proteins
interact."

## 8. Structure prediction

With no experimental structure, Boltz-2 can model the complex. **A predicted
complex is not evidence that the proteins bind.** The useful question is whether
independent predictions repeatedly place the same TF region on the same partner
surface — which is why we run multiple independent seeds.

## 9. Why an ensemble is necessary

Complex prediction is stochastic, especially for disordered activation domains.
Different seeds give different interfaces:

```
seed 1 → site A     seed 3 → site A     seed 5 → site A
seed 2 → site B     seed 4 → site C
```

Repeated recovery of A indicates **model self-consistency**. It still does not
prove biological binding, but it beats one arbitrary prediction.

## 10. Majority vote is not enough

The ELK1 control showed an ensemble can contain a locally correct prediction
while a larger cluster is wrong. So the structural layer **preserves multiple
interface hypotheses**, retaining per cluster: sample IDs, support fraction,
target-side segment, partner-side residues, compactness, interface confidence,
blockers.

Classify the ensemble:

| Status | Meaning | Licensed next action |
|---|---|---|
| **CONVERGED** | one localized hypothesis clears all predefined criteria | site generation may proceed |
| **AMBIGUOUS** | localized hypotheses exist, none with sufficient support | preserve them, sample more, **no automatic docking** |
| **REFUSED** | no defensible localized hypothesis | stop |

**Do not lower thresholds to rescue a known-answer control.**

## 11. Localization matters

A model can touch the correct pocket while also draping across a huge fraction of
the surface — common for IDRs. That is not enough for drug discovery:

> correct residues somewhere in the contact map ≠ correct localized interface.

## 12. ELK1–MED23 control, and its confound

Useful because an experimental structure exists and gives known interface
residues, and the contact-analysis machinery correctly recovers them.

**But** the biological interaction depends strongly on phosphorylation of **ELK1
Ser383**, and the Proto → Boltz path does not currently transmit that
modification into the Boltz input. The sequence-only ELK1 prediction is therefore
**biologically confounded**.

- Do **not** tune structural scoring until unphosphorylated ELK1 "passes".
- The control validates geometry and pipeline behaviour, **not** Boltz's ability
  to reproduce the native phospho-dependent interaction.

## 13. POU2F3–POU2AF2 as an orthogonal control

Disease-relevant in SCLC, has an experimental structure, no phosphorylation
problem, and the partner is **not MED23** — so it tests whether the architecture
is genuinely generic rather than secretly hardcoded around MED23.

## 14. MSAs in structure prediction

`use_msa=True` does **not** mean the current Proto path performs an MSA search.
The caller must supply the alignment.

Do not manufacture paired MSAs where row-wise correspondence between two proteins
is not biologically justified — a fake pairing creates fake co-evolutionary
signal. **Inputs to the structural model are part of the scientific result and
must be logged.**

## 15. From interface to druggable site

Partner-side contact residues are mapped onto the correct **free/unbound**
receptor structure. The site module then asks: are the residues present, are they
spatially localized, is the box reasonable, is the receptor identity correct, is
provenance known?

Yes → `SearchSite`. No → **abstain**. No defensible receptor-side site means no
automatic docking.

## 16. Experimental vs predicted sites

Two legitimate routes, provenance explicit either way:

- **Experimental** — published structure → known receptor-side residues → site.
- **Predicted** — Boltz ensemble → converged hypothesis → receptor-side residues
  → site.

A known ELK1–MED23 pocket can test the docking machinery. It does **not**
establish that a newly discovered TF uses that same pocket.

## 17. What docking tells us

Docking asks: given this receptor and this defined site, what poses are
geometrically plausible, and how does the scoring function rank them?

Docking does **not** prove binding, affinity, inhibition, selectivity, cellular
activity or efficacy.

A useful docking artifact carries: compound identity and provenance, score, pose,
site contacts, distance from the intended site, inside/outside-box status, clash
flags.

**The 12-drug Vina run is machinery validation, not evidence that those compounds
are MED23 drugs.**

## 18. What the agentic part actually means

```
evidence → claim → explicit decision rule → proceed / abstain
        → next experiment → new evidence → updated decision
```

The interesting behaviour is not generating many hypotheses. It is **changing
course**:

- *Subtype blind spot* — broad grouping missed real dependencies → gate logic
  changed.
- *Wrong accession* — a plausible-looking structural result was MED24, not MED23
  → result retracted, identity checks added.
- *Structural ensemble* — a majority cluster could suppress a useful minority
  hypothesis → preserve multiple hypotheses and sample more instead of pretending
  certainty.

## 19. What counts as a successful result

The pipeline does not have to reach a molecule.

| Situation | Valid outcome |
|---|---|
| Strong dependency, no known coactivator relationship | next experiment: test the interaction |
| Dependency + interaction, ambiguous structure | more structural samples / contact mapping |
| Clear interface, no localized site | another modality, or stop |
| Localized site | screen compounds |
| Plausible docked compounds | test binding and functional disruption experimentally |

**A correct abstention is a successful scientific result.**

## 20. Software architecture

The pipeline is a **typed evidence-state machine**. Each stage carries:

```
INPUT · EVIDENCE · PROVENANCE · CLAIM · CONFIDENCE · DECISION · NEXT ACTION
```

States: `DONE` · `AMBIGUOUS` · `ABSTAINED` · `BLOCKED`.

Core types: `DiseaseContext`, `DependencyVerdict`, `InteractionEvidence`,
`StructuralEnsemble`, `InterfaceHypothesis`, `InterfaceConsensus`,
`ReceptorSite`, `DockingResult`.

**The software should make scientifically invalid transitions difficult or
impossible.**

## 21. The pipeline in one view

```
User question
    ↓  Resolve disease/subtype
    ↓  DepMap TF scan → dependency gate → shortlist disease-selective TFs
    ↓  Search evidence for a coactivator relationship
Enough evidence?
    ├── no → abstain / recommend interaction experiment
    └── yes ↓
Mapped or structural interface?
    ├── no → generate/test structural hypothesis
    └── yes ↓  structural ensemble if needed
CONVERGED / AMBIGUOUS / REFUSED
    ├── ambiguous → sample more
    ├── refused   → stop
    └── converged ↓
Localized receptor-side site — defensible?
    ├── no → stop / alternative modality
    └── yes ↓
Compound screen → pose + score + geometry checks → next experimental test
```

## 22. The thesis

The therapeutic unit is not "Mediator." It is a **disease-selective
transcriptional dependency that relies on a particular coactivator interface**.

Can we systematically move from cancer dependency → mechanism → physical
interface → tractable site → chemistry, while preserving uncertainty and stopping
whenever the evidence does not justify the next step?

**One sentence:** we are building an agentic drug-discovery system that starts
from disease-specific transcription-factor dependencies, identifies the
coactivator mechanism that sustains them, validates the physical interface before
defining a druggable site, and only then prioritizes chemistry — while explicitly
preserving uncertainty and changing course when the evidence fails.
