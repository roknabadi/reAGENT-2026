# Cross-complex validation targets

Input from **Sasha**, 2026-08-15. Recorded as research input, not as a decision.

The pitch says the pipeline is **general across diseases and target classes**,
and that Mediator/TF is one test case. This is the list that tests that claim.
Each target is a coactivator complex that is mechanistically analogous to
Mediator but is *not* Mediator — so if the agent can map these, generality is
demonstrated rather than asserted.

Sasha's framing, verbatim in substance: if the agent cannot correctly map how an
acidic/hydrophobic activation domain biases the conformational state of a large
α-solenoid coactivator, it cannot be trusted on ELK1–MED23 or ELF3–MED23 either.

---

## 1. SAGA complex — via Tra1 / TRRAP

**Relevance: highest.** **TF spectrum: very high.**

The massive Tra1 subunit is an α-solenoid sink that binds a wide spectrum of
activators — heavily acidic ADs (VP16, p53) and diverse mammalian activators
(c-Myc, E2F1).

**Why it is the closest analogue to MED23.** MED23 is a massive α-helical Tail
subunit; Tra1 is identically a massive α-solenoid built from HEAT/FAT repeats.
Just as ELK1 binding propagates an allosteric shift across MED23, AD binding at
Tra1 induces large-scale allostery between SAGA's core lobes. SAGA is, like
Mediator, a megadalton multi-subunit complex bridging distal sequence-specific
TFs to the pre-initiation complex to recruit TBP.

**Adversarial test.** Map co-folding of the Tra1 HEAT/FAT repeats with the Gcn4
or VP16 ADs. Determine whether AD engagement biases the known structural
flexibility between SAGA's Lobe A and Lobe B. This tests whether the agent can
link a fuzzy AD interaction at a Tail-like module to a downstream multi-subunit
structural output.

## 2. TFIID complex — via TAF subunits

**Relevance: very high.** **TF spectrum: high.**

TFIID engages a biochemically diverse TF set: TAF4 binds glutamine-rich Sp1;
TAF6/TAF9 bind acidic p53; TAF1 binds VP16.

**Why.** TFIID operates directly alongside Mediator at the promoter during PIC
assembly. Its TAFs form massive flexible lobes that undergo extreme
architectural reorganisation — over 100 Å — on activator binding. Where the
ELF3–MED23 pathway asks how AD contacts tune Pol II initiation, TFIID asks how
AD contacts at the TAF4/TAF12 lobes physically transition the complex to load
TBP.

**Adversarial test.** Map VP16 or p53 AD engagement with the TAF4/TAF12
histone-fold heterodimers, then link that binding event to the transition of
Lobe A from compact to extended. Tests multi-subunit allostery rather than
localised rigid poses.

## 3. CBP/p300 hub — via KIX and TAZ domains

**Relevance: moderate.** Highest for IDR binding; **lacks multi-subunit
allostery.** **TF spectrum: maximum.**

A universal coactivator with an interactome above 400 proteins, engaging almost
every structural class of TF (HIF-1, STATs, CREB, p53) through specialised
pockets. It mimics the fuzzy, multivalent, intrinsically disordered AD binding
central to the Mediator hypothesis — but as a single multidomain chain, not a
multi-subunit complex, so it has no quaternary allostery.

**Adversarial test.** Test co-occupancy of the MLL and c-Myb ADs on the KIX
domain. The agent must recognise the transient, mutually shifting nature of
these contacts rather than enforcing a uniquely correct atomic structure.

## 4. TFIIH — via the p62 subunit

**Relevance: high.**

Like MED23, TFIIH's p62 subunit is a direct physical target for intrinsically
disordered ADs such as p53. On binding it undergoes coordinated allosteric
rearrangements that activate enzymatic modules (CDK7 kinase). Tests AD
engagement mapped to structural allostery *inside* the PIC.

---

## What this means for the code

**The contract already generalises.** `MediatorLink.partner_gene` is a free
string, defaulted to `MED23` but not restricted to it. TRRAP, TAF4, CREBBP and
GTF2H1 all fit today with no schema change. `examples/coactivator_link_foxo4_kix.json`
is a working non-Mediator instance.

**The name does not.** `MediatorLink` hard-codes the test case into a type the
pitch calls general. Renaming to `CoactivatorLink` is cosmetic but would touch
Amir's adapter and `demo.json` field names, so it is a `DECISIONS.md` entry and
Andrey's call — **not** something to do mid-build. Recorded, not actioned.

**Ranking these as candidates needs dependency data we do not have.** These are
mostly yeast/in-vitro mechanistic systems, not cancer-cell dependencies. They
belong in the pipeline as *interface/structure* validation cases, entering at
the Mediator-connection stage with `dependency: null`, exactly the state the
schema now supports. They are not disease-target candidates and must never be
shortlisted as such.

## Paperclip retrieval status, 2026-08-15

Searched the PMC corpus for each target. Honest result:

| Target | Retrieved | Note |
|---|---|---|
| CBP/p300 KIX | **yes** | FOXO4-CR3 binds the MLL and c-Myb sites of CBP-KIX; two hydrophobic pockets. PMC12698673, KIX structure PDB 2AGH |
| p53 TAD (FOXO context) | yes | PMC12117093, structural plasticity, multiple binding modes |
| SAGA / Tra1 | **partial** | SAGA papers present; the Tra1 AD-binding cryo-EM work is not in the open-access PMC slice Paperclip indexes |
| TFIID / TAF4-TAF12 | not yet | needs a different query strategy or a source outside PMC |
| TFIIH / p62 | not yet | same |

Two of four are not retrievable from the corpus as queried. That is a real
limit worth stating rather than papering over: **the agent's reach is bounded by
what the corpus indexes**, and any claim about SAGA or TFIID would currently
rest on sources we have not read. Andrey's forthcoming Paperclip research
system prompt may change the query strategy; re-run then.
