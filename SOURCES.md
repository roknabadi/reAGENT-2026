# Public sources and provenance

Add each artifact before it is used in the project.

| Artifact | URL | Version/commit | License/terms | Retrieved | Checksum |
|---|---|---|---|---|---|
| Proto Language | https://github.com/evo-design/proto-language | See `vendor/proto-language` | MIT | 2026-08-14 | See git commit |
| Proto Tools | https://github.com/evo-design/proto-tools | See submodule | Repository license metadata | 2026-08-14 | See git commit |
| PARADE | https://github.com/autosome-ru/parade | Pinned by Proto Tools | MIT; attribution required | On first model run | Verified by Proto Tools |
| BenchFlow | https://github.com/benchflow-ai/benchflow | PyPI 0.6.8 | Apache-2.0 | 2026-08-15 | PyPI-managed install |
| Paperclip | https://paperclip.gxl.ai | CLI 0.7.37 | Service/package terms | 2026-08-14 | N/A |
| DepMap CRISPRGeneEffect (Chronos) | https://depmap.org/portal/data_page/ | Public 24Q2 | CC BY 4.0 | 2026-08-15 | 382 MB, 1100 lines × 18443 genes, local only |
| DepMap Model metadata | https://depmap.org/portal/data_page/ | Public 24Q2 | CC BY 4.0 | 2026-08-15 | 1921 models, OncotreeLineage/Subtype |
| Lambert et al. human TF catalogue | http://humantfs.ccbr.utoronto.ca | v1.01 | Academic use, cite Cell 2018;172:650 | 2026-08-15 | 1639 TFs of 2765 rows |
| RCSB PDB structure files | https://files.rcsb.org/download/ |  9F6Y, 9F76, 9PFP | CC0 1.0 | 2026-08-15 | mmCIF, CA traces only in `ui/data.json` |
| RCSB PDB 9F76 mmCIF, vendored | https://files.rcsb.org/download/9F76.cif | Deposited entry | CC0 1.0 | 2026-08-15 | 1.06 MB copied to `ui/med23.cif` so the viewer can assign secondary structure; the residue trace in `med23.json` cannot |
| 3Dmol.js molecular viewer, vendored | https://3dmol.csb.pitt.edu | 2.4.2 | BSD-3-Clause | 2026-08-16 | `ui/vendor/3Dmol-min.js`, 502 KB. Cartoon, surface and per-residue styling; suggested by Amir |
| UniProtKB feature table (MED23 Q9ULK4 regions) | https://rest.uniprot.org/uniprotkb/Q9ULK4.json | Release at retrieval | CC BY 4.0 | 2026-08-16 | 2 features, both in the disordered C-terminal tail 1343–1368, which 9F76 does not resolve |
| UniProtKB (gene symbol → reviewed human accession) | https://rest.uniprot.org/uniprotkb/search | Release at retrieval | CC BY 4.0 | 2026-08-15 | 11 accessions, reviewed + organism 9606 |
| AlphaFold DB predicted monomers | https://alphafold.ebi.ac.uk/api/prediction/ | Model v6 | CC BY 4.0 | 2026-08-15 | 11 TFs, CA trace + per-residue pLDDT |
| UniProtKB canonical sequences (ELK1 P19419, MED23 Q9ULK4) | https://rest.uniprot.org/uniprotkb/ | Fetched live per run via `proto_tools` UniProt tool | CC BY 4.0 | 2026-08-15 | ELK1 428 aa, MED23 1368 aa; not vendored, re-fetched each run. **Corrected 2026-08-15**: this row read `MED23 O75448 … 989 aa`, which is MED24. See `team/FINDINGS_ELK1_CONTROL.md` |

## Scientific evidence

Constants transcribed into code from these rows are re-checked against the
primary record at run time where that is possible. `scripts/calibrate_structure.py`
asserts ELK1 P19419 residues 374–384 are literally `PSIHFWSTLSP`, and that **both**
accessions carry the gene symbol they are named for and appear in UniProt's own
cross-references for 9F6Y; it refuses to run if any of that has moved. The claims
it does **not** independently verify — that 9F6Y is the MED23 complex, its
resolution, the interface residues, and the Kd — are second-hand from the row
below and are not used as model inputs.

