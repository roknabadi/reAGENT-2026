# Interface

One page, no server, no build step. Open `index.html` over any static server:

```bash
python -m http.server 8931 --directory ui
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
- **Structure** — PDB 9F6Y at 3.0 Å. MED23 backbone, the ELK1 motif 374–384,
  and the MED23 residues lining the pocket.

Built for people who read papers, not dashboards: gene symbols, effect sizes,
sample counts and sources are on the surface; nothing is summarised into a score
without the numbers behind it staying visible.

`vendor/three.module.min.js` and `vendor/OrbitControls.js` are Three.js r170, MIT.
The palette is validated for colour-vision deficiency in both light and dark;
every status also carries a word, so colour is never the only encoding.
