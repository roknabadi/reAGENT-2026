# Dependency Scout → Proto Screen

1. **Discover:** ingest an official DepMap model table and Chronos gene-effect matrix.
2. **Gate:** reject weak, broad, or under-sampled dependencies before enrichment.
3. **Explain:** emit every score, failure, source version, and model count.
4. **Investigate:** recursively request literature, normal-cell, interface, and compound evidence with explicit stop conditions.
5. **Handoff:** serialize a `ProtoScreenSpec`; no prose-to-tool ambiguity.
6. **Execute in Proto:** retrieve/validate a public structure, score interface quality, then run Vina only when a reference ligand or explicit evidence-backed search box exists. Optionally use Boltz2 affinity as an orthogonal reranker.

## Scientific boundary

The output is a ranked hypothesis and computational compound shortlist, not a validated disease target, selective therapeutic, or binding result. DepMap cancer-cell-line selectivity is not normal-tissue safety. Docking score is not binding affinity. Missing evidence stays missing.

## Why Proto is central

Proto owns the structural execution contract and provenance. The discovery system must produce inputs that validate against native Proto models (`VinaDockingInput`, structure retrieval, interface scoring, and Boltz2 contracts). If inputs cannot be made auditable, the system abstains before docking.