The partner half of that check was added on 2026-08-15, after `MED23 = "O75448"`
(MED24) reached a GPU run. Verifying a transcribed *value* is not the same as
verifying that the accession names the *protein* meant; MED24 passes any check
that only asks whether the entry is a real, reviewed, Mediator-complex subunit.

**Verified at source 2026-08-15** via Paperclip against PMC12015215: the motif
and its HR2/HR3 binding site (L23), the MED23 interface residues surrounding
F378-Elk-1 (L23 — note these are MED23 residues; MED23's own 383 is distinct
from the ELK1 S383 phosphosite), PDB 9F6Y as the complex at 3.0 Å and 9F76 as
apo MED23 (Table 1, L19), and the SPR Kd of 81 nM (L614, technique at L342).
Every transcribed value held.

| Claim used | Source | Identifier | Retrieved |
|---|---|---|---|
| ELK1–MED23 contact: cryo-EM structure, MED23-binding motif at ELK1 residues 374–384, pocket residues, Kd 81 nM, MED23 G382F loss of function | Monté et al. "Structural basis of human Mediator recruitment by the phosphorylated transcription factor Elk-1." *Nat. Commun.* (2025) | doi:10.1038/s41467-025-59014-8 · PMC12015215 · PDB 9F6Y (complex), 9F76 (apo MED23) | 2026-08-15 |
| POU2F3–OCA-T1/T2 contact: LEELE motif residues 188–192, LEELE alanine mutants abolish binding, conserved shallow groove called a potential ligandable surface, POU2F3 essential in SCLC-P and dispensable in other SCLC subtypes | Alpsoy et al. "Structural basis of DNA-dependent coactivator recruitment by the tuft cell master regulator POU2F3." (2025) | PMC12755459 · PDB 9PFP (OCA-T1), 9PFN/9PFO (OCA-T2) | 2026-08-15 |
| FOXO4-CR3 binds two hydrophobic pockets in the CBP KIX domain at the MLL and c-Myb sites | Heo, Ryu & Park, "Recognition of two hydrophobic pockets in the KIX domain of CBP by FOXO4 transactivation domain" (2025) | PMC12698673 · KIX reference structure PDB 2AGH | 2026-08-15 |
| ELK1–MED23 phospho-dependence is not S383-specific: Kd 75 nM for ELK1 T368P/S389P, comparable to 81 nM for S383P alone | Monté et al. (2025), verified at source | doi:10.1038/s41467-025-59014-8 · PMC12015215 L24 | 2026-08-15 |
| ELF3–MED23 chemical validation: chalcone compound 10 inhibits the ELF3–MED23 interaction at Ki 0.68 ± 0.08 µM; activity in HER2-overexpressing gastric cancer including trastuzumab-resistant models | "Synthesis and biological assessment of chalcone and pyrazoline derivatives as novel inhibitor for ELF3-MED23 interaction" (2024) | doi:10.7554/eLife.97051.3.sa4 · PMC11623927 L105 | 2026-08-15 |
| ELF3–MED23 assay format: FP and split-luciferase both use MED23 fragment 391–582; ELF3 TAD residues 129–145, essential residues S137–E144; FP Kd 10.2 ± 0.82 nM | same as above | doi:10.7554/eLife.97051.3.sa4 · PMC11623927 L80, L81, L125, L129 | 2026-08-15 |
| RUNX2–MED23 contact: endogenous co-IP in differentiated MC3T3E1 osteoblasts plus GST pull-down; interaction mediated via the RUNX2 Runt and PST domains | Liu et al. "Mediator MED23 cooperates with RUNX2 to drive osteoblast differentiation and bone development." *Nat. Commun.* 7, 11149 (2016) | doi:10.1038/ncomms11149 · PMC4821994 L23 | 2026-08-15 |
| ETV1 dependency in GIST: required for growth of both imatinib-sensitive and imatinib-resistant lines; activated KIT prolongs ETV1 protein stability, imatinib and PD325901 destabilize it | Chi et al. "ETV1 is a lineage-specific survival factor in GIST and cooperates with KIT in oncogenesis." *Nature* (2010) | doi:10.1038/nature09409 · PMC2955195 L1, L24 | 2026-08-15 |
| CEBPB/STAT3 as necessary-and-sufficient master regulators of mesenchymal transformation in glioma | Carro et al. "The transcriptional network for mesenchymal transformation of brain tumors." *Nature* (2010) | doi:10.1038/nature08712 · PMC4011561 | 2026-08-15 |
| Whole-protein Med23 loss is tumour-PROMOTING in Kras-G12D NSCLC: more and larger tumours, reduced CD4+/CD8+ infiltration, increased MDSCs and Tregs, compromised MHC-I via B2m downregulation | Fu et al. "Med23 deficiency reprograms the tumor microenvironment to promote lung tumorigenesis." *Br. J. Cancer* (2024) | doi:10.1038/s41416-023-02556-9 · PMC10912217 L20 | 2026-08-15 |
| TF selectivity context: in pan-cancer CRISPR across 930 cell lines and 27 cancer types ranked by normLRT, 22 of the top 50 most selective dependency genes encode transcription factors | "Short circuit: Transcription factor addiction as a growing vulnerability in cancer" (2024) | doi:10.1016/j.sbi.2024.102948 · PMC11614577 L24, L25 | 2026-08-15 |
| MED23 is absent from current high-throughput activation-domain × coactivator affinity maps (which cover P300, KIX, TAZ1/TAZ2, BRD4) | "High-throughput affinity measurements of direct interactions between activation domains and co-activators" (2024) | PMC11370418 | 2026-08-15 |

