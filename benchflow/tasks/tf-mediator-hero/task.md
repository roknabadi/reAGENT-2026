---
schema_version: "1.3"
task:
  name: reagent/tf-mediator-hero
  description: Select one public-data-supported disease–TF–Mediator hypothesis
metadata:
  author_name: re:AGENT 2026 team
  difficulty: hard
  category: research
  task_type: [research, verification, data-analysis]
  modality: [web, json]
  interface: [terminal]
  tags: [biology, transcription, mediator, drug-discovery, provenance]
agent:
  timeout_sec: 1800
verifier:
  timeout_sec: 180
sandbox:
  cpus: 2
  memory_mb: 4096
  storage_mb: 10240
  allow_internet: true
---

## Prompt

Act as a cautious computational biology research agent. Using only public
sources, identify one disease or cell state with a selective dependency on a
transcription factor, connect that TF to a known or plausible Mediator-subunit
interaction, and assess whether the interface can support a structure-based
drug-discovery hypothesis.

ELK1–MED23 and ELF3–MED23 may be used as positive controls, but do not select
either automatically. Compare alternatives and nominate one strongest hero
disease–TF–Mediator hypothesis. Abstain from docking when no public structure,
validated interface, reference ligand, or defensible search region exists.

Write `/root/hero_hypothesis.json` with this shape:

```json
{
  "schema_version": "1.0",
  "hero": {
    "disease_context": "...",
    "transcription_factor": "...",
    "mediator_subunit": "...",
    "hypothesis": "...",
    "confidence": "low|medium|high"
  },
  "selection": {
    "dependency_strength": {"value": 0.0, "definition": "...", "source_url": "https://..."},
    "disease_specificity": {"value": 0.0, "definition": "...", "source_url": "https://..."},
    "normal_cell_proxy": {"status": "supported|mixed|missing", "notes": "...", "source_url": "https://..."},
    "evidence_quality": 0.0,
    "structural_tractability": 0.0
  },
  "evidence": [
    {"claim": "...", "source_url": "https://...", "source_type": "primary|database|review", "interpretation": "observed|computed|predicted|inference", "supports": true}
  ],
  "alternatives_rejected": [
    {"candidate": "...", "reason": "..."}
  ],
  "structure": {
    "status": "experimental|predicted|missing",
    "public_id": "PDB/UniProt/model identifier or null",
    "interface_residues": {},
    "caveats": ["..."]
  },
  "drug_discovery": {
    "status": "screened|ready|blocked",
    "search_region_basis": "...",
    "compounds": [],
    "blockers": []
  },
  "limitations": ["..."]
}
```

Use numeric scores only when their definition and source are explicit. Include
at least three public evidence records and one rejected alternative. Do not use
private data or claim binding, safety, efficacy, or experimental validation
from computational results.
