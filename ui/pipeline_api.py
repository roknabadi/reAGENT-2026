"""The live run behind the interface: the question drives the pipeline.

Two rewrites are folded into this file, and both were about the same thing —
the interface reporting a conclusion the rest of the project would not have
reached.

The first version was a facade: one generic Paperclip search capped at six
results and a precomputed Lung landscape served for every question, so the
plot, the table and the structure were identical whatever you asked. Only the
sources rail responded, which is why the numbers looked instant and hardcoded.
They were.

The second computed its numbers honestly but applied its own gate — a four-way
AND that no other part of the project used — against a lineage resolved from a
hand-written list of eighteen regexes. That fails in a way a facade does not,
because the output is real and defensible-looking and still wrong: asking
about small cell lung cancer got you an answer about Lung, and pooling SCLC
with every other lung tumour pushes ASCL1, POU2F3, NEUROD1 and INSM1 below
threshold. Four master regulators, invisible, with no error anywhere.

So the decisions are made elsewhere now, by the modules that own them:

  resolve.resolve       free text -> a context the loaded data can answer
                        about, at lineage OR subtype granularity, or an
                        abstention
  verdict.scan_context  the canonical gate from stage1_depmap.py, applied to
                        that context
  verdict.shortlist     the three that clear it
  literature.gather     six evidence axes per candidate, on-target only

Nothing here decides whether a TF is a dependency, and no stage reports `done`
on the strength of a different stage's success.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

import pandas as pd
from pydantic import ValidationError

from dependency_scout.models import MediatorLink
from reagent_workflow import agent as A
from reagent_workflow import verdict as V
from reagent_workflow.chemistry import depict, parse_molblock
from reagent_workflow.interface import InterfaceConsensus, parse_mmcif
from reagent_workflow.literature import AXES, gather
from reagent_workflow.resolve import resolve, vocabulary
from reagent_workflow.site import build_search_site, receptor_residues

DOWNSTREAM = ("literature", "discovery", "ranking", "specificity", "site",
              "structure", "screening", "experiment")

ROOT = Path(__file__).resolve().parents[1]
SCREEN_FILE = ROOT / "runs" / "vina_smoke.json"
MAX_POSES_SHOWN = 3


class _Recorder:
    """Passes every event through, and keeps the record the answer is written from.

    The alternative was assembling a summary by hand at each call site, which
    drifts the moment a stage changes its wording — and an answer written from
    a drifted summary is an answer about a run that did not happen. Wrapping
    `emit` means the model reads exactly what the reader saw.
    """

    def __init__(self, emit: Callable[[str, dict], None]) -> None:
        self._emit = emit
        self.stages: dict[str, dict] = {}
        self.candidates: list[dict] = []
        self.highlights: list[dict] = []
        self.papers = 0

    def __call__(self, event: str, payload: dict) -> None:
        if event == "stage":
            self.stages[payload["id"]] = {k: payload.get(k)
                                          for k in ("state", "detail", "note")}
        elif event == "candidates":
            self.candidates = [{k: r.get(k) for k in
                                ("gene", "context", "median", "sel", "tfrac",
                                 "ofrac", "n", "q", "route")}
                               for r in payload.get("rows", [])]
        elif event == "highlight":
            self.highlights.append(
                {"gene": payload.get("gene"),
                 "predicted_partner_residues": payload.get("residues"),
                 "compounds": [{k: c.get(k) for k in ("name", "score", "residues")}
                               for c in payload.get("ligands", [])]})
        elif event == "paper":
            self.papers += 1
        self._emit(event, payload)

    def record(self) -> dict:
        return {"stages": self.stages, "shortlist": self.candidates,
                "papers_retrieved": self.papers,
                "partner_site_and_compounds": self.highlights}


def _screen_files(genes: list[str]) -> list[Path]:
    """Screens to consider, most specific first.

    A screen docked into a TF's predicted interface answers a different
    question from one docked into the curated ELK1 cavity, so the per-gene file
    is tried first and the calibration screen is the fallback. Which one is
    served is decided by whether its residues match the box that was actually
    built, not by which file happens to exist.
    """
    return [ROOT / "runs" / "screens" / f"{g.upper()}_MED23.json"
            for g in genes] + [SCREEN_FILE]


def _recorded_screen(site, genes: list[str] | None = None):
    """The screen on file, but only if it was run against *this* box.

    A docking record is only about the site it was docked into. Serving one
    keyed to different residues would put poses on screen that were computed
    somewhere else on the protein, which is the same class of error as
    borrowing another candidate's coordinates — so the residues are compared
    and a mismatch is skipped rather than shown.
    """
    if site is None or not site.defensible:
        return None
    for path in _screen_files(genes or []):
        got = _read_screen(path, site)
        if got:
            return got
    return None


def _read_screen(path: Path, site):
    if not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if set(rec.get("site", {}).get("resolved_residues", [])) != set(site.residues):
        return None

    summary = dict(rec.get("summary") or {})
    if summary.get("best") is None:
        return None
    summary["seed"] = rec.get("config", {}).get("seed")
    summary["file"] = str(path.relative_to(ROOT))
    summary["basis"] = rec.get("site", {}).get("basis", "")

    # Only poses that landed in the pocket. A good score with no contact and a
    # large offset from the box centre is Vina docking somewhere else, and
    # drawing it on the cavity would show a hit that is not one.
    clean = [r for r in rec.get("results", [])
             if r.get("vina_score") is not None
             and not (r.get("wrong_site") or r.get("clash") or r.get("outside_box"))
             and r.get("geometry", {}).get("centroid")]
    clean.sort(key=lambda r: r["vina_score"])
    poses = [{"name": r["compound"], "score": r["vina_score"],
              "centroid": r["geometry"]["centroid"],
              # Which MED23 residues this compound actually touches, at 4.5 A.
              # This is the answer to "what would we work on and where": a
              # score alone says a pose exists somewhere, the residue list says
              # what it sits against, and only the second is a starting point
              # for a medicinal chemist.
              "residues": r["geometry"].get("pocket_contacts", []),
              "contacts": r["geometry"].get("n_pocket_contacts", 0),
              "closest": r["geometry"].get("closest_pocket_approach"),
              "smiles": r.get("smiles", ""),
              "provenance": r.get("provenance", ""),
              # The molecule itself, twice: the heavy-atom skeleton of the pose
              # that was scored, in the receptor's frame, and the 2D structure
              # a chemist reads. A score with no molecule beside it is a number
              # about something the reader cannot see.
              "pose": parse_molblock(r.get("pose_sdf", "")),
              # The molblock as it came back, for the viewer that can read one
              # directly. Four kilobytes a compound, and it saves rebuilding a
              # molecule the screen already serialized.
              "sdf": r.get("pose_sdf", ""),
              "svg": depict(r.get("smiles", ""))}
             for r in clean[:MAX_POSES_SHOWN]]
    return (summary, poses) if poses else None


def _recorded_interface(gene: str):
    """The predicted TF–partner interface for `gene`, if an ensemble produced one.

    Written by `scripts/predict_med23_interface.py`, which co-folds the pair on
    GPU and keeps the consensus rather than any single sample. Returns
    `(InterfaceConsensus, record)` or None. A consensus the module rejected is
    returned as-is: `receptor_residues` refuses a blocked consensus, and the
    interface reports the refusal instead of quietly falling back.
    """
    path = ROOT / "runs" / "interfaces" / f"{gene.upper()}_MED23" / "consensus.json"
    if not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
        return InterfaceConsensus.model_validate(rec["consensus"]), rec
    except (OSError, json.JSONDecodeError, KeyError, ValidationError):
        return None


PREDICT_SAMPLES = 3          # the consensus floor; five is the batch convention


def _predict_interface(gene: str, emit) -> tuple[tuple | None, str]:
    """Run the real predictor for `gene`, live, and return what it wrote.

    This is a bridge, not a second implementation: `scripts/predict_med23_interface`
    owns fetching both sequences, verifying the accessions, building per-chain
    MSAs, dispatching independent Boltz-2 seeds to Modal and scoring the
    ensemble. Calling it from here means the interface triggers exactly what the
    command line runs — the one thing the interface could not do before was
    cause the file it was already reading to exist.

    Three seeds rather than five: `ConsensusConfig.min_ensemble_samples` is 3,
    so that is the fewest that can demonstrate agreement at all, and every
    additional seed is another GPU dispatch a person is waiting on.

    Failure is reported, never smoothed over. A refused accession, a dead
    dispatch and an ensemble that ran and disagreed are three different
    outcomes, and each keeps the structure stage abstaining rather than
    producing residues nobody can defend. The reason is returned as well as
    emitted, because the stage this returns into emits its own verdict
    afterwards and would otherwise overwrite the only explanation on screen.
    """
    import sys
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        from predict_med23_interface import accession_for, dispatch
    except Exception as e:                                   # noqa: BLE001
        note = f"the predictor could not be loaded: {type(e).__name__}: {str(e)[:160]}"
        emit("stage", {"id": "structure", "state": "blocked",
                       "detail": f"the predictor could not be loaded: {type(e).__name__}",
                       "note": note})
        return None, note

    accession, why = accession_for(gene)
    if not accession:
        note = (why + ". A gene symbol does not identify a protein, and the wrong "
                "accession folds the wrong one.")
        emit("stage", {"id": "structure", "state": "abstained",
                       "detail": f"no accession for {gene}", "note": note})
        return None, note

    emit("stage", {
        "id": "structure", "state": "running",
        "detail": f"co-folding {gene} with MED23, {PREDICT_SAMPLES} seeds on Modal",
        "note": f"{gene} is {accession} (UniProt, reviewed, human). Fetching "
                "sequences, building per-chain MSAs, then one GPU dispatch per "
                "seed. This is minutes, not seconds."})

    def log(msg: str) -> None:
        emit("stage", {"id": "structure", "state": "running",
                       "detail": f"co-folding {gene} with MED23 "
                                 f"({PREDICT_SAMPLES} seeds on Modal)",
                       "note": str(msg)[:300]})

    try:
        dispatch(gene, accession, PREDICT_SAMPLES, log=log)
    except Exception as e:                                   # noqa: BLE001
        note = (f"{type(e).__name__}: {str(e)[:200]} — the dispatch for {gene} "
                "failed, nothing was written, and the site below falls back to "
                "the curated cavity.")
        emit("stage", {"id": "structure", "state": "blocked",
                       "detail": f"prediction failed: {type(e).__name__}",
                       "note": note})
        return None, note
    got = _recorded_interface(gene)
    return got, ("" if got is not None else
                 f"the {gene} dispatch returned but wrote no consensus file")


def _model_table(model_path) -> pd.DataFrame:
    return pd.read_csv(model_path, usecols=["ModelID", "OncotreeLineage",
                                            "OncotreeSubtype"])


def _named_genes(question: str, tf_path, partner: str) -> list[str]:
    """Gene symbols the question names, from the catalogues this project uses.

    Uppercase tokens only. Symbols are conventionally written that way, and the
    TF catalogue contains MAX, REST, JUN and FOS — matching case-insensitively
    would turn "the rest of the signal" into a gene question. A user who types
    a symbol in lower case gets the ordinary no-context refusal, which is a
    better failure than answering about the wrong protein.
    """
    tokens = set(re.findall(r"\b[A-Z][A-Z0-9]{2,}\b", question or ""))
    if not tokens:
        return []
    from dependency_scout.depmap import load_tf_universe
    try:
        universe = load_tf_universe(str(tf_path))
    except Exception:
        universe = set()
    hits = sorted(tokens & (universe | {partner.upper()}))
    # The partner alone is a legitimate question ("what binds MED23?"), but it
    # is the receptor, not a candidate, so it never becomes a highlight key.
    return [h for h in hits if h != partner.upper()] or (
        [partner.upper()] if partner.upper() in tokens else [])


def _partner_first(question: str, named: list[str], cfg, emit, trace, why: str,
                   decided_by: str, interface_evidence: dict, free_receptor,
                   predict: str | None = None, require_site: bool = False) -> None:
    """Answer a gene question that names no disease.

    Everything that needs a cohort abstains and says why; everything that needs
    only the gene and the partner runs. The result is the honest half of the
    pipeline rather than a blocked page — and the half that runs is the half a
    question phrased this way was actually asking about.
    """
    emit("stage", {
        "id": "question", "state": "done", "detail": question,
        "note": (f"no disease context ({why or 'none named'}; decided by "
                 f"{decided_by}), so this is answered as an interface question "
                 f"about {', '.join(named)} and {cfg.partner_gene}. Dependency "
                 "ranking needs a cohort and is abstained below.")})
    for sid in ("discovery", "ranking", "specificity"):
        emit("stage", {
            "id": sid, "state": "abstained",
            "detail": "no disease context to screen against",
            "note": "A dependency is a statement about a cohort of models. "
                    "Without one there is nothing to rank, and ranking on the "
                    "whole of DepMap would answer a question nobody asked."})

    # Three axes, not six. The three that are dropped — dependency, driver,
    # normal tissue — all read `{context}`, and a question with no disease has
    # nothing to put there; searching them anyway spends a subprocess each on a
    # query with a hole in it. The three kept are the ones a contact question is
    # actually asking, and cutting the other three roughly halves the wait.
    axes = {k: v for k, v in AXES.items()
            if k in ("coactivator", "structure", "activation_domain")}
    emit("stage", {"id": "literature", "state": "running",
                   "detail": f"{len(axes)} interface axes for {', '.join(named)}"})
    total_on, hits, errors, read = 0, 0, [], {}
    for gene in named[:2]:          # two is enough to keep a demo interactive
        ev, papers, errs = gather(gene, "", cfg.partner_gene, axes=axes, per_axis=4)
        errors.extend(errs)
        for p in papers:
            emit("paper", {"title": p.title, "id": p.accession, "url": p.url,
                           "abstract": p.abstract, "axis": p.axis,
                           "gene": gene, "support": p.suggested_support})
        total_on += len(papers)
        hits += len(ev.axes_with_hits)
        verdict_ev = {"contact_documented": False, "support": "none",
                      "note": "not assessed"}
        if A.available():
            verdict_ev, _ = A.read_evidence(trace, gene, papers)
            emit("thinking", {"trace": trace.as_dict()})
        read[gene] = verdict_ev
        emit("evidence", {"gene": gene, "axes": {
            a: {"on_target": r.n_on_target, "returned": r.n_papers,
                "note": r.note, "query": r.query} for a, r in ev.axes.items()},
            "read": verdict_ev})

    documented = [g for g, r in read.items() if r.get("contact_documented") is True]
    emit("stage", {
        "id": "literature", "state": "done" if total_on else "abstained",
        "detail": f"{total_on} on-target papers across {hits} axes with hits",
        "note": ("Read, not just retrieved: the model was asked whether these "
                 "abstracts document a physical contact. "
                 + (f"Contact reported for {', '.join(documented)}."
                    if documented else
                    "No abstract here documents a physical contact."))
               + (f" {len(errors)} axis search(es) failed." if errors else "")})

    _structure_site_and_screen(emit, cfg, named, interface_evidence, free_receptor,
                               predict, require_site)

    emit("stage", {
        "id": "experiment", "state": "done",
        "detail": (f"co-fold {named[0]} with {cfg.partner_gene} and test whether the "
                   "predicted interface survives an ensemble"),
        "note": "Named a gene but no cohort, so the next step is structural, not "
                "genetic: `python scripts/predict_med23_interface.py "
                f"{named[0]} --accession <UNIPROT> --dispatch`."})
    if A.available():
        text, _ = A.answer(trace, question, emit.record()
                           if isinstance(emit, _Recorder) else {})
        if text:
            emit("answer", {"text": text, "model": A.MODEL,
                            "caveat": "Written by the model from the record of this "
                                      "run. Every number in it was computed by a "
                                      "stage above."})
    emit("thinking", {"trace": trace.as_dict()})
    emit("done", {"ok": True})


def _structure_site_and_screen(emit, cfg, genes: list[str],
                               interface_evidence: dict, free_receptor,
                               predict: str | None = None,
                               require_site: bool = False) -> None:
    """Everything downstream of the shortlist that depends only on the partner.

    Split out of `run_live` because it is the half of the pipeline that does
    not need a disease. The receptor, its pocket, and the compounds docked
    into it are properties of MED23 and the library, not of the tumour that
    was asked about — so a question naming a transcription factor and no
    disease can still be answered here. Refusing to run any of it because
    the dependency scan had nothing to scan threw away the half that did
    have an answer.
    """
    # ── structure ──────────────────────────────────────────────────────────
    #
    # Nothing is folded inline: a co-fold of a 1,368-residue subunit with a TF
    # is GPU minutes per sample and five samples per candidate, which is a
    # costed decision, not something a page load should trigger. What this
    # stage does is look for an ensemble a previous dispatch left on disk for
    # the shortlisted candidates, and report honestly when there is none.
    predicted = {g: _recorded_interface(g) for g in genes}
    predicted = {g: p for g, p in predicted.items() if p is not None}
    # An explicit request to fold one of these candidates now. Only when the
    # caller named it and only when nothing is already on disk: a page load must
    # never start a GPU job, and a job that already ran must never be repeated
    # because someone asked the same question twice.
    predict_note = ""
    if predict and predict.upper() in {g.upper() for g in genes} \
            and predict.upper() not in predicted:
        got, predict_note = _predict_interface(predict.upper(), emit)
        if got is not None:
            predicted[predict.upper()] = got
    converged = {g: (c, r) for g, (c, r) in predicted.items() if c.converged}
    # One site is boxed per run, so the consensus that can define it is the
    # first candidate's — and only if that ensemble converged.
    consensus = converged.get(genes[0], (None, None))[0] if genes else None

    if converged:
        emit("stage", {
            "id": "structure", "state": "done",
            "detail": "; ".join(
                f"{g}–{cfg.partner_gene}: {c.dominant_cluster_samples}/"
                f"{c.total_samples} samples agree ({c.ensemble_support:.0%})"
                for g, (c, _) in converged.items()),
            "note": "Boltz-2 ensembles from a prior GPU dispatch "
                    "(scripts/predict_med23_interface.py), not folded for this "
                    "question. Convergence across seeds is model self-consistency. "
                    "A predicted interface is a hypothesis, not a contact."})
    else:
        rejected = [f"{g}: {'; '.join(c.blockers)[:90]}" for g, (c, _) in predicted.items()]
        emit("stage", {
            "id": "structure", "state": "abstained",
            "detail": ("; ".join(rejected) if rejected
                       else "no ensemble on file for any candidate in this run"),
            "note": (predict_note or
                     ("Ensembles that ran but did not converge highlight nothing."
                      if rejected else
                      "Structural discovery is a separate costed GPU step, not run "
                      "inline. Ask for one with the button on the structure panel, "
                      "or `python scripts/predict_med23_interface.py <GENE> "
                      "--dispatch`."))})

    # ── druggable site ─────────────────────────────────────────────────────
    #
    # This stage used to go `done` whenever some candidate had
    # `interacting_region_mapped`. Those are different claims. A mapped
    # interacting region says a contact is documented somewhere on the TF; a
    # druggable site is a box of receptor coordinates you can dock into. The
    # interface reported the second on evidence for the first, so a run with no
    # structure at all showed a completed site stage.
    #
    # `receptor_residues` allows exactly two origins — an ensemble consensus,
    # or a published receptor-side pocket — and returns blockers otherwise.
    mapped = [g for g in genes
              if (link := interface_evidence.get(g))
              and link.interacting_region_mapped and not link.calibration_only]
    #
    # The curated origin is enabled here and labelled as what it is. The MED23
    # pocket from PDB 9F6Y is receptor-side coordinates someone deposited, so it
    # is a legal docking box; it is *where ELK1 binds*, so it is calibration and
    # not a site established for whatever this run shortlisted. Both halves of
    # that go on screen. `curated_for` is deliberately not passed: it would
    # refuse the pocket for every TF except ELK1, which is the right guard when
    # a caller is about to assert the site belongs to their TF, and the wrong
    # one when the interface is showing a labelled calibration surface.
    residues, basis, blockers = receptor_residues(cfg.partner_gene,
                                                  consensus=consensus,
                                                  allow_curated=True)
    site = None
    if not blockers and free_receptor and Path(free_receptor).exists():
        site = build_search_site(parse_mmcif(Path(free_receptor)), residues,
                                 cfg.structure, receptor_path=str(free_receptor))
        site.basis = basis

    if site is not None and site.defensible:
        emit("stage", {
            "id": "site", "state": "done",
            "detail": (f"{site.size[0]:.1f} x {site.size[1]:.1f} x {site.size[2]:.1f} A "
                       f"box on {cfg.partner_gene} around {len(site.residues)} residues"),
            "note": (f"From {basis}. Screened against the free receptor, not a "
                     "TF-occupied one. "
                     + ("This is where the ensemble puts the transcription factor, "
                        "so a compound in this box is the blocking hypothesis: it "
                        "occupies the surface the TF would have used. Predicted, "
                        "not observed."
                        if consensus is not None else
                        "This is the cavity that binds ELK1 — calibration, not a "
                        "site established for "
                        + (", ".join(genes) if genes else "any candidate here")
                        + ".")),
            "center": site.center, "size": site.size, "residues": site.residues})
    else:
        why = blockers or (site.blockers if site else
                           [f"the free {cfg.partner_gene} structure is not on disk"])
        emit("stage", {
            "id": "site", "state": "abstained",
            "detail": "; ".join(why)[:180],
            "note": ("A docking box needs receptor-side coordinates: an ensemble "
                     "consensus this run computed, or a published structure. "
                     + (f"{len(mapped)} candidate(s) here have a documented "
                        f"interacting region ({', '.join(mapped)}), which locates a "
                        "contact on the TF and is not a pocket on "
                        f"{cfg.partner_gene}." if mapped
                        else "No candidate here has a documented contact either."))})

    # ── screening ──────────────────────────────────────────────────────────
    #
    # Vina is minutes of CPU per compound, so the screen is not re-run per
    # question — and it does not need to be: the box is a property of the
    # receptor and the library, not of the disease context that was asked
    # about. The recorded run is served with its seed and its file, labelled
    # as recorded, exactly as `data.json` labels the cold-start landscape. A
    # screen that never ran and a screen that ran earlier are different
    # results and the interface says which one it is showing.
    # A request can forbid the screen. "Only if a localized partner-side site is
    # supported, proceed to screening; otherwise abstain and state what is
    # missing" is control flow, and until this gate existed the pipeline read it
    # as a disease name and screened anyway — answering a question nobody asked,
    # confidently. Support means one of two things and neither is the curated
    # cavity: a documented interaction with a mapped region on this partner, or
    # an ensemble that converged.
    supported = consensus is not None or bool(mapped)
    screen = _recorded_screen(site, genes)
    if require_site and not supported:
        missing = []
        if not mapped:
            missing.append("no candidate here has a documented interaction with a "
                           f"mapped region on {cfg.partner_gene}")
        if consensus is None:
            missing.append("no ensemble has converged on an interface for any of "
                           "them")
        emit("stage", {
            "id": "screening", "state": "abstained",
            "detail": "the request gated the screen on a supported partner-side site",
            "note": ("; ".join(missing) + ". The only site on file is the curated "
                     "ELK1 cavity, which is calibration and not support for these "
                     "candidates, so nothing was docked. Predicting an interface "
                     "for one of them is the step that would change this.")})
        screen, poses = None, []
    elif site is None or not site.defensible:
        poses = []
        emit("stage", {
            "id": "screening", "state": "abstained",
            "detail": "no defensible site, so no screen",
            "note": "Docking without a site finds something everywhere and means nothing."})
    elif screen is None:
        poses = []
        emit("stage", {
            "id": "screening", "state": "pending",
            "detail": "ready to dock against the site above",
            "note": f"No screen on file for this box. Run `python scripts/vina_smoke.py "
                    f"--dock --json {SCREEN_FILE.relative_to(ROOT)}` to produce one."})
    else:
        s, poses = screen
        emit("stage", {
            "id": "screening", "state": "done",
            "detail": (f"{s['scored']}/{s['docked']} compounds scored, "
                       f"{s['clean_poses']} without geometry flags, best "
                       f"{s['best']:.2f} kcal/mol"),
            "note": (f"Recorded screen, seed {s['seed']}, from "
                     f"{s['file']} — not re-run for this question; "
                     "the box depends on the receptor, not the context. Approved-drug "
                     "control library: a machinery check, not a designed screen. Vina "
                     "scores rank poses — they are not affinities and not evidence of "
                     "binding.")})

    # What the 3D view highlights on MED23 for each shortlisted candidate. The
    # two layers are different kinds of claim and stay separate: `ligands` are
    # docked poses this project computed, `residues` would be a predicted
    # interface, and there is no ensemble inline, so that list is empty and the
    # panel says "not predicted" rather than borrowing the pocket.
    for g in genes:
        hit = converged.get(g)
        residues = sorted(set(hit[1].get("partner_contact_residues", []))) if hit else []
        emit("highlight", {
            "gene": g, "residues": residues,
            "ligands": poses if screen else [],
            # The union of what the shown poses contact. Computed, not
            # predicted and not observed: these are the residues a compound
            # this project docked came within 4.5 A of.
            "ligand_residues": sorted({r for p in (poses if screen else [])
                                       for r in p["residues"]}),
            "note": ((f"{len(residues)} {cfg.partner_gene} residues, from an ensemble "
                      f"where {hit[0].dominant_cluster_samples}/{hit[0].total_samples} "
                      "samples agree. A prediction, not a contact.") if hit else
                     (f"No interface between {g} and {cfg.partner_gene} has been "
                      "predicted, so nothing is highlighted for this candidate."))
                    + (" The poses shown are docked into the ELK1 cavity."
                       if screen else "")})



def run_live(question: str, data_paths, cfg, emit: Callable[[str, dict], None],
             interface_evidence: dict[str, MediatorLink] | None = None,
             top_n: int = 3, free_receptor: Path | None = None,
             predict: str | None = None) -> None:
    """Stream the real pipeline for one question. `emit(event, payload)` per step."""
    ge_path, model_path, tf_path = data_paths
    interface_evidence = interface_evidence or {}
    emit = _Recorder(emit)

    if not Path(ge_path).exists():
        emit("stage", {"id": "question", "state": "blocked",
                       "detail": f"{Path(ge_path).name} is not present",
                       "note": "see team/TASKS.md for the fetch command"})
        for sid in DOWNSTREAM:
            emit("stage", {"id": sid, "state": "pending", "detail": "not run"})
        emit("done", {"ok": False})
        return

    # ── question -> context ────────────────────────────────────────────────
    #
    # The model reads the question; token matching is the fallback. Free text is
    # ambiguous in ways overlap cannot resolve — "the childhood bone tumour
    # driven by the EWSR1-FLI1 fusion" names Ewing sarcoma without containing
    # the word. But the model chooses from the real Oncotree vocabulary and its
    # answer is bounds-checked against that list, so it cannot name a context
    # the scan is unable to answer about.
    model = _model_table(model_path)
    trace = A.AgentTrace()
    options = vocabulary(model)
    m, why, decided_by = None, "", "token match"
    policy = {"require_interface_site": False, "quote": None}

    if A.available():
        # What the request demands of the pipeline, before anything runs. A
        # request can gate a stage, and a gate nobody read is a gate nobody
        # honoured.
        policy, _ = A.read_request(trace, question)
        emit("thinking", {"trace": trace.as_dict()})
        if policy["require_interface_site"]:
            emit("policy", {"require_interface_site": True,
                            "quote": policy.get("quote"),
                            "note": "The screen will run only where a partner-side "
                                    "site is supported. Otherwise it abstains and "
                                    "says what is missing."})
        choice, reason, call = A.decide_context(trace, question, options)
        emit("thinking", {"trace": trace.as_dict()})
        if call is not None and not call.error:
            decided_by = f"model ({A.MODEL})"
            m, why = choice, reason
        else:
            why = (call.error if call else "") or ""

    if m is None and decided_by == "token match":
        res = resolve(question, model)
        m = res.match
        why = res.note

    if m is None:
        # A question can name no disease and still be answerable. "What binds
        # the MED23 groove?" or "is there a documented ELK1–MED23 contact?" are
        # partner questions: the receptor, its pocket and the compounds docked
        # into it do not depend on a tumour, and the literature axes for a named
        # gene do not either. Only the dependency scan needs a context, so only
        # the dependency stages abstain.
        named = _named_genes(question, tf_path, cfg.partner_gene)
        if named:
            _partner_first(question, named, cfg, emit, trace, why, decided_by,
                           interface_evidence, free_receptor, predict,
                           policy["require_interface_site"])
            return
        emit("stage", {"id": "question", "state": "blocked",
                       "detail": question or "no question given",
                       "note": (why or "no disease context in this question "
                                       "matched the DepMap vocabulary")
                               + f" (decided by {decided_by}), and no gene this "
                               + "project can answer about was named either"})
        for sid in DOWNSTREAM:
            emit("stage", {"id": sid, "state": "pending", "detail": "not run",
                           "note": "waiting on a disease context or a named gene"})
        emit("done", {"ok": False})
        return

    emit("stage", {
        "id": "question", "state": "done", "detail": question,
        "note": (f"resolved to {m.context} — {m.level} level, {m.n_models} models"
                 + (f", within {m.parent_lineage}" if m.parent_lineage else "")
                 + f". {why} (decided by {decided_by}; chosen from "
                 + f"{len(options)} contexts the data can answer about)"),
        "context": m.context, "level": m.level, "decided_by": decided_by})

    # ── discovery: the canonical scan, for THIS context ────────────────────
    emit("stage", {"id": "discovery", "state": "running",
                   "detail": f"screening the TF universe against {m.context}"})
    ge, model_full = V.load_matrix(str(ge_path), str(model_path), str(tf_path))
    verdicts = V.scan_context(ge, model_full, m.context, level=m.level)
    if not verdicts:
        emit("stage", {"id": "discovery", "state": "blocked",
                       "detail": f"{m.context} has too few screened models to test",
                       "note": f"{m.n_models} models are annotated {m.context}, but "
                               "the CRISPR matrix covers fewer than the minimum; "
                               "a broader context would be testable."})
        for sid in DOWNSTREAM[2:]:
            emit("stage", {"id": sid, "state": "pending", "detail": "not run"})
        emit("done", {"ok": False})
        return

    n_lines = verdicts[0].n_target
    emit("stage", {
        "id": "discovery", "state": "done",
        "detail": f"{len(verdicts)} TFs across {n_lines} {m.context} models",
        "note": (f"DepMap {V.DEPMAP_RELEASE} Chronos, Lambert TF catalogue. Gate "
                 f"from {V.CANONICAL_SOURCE} — the same verdict the batch scan "
                 f"reaches. Computed now, not cached."),
        "context": m.context, "level": m.level})

    # The gate the interface draws must be the gate that ran. data.json seeds the
    # cold page with `dependency_scout`'s four-way AND, and a live run used to
    # leave it there — so the plot kept shading "the only region that passes"
    # around a rule no longer in force, and INSM1 passed while sitting well
    # outside the shaded corner. Emitted from the canonical config, per run.
    emit("thresholds", {
        "gate": "either path passes",
        "source": V.CANONICAL_SOURCE,
        "release": V.DEPMAP_RELEASE,
        "fdr_alpha": V.FDR_ALPHA,
        "confidence_floor": V.MIN_N_FULL_CONFIDENCE,
        "paths": [
            {"name": "median",
             "description": "the context as a whole is dependent",
             "terms": [f"median effect ≤ {V._stage1_config.IN_CONTEXT_DEPENDENCY_THRESHOLD}",
                       f"median elsewhere > {V._stage1_config.OUT_OF_CONTEXT_NONDEPENDENCY_THRESHOLD}"]},
            {"name": "specificity-first",
             "description": ("part of the context is dependent and almost nothing "
                             "else is — catches subpopulation dependencies a "
                             "median dilutes away"),
             "terms": [f"dependent here ≥ {V._stage1_config.SPECIFICITY_FIRST_MIN_TARGET_FRACTION:.0%}",
                       f"dependent elsewhere ≤ {V._stage1_config.SPECIFICITY_FIRST_MAX_OTHER_FRACTION:.0%}"]},
        ],
        # Kept for the plot's guide line only. It is one term of one path, not
        # the gate, and the interface must not present it as a pass/fail line.
        "median_target_effect": V._stage1_config.IN_CONTEXT_DEPENDENCY_THRESHOLD,
    })

    emit("landscape", {"context": m.context, "level": m.level, "points": [
        {"gene": v.gene,
         "median": round(v.median_target, 3),
         "sel": round(v.median_other - v.median_target, 3),
         "tfrac": round(v.target_dependent_fraction, 3),
         "ofrac": round(v.other_dependent_fraction, 3),
         "n": v.n_target, "q": v.qvalue, "route": v.route,
         "pass": v.significant, "flag": v.dependency_flag,
         "why": V.to_candidate(v).gate.failures,
         "low_n": v.low_n}
        for v in verdicts]})

    # ── gates ──────────────────────────────────────────────────────────────
    top = V.shortlist(verdicts, top_n)
    flagged = [v for v in verdicts if v.dependency_flag]
    routes: dict[str, int] = {}
    for v in flagged:
        routes[v.route] = routes.get(v.route, 0) + 1
    near = [v for v in flagged if not v.significant]
    emit("stage", {
        "id": "ranking", "state": "done" if top else "abstained",
        "detail": (f"{len(top)} shortlisted of {len(flagged)} that clear the gate: "
                   + ", ".join(f"{v.gene} (via {v.route})" for v in top)) if top
                  else f"{len(flagged)} of {len(verdicts)} clear the gate, none at FDR "
                       f"{V.FDR_ALPHA}",
        "note": ("; ".join(f"{n} via {r}" for r, n in sorted(routes.items()))
                 + (f". {len(near)} more pass the gate but miss FDR: "
                    + ", ".join(f"{v.gene} q={v.qvalue:.2f}" for v in near[:4])
                    if near else ""))})

    emit("candidates", {"rows": [
        {"gene": v.gene, "context": v.context, "level": v.context_level,
         "n": v.n_target,
         "median": round(v.median_target, 3),
         "sel": round(v.median_other - v.median_target, 3),
         "tfrac": round(v.target_dependent_fraction, 3),
         "ofrac": round(v.other_dependent_fraction, 3),
         "q": v.qvalue, "route": v.route,
         "gate_pass": True, "gate_why": [], "awaiting": False,
         "shortlisted": True,
         # The partner is what this run SEARCHED against, not something it
         # discovered. Nothing computed here establishes that MED23 is this
         # TF's coactivator.
         "partner": cfg.partner_gene, "partner_is_query": True,
         "involvement": "unknown", "region": None, "region_mapped": False,
         "tractability": "unknown", "control": False, "concerns": [],
         "ready": False, "blocked_because": None, "claims": []}
        for v in top]})

    # ── literature: six axes per candidate, on-target only ─────────────────
    if not top:
        emit("stage", {"id": "literature", "state": "abstained",
                       "detail": "no candidate survived the gate to search evidence for",
                       "note": "Retrieval follows the gate; searching all "
                               f"{len(verdicts)} TFs would find something for "
                               "every one of them."})
    else:
        emit("stage", {"id": "literature", "state": "running",
                       "detail": f"six evidence axes for {len(top)} candidate(s)"})
        total_on, total_axes, hits, leads, errors = 0, 0, 0, [], []
        read: dict[str, dict] = {}
        for v in top:
            ev, papers, errs = gather(v.gene, m.context, cfg.partner_gene, per_axis=4)
            errors.extend(errs)
            for p in papers:
                emit("paper", {"title": p.title, "id": p.accession, "url": p.url,
                               "abstract": p.abstract, "axis": p.axis,
                               "gene": v.gene, "support": p.suggested_support})
            total_on += len(papers)
            total_axes += len(ev.axes)
            hits += len(ev.axes_with_hits)

            # Whether a contact is documented is a reading question, and the
            # keyword triage it replaces called any abstract containing "crystal
            # structure" direct experimental evidence -- including reviews and
            # papers about a different complex entirely.
            verdict_ev = {"contact_documented": False, "support": "none",
                          "note": "not assessed"}
            if A.available():
                verdict_ev, _ = A.read_evidence(trace, v.gene, papers)
                emit("thinking", {"trace": trace.as_dict()})
            read[v.gene] = verdict_ev
            if verdict_ev.get("contact_documented") is True:
                leads.append(v.gene)

            emit("evidence", {"gene": v.gene, "axes": {
                a: {"on_target": r.n_on_target, "returned": r.n_papers,
                    "note": r.note, "query": r.query}
                for a, r in ev.axes.items()},
                "read": verdict_ev})
        if errors:
            emit("stage", {"id": "literature", "state": "blocked",
                           "detail": f"{len(errors)} axis search(es) failed",
                           "note": "; ".join(errors[:2])})
        else:
            emit("stage", {
                "id": "literature", "state": "done" if total_on else "abstained",
                "detail": f"{total_on} on-target papers across {hits}/{total_axes} axes",
                "note": ("Retrieved, not read. Only papers naming the gene are counted: "
                         "semantic search returns nearest matches, and nearest is not "
                         "relevant. "
                         + (f"Coactivator leads: {', '.join(leads)}." if leads
                            else "No coactivator lead on any candidate."))})

        # Prose over the numbers already computed. The model is given them and
        # told not to change or add any; it explains, it does not decide.
        if A.available() and top:
            summary, _ = A.explain(trace, m.context, [
                {"gene": v.gene, "median": v.median_target,
                 "median_other": v.median_other,
                 "tfrac": v.target_dependent_fraction,
                 "ofrac": v.other_dependent_fraction, "n": v.n_target,
                 "q": v.qvalue, "route": v.route,
                 "evidence_note": read.get(v.gene, {}).get("note", "not assessed")}
                for v in top])
            if summary:
                emit("summary", {"text": summary, "model": A.MODEL,
                                 "caveat": "Model-written prose over computed "
                                           "numbers. It explains the result; it "
                                           "did not produce it."})
            emit("thinking", {"trace": trace.as_dict()})

    # ── specificity ────────────────────────────────────────────────────────
    emit("stage", {
        "id": "specificity", "state": "done" if top else "abstained",
        "detail": f"{len(top)} candidate(s) with a selective dependency in {m.context}",
        "note": "Cancer-cell selectivity is not normal-tissue safety."})

    _structure_site_and_screen(emit, cfg, [v.gene for v in top],
                               interface_evidence, free_receptor, predict,
                               policy["require_interface_site"])

    # ── next experiment ────────────────────────────────────────────────────
    emit("stage", {
        "id": "experiment", "state": "done",
        "detail": (f"test whether {top[0].gene} dependence in {m.context} requires a "
                   "coactivator contact" if top
                   else f"no TF clears the gate in {m.context} at {m.level} granularity"),
        "note": ("The dependency is real and the contact is undocumented; closing "
                 "that gap is what unblocks the rest." if top
                 else ("Either this context is not TF-addicted, or it needs finer "
                       "resolution than " + m.level + ". Both are results."))})
    if A.available():
        text, _ = A.answer(trace, question, emit.record()
                           if isinstance(emit, _Recorder) else {})
        if text:
            emit("answer", {"text": text, "model": A.MODEL,
                            "caveat": "Written by the model from the record of this "
                                      "run. Every number in it was computed by a "
                                      "stage above."})
    emit("thinking", {"trace": trace.as_dict()})
    emit("done", {"ok": True})
