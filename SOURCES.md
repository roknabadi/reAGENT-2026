# Public sources and provenance

Add each artifact before it is used in the project.

| Artifact | URL | Version/commit | License/terms | Retrieved | Checksum |
|---|---|---|---|---|---|
| Proto Language | https://github.com/evo-design/proto-language | See `vendor/proto-language` | MIT | 2026-08-14 | See git commit |
| Proto Tools | https://github.com/evo-design/proto-tools | See submodule | Repository license metadata | 2026-08-14 | See git commit |
| PARADE | https://github.com/autosome-ru/parade | Pinned by Proto Tools | MIT; attribution required | On first model run | Verified by Proto Tools |
| BenchFlow | https://github.com/benchflow-ai/benchflow | PyPI 0.6.8 | Apache-2.0 | 2026-08-15 | PyPI-managed install |
| Paperclip | https://paperclip.gxl.ai | CLI 0.7.37 | Service/package terms | 2026-08-14 | N/A |

## Scientific evidence

| Claim used | Source | Identifier | Retrieved |
|---|---|---|---|
| ELK1–MED23 contact: cryo-EM structure, MED23-binding motif at ELK1 residues 374–384, pocket residues, Kd 81 nM, MED23 G382F loss of function | Monté et al. "Structural basis of human Mediator recruitment by the phosphorylated transcription factor Elk-1." *Nat. Commun.* (2025) | doi:10.1038/s41467-025-59014-8 · PMC12015215 · PDB 9F6Y (complex), 9F76 (apo MED23) | 2026-08-15 |

Used as the **positive control** for the Mediator-connection checkpoint in
`examples/mediator_link_elk1_med23.json`. It validates that the `MediatorLink`
contract classifies a genuinely mapped contact as `direct`. It is a calibration
case, not a target proposal, and no result may be carried from it.
