# Interface

One page, no server, no build step. Open `index.html` over any static server:

```bash
python ui/serve.py       # live: real Paperclip searches stream in
python -m http.server 8931 --directory ui   # static: recorded run, labelled as such
```

`data.json` is committed, so the interface works without the 382 MB DepMap
download. Regenerate it after a new run:

```bash
python scripts/build_ui_data.py
```

Three views:

- **Landscape** — every TF in one context, placed by the two numbers the gate
  tests, with the thresholds drawn as a boundary. The passing region is shaded;
  in Lung it is empty. Scroll to zoom, drag to pan.
- **Candidates** — real dependency numbers joined to typed interface evidence.
  Every claim carries its support type and its citation.
- **Structure** — follows the selected candidate, in three states, and never
  borrows one candidate's coordinates for another:

  1. **Solved complex.** ELK1–MED23 (PDB 9F6Y, 3.0 Å cryo-EM) and
     POU2F3–POU2AF2 (PDB 9PFP, 1.7 Å X-ray). Backbone, the mapped motif, and
     the residues lining the pocket. Opens on POU2F3 rather than ELK1, which is
     the calibration control and already published.
  2. **Predicted monomer.** For a target with no solved complex, the AlphaFold
     DB model coloured by per-residue pLDDT. This is the target *alone* — the
     interface is not modelled. The confidence bands are the argument: RUNX2 is
     30 % ordered, FOXO4 17 %, ZNF217 19 %. Transactivation domains are mostly
     disordered, which is why so few of these contacts have a solved complex
     and why a folded pocket is rare.
  3. **Nothing to model.** A candidate with no contact partner at all says so.

  A predicted model is a hypothesis, not a contact, and the panel says that on
  every prediction.

## Flow

```text
question ─▶ literature ─▶ target discovery ─▶ ranking & gates ─▶ specificity
                                                                     │
                              ┌──────────────────────────────────────┘
                              ▼
                        druggable site ─▶ structural evaluation ─▶ screening
                                                                     │
                                                            next experiment
```

Each stage reports `done`, `blocked`, `abstained` or `pending` on its own and
never on another stage's behalf. Selecting a candidate in **Candidates** re-keys
**Structure** to that candidate's `gene|partner` pair; the pair either has
coordinates in `data.json` or it does not, and both outcomes render.

Built for people who read papers, not dashboards: gene symbols, effect sizes,
sample counts and sources are on the surface; nothing is summarised into a score
without the numbers behind it staying visible.

`data.json` carries `structures` (experimental complexes, keyed `GENE|PARTNER`)
and `predicted` (AlphaFold monomers, keyed by gene). Regenerating fetches
UniProt accessions and AlphaFold models over the network; anything unreachable
is skipped with a warning rather than faked.

`vendor/three.module.min.js` and `vendor/OrbitControls.js` are Three.js r170, MIT.
The palette is validated for colour-vision deficiency in both light and dark;
every status also carries a word, so colour is never the only encoding.