The ELK1 row is the **positive control** for the Mediator-connection checkpoint,
in `examples/mediator_link_elk1_med23.json`. It validates that the `MediatorLink`
contract classifies a genuinely mapped contact as `direct`. It is a calibration
case, not a target proposal, and no result may be carried from it.

### Evidence-quality caveats on the rows above

- The **EWSR1::FLI1 degron** work cited in round-01 triage
  (doi:10.1101/2024.10.27.620498, PMC11566046) is a **bioRxiv preprint**, not a
  peer-reviewed paper. It is not listed above because nothing in this handoff
  depends on it; note the status before it is used.
- The TF-addiction review (PMC11614577) **contradicts itself**: body text at L25
  maps SOX10→glioma and FOXA1→neuroblastoma, while the figure legend at L24 maps
  PAX8/FOXA1/IRF4/SOX10→ovarian/breast/lymphoid/skin. The figure legend is
  correct and is what the row above uses.
- `PMC11614577` and `PMC11623927` were cited in round-01 without journal
  identification. Journals and DOIs above were resolved from Paperclip metadata
  on 2026-08-15.

### Cited but NOT retrievable as full text

These are cited only through the bibliography of Monté et al. 2025
(doi:10.1038/s41467-025-59014-8). They have **not been read at the source**. Per
`SOURCE_POLICY.md` they are recorded here so the gap is visible; a claim resting
on one of them must not be treated as verified.

| Claim carried | Source | Identifier | Status |
|---|---|---|---|
| Ras induces Mediator complex exchange on C/EBPβ — sole basis for any CEBPB–MED23 link | Mo, Kowenz-Leutz, Xu & Leutz, *Mol. Cell* 13, 241–250 (2004) | PMID 14759370 | **Not retrieved.** Blocks any CEBPB commitment. |
| ELF3/ESX–MED23; external control of HER2 expression via a Ras-linked coactivator | Asada et al., *PNAS* 99, 12747–12752 (2002) | — | Not retrieved. ELF3 is a control, so lower urgency. |
| ELF3 TAD 129–145 / MED23 391–582 interaction mapping | Hwang et al. (2023), cited by PMC11623927 L80 | — | Not retrieved. Methods template for a CEBPB tiling experiment. |
| Transcription control by E1A and MAP kinase via Sur2/MED23 | Stevens et al., *Science* 296, 755–758 (2002) | — | Not retrieved. E1A CR3 specificity control. |
| Mammalian Srb/Mediator targeted by adenovirus E1A | Boyer et al., *Nature* 399, 276–279 (1999) | — | Not retrieved. |
| *sur-2* in *C. elegans* let-60 Ras signalling (original MED23 discovery) | Singh & Han, *Genes Dev.* 9, 2251–2265 (1995) | — | Not retrieved. |
| Germline *MED23* mutation links intellectual disability to dysregulated IEG expression | Hashimoto et al., *Science* 333 (2011) | — | Not retrieved. Safety-bar context. |
