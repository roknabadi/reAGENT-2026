# Interface screenshots

Captured from a live local run against `ui/serve.py`, 1512×861, light theme.
The question was:

> RUNX2 has a mapped interacting region on MED23. Screen small molecules
> against that site on MED23 and show me the compounds.

RUNX2 is the candidate that opens the screen: its MED23 interaction is the one
mapped, non-calibration link in `examples/`, and the gate needs either that or
a converged ensemble.

| File | What it shows |
|---|---|
| `02-answer.png` | The model's account of the run, written from what the stages computed |
| `03-pipeline.png` | The nine stages, each with its own verdict — including the five that abstained |
| `04-reasoning.png` | Every model call, with the prompt sent and the reply returned |
| `05-structure-compounds.png` | MED23 with imatinib in the cavity, the compound strip, and the label saying whose cavity it is |
| `06-sources.png` | Papers retrieved during the run |

These are interface captures, not results. Nothing here demonstrates binding:
the Vina scores rank poses, the library is an approved-drug control set, and
the cavity is ELK1's calibration pocket rather than a site established for
RUNX2 — which is what the label in `05` says on screen.
