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
import time
from pathlib import Path
from typing import Callable

import pandas as pd
from pydantic import ValidationError

import sessions as S_
from dependency_scout.models import (Claim, InterfaceTractability,
                                     MediatorLink, SupportType)
from reagent_workflow import agent as A
from reagent_workflow import verdict as V
from reagent_workflow.chemistry import depict, describe, parse_molblock
from reagent_workflow.interface import InterfaceConsensus, parse_mmcif
from reagent_workflow.literature import AXES, gather
from reagent_workflow.resolve import resolve, vocabulary
from reagent_workflow.site import (CURATED_POCKETS, build_search_site,
                                   receptor_residues)

DOWNSTREAM = ("literature", "discovery", "ranking", "specificity", "site",
              "structure", "screening", "experiment")

ROOT = Path(__file__).resolve().parents[1]
SCREEN_FILE = ROOT / "runs" / "vina_smoke.json"
MAX_POSES_SHOWN = 3
SCREEN_SEED = 20260816     # fixed: a screen you cannot repeat is not a result


class _Recorder:
    """Passes every event through, and keeps the record the answer is written from.

    The alternative was assembling a summary by hand at each call site, which
    drifts the moment a stage changes its wording — and an answer written from
    a drifted summary is an answer about a run that did not happen. Wrapping
    `emit` means the model reads exactly what the reader saw.

    It is also what a session retains. `state()` returns the whole stream, each
    event stamped with the epoch at which it was *computed* — not the epoch at
    which it was last sent. A replayed event arrives carrying its original
    stamp and is re-recorded under that stamp, so a follow-up on a follow-up
    still reports the age of the run that did the work rather than the age of
    the last replay. Without that, retained state quietly ages backwards until
    it reads as fresh, which is the one thing this interface must never do.
    """

    def __init__(self, emit: Callable[[str, dict], None]) -> None:
        self._emit = emit
        self.stages: dict[str, dict] = {}
        self.candidates: list[dict] = []
        self.highlights: list[dict] = []
        self.papers = 0
        # The retained half: every event with its compute time, plus the few
        # indexes a follow-up needs to answer without walking the stream.
        self.events: list[tuple[str, dict, float]] = []
        self.measured: dict[str, dict] = {}
        self.evidence: dict[str, dict] = {}
        self.rows: list[dict] = []
        self.context: str | None = None
        self.level: str | None = None
        self.genes: list[str] = []
        self.partner: str = ""
        # A condition the request placed on the pipeline. It is retained with
        # the rest of the run because it is still in force for the session: a
        # follow-up that forgot it would run a screen the request forbade.
        self.require_site = False
        # Live objects a follow-up reuses in this process only: the docking box
        # the site stage built, whether the evidence gate supported it, and the
        # evidence dict the literature stage may have promoted a reading into.
        self.runtime: dict = {}

    def __call__(self, event: str, payload: dict) -> None:
        self.events.append(
            (event, payload, float(payload.get("retained_at_epoch") or time.time())))
        if event == "stage":
            self.stages[payload["id"]] = {k: payload.get(k)
                                          for k in ("state", "detail", "note")}
            if payload["id"] == "question" and payload.get("context"):
                self.context = payload.get("context")
                self.level = payload.get("level")
        elif event == "landscape":
            self.context = payload.get("context", self.context)
            self.level = payload.get("level", self.level)
            self.measured = {p["gene"]: p for p in payload.get("points", [])}
        elif event == "evidence":
            self.evidence[payload.get("gene")] = payload
        elif event == "policy":
            self.require_site = bool(payload.get("require_interface_site"))
        elif event == "candidates":
            self.rows = list(payload.get("rows", []))
            self.candidates = [{k: r.get(k) for k in
                                ("gene", "context", "median", "sel", "tfrac",
                                 "ofrac", "n", "q", "route")}
                               for r in payload.get("rows", [])]
        elif event == "highlight":
            # Both lists, and the SMILES with them. The reader is looking at the
            # structures on the canvas while the answer is being written, and an
            # answer that says the compounds are not in its record — while the
            # panel beside it lists them — is describing a different run. The
            # site's owner travels too, so the model can say whose cavity these
            # were docked into rather than implying they are the candidate's.
            self.highlights.append(
                {"gene": payload.get("gene"),
                 "predicted_partner_residues": payload.get("residues"),
                 "compounds": [{k: c.get(k) for k in
                                ("name", "score", "smiles", "residues")}
                               for c in payload.get("ligands", [])],
                 "compounds_docked_into_this_site": [
                     {k: c.get(k) for k in ("name", "score", "smiles", "residues")}
                     for c in payload.get("site_ligands", [])],
                 "site_belongs_to": payload.get("site_owner")})
        elif event == "paper":
            self.papers += 1
        self._emit(event, payload)

    def record(self) -> dict:
        return {"stages": self.stages, "shortlist": self.candidates,
                "papers_retrieved": self.papers,
                "partner_site_and_compounds": self.highlights}

    def state(self) -> dict:
        """Everything a follow-up can be answered from, JSON-able.

        The event stream is kept whole rather than summarised. A summary is a
        second description of the run that drifts from the first one, and this
        file has already been bitten twice by a number recovered from prose;
        replaying the events the reader actually saw cannot drift from them.
        Only the `thinking` events are thinned — the trace arrives cumulative,
        so every one but the last is a prefix of the last.
        """
        thinking = [i for i, (e, _, _) in enumerate(self.events) if e == "thinking"]
        drop = set(thinking[:-1])
        now = time.time()
        return {
            # The oldest surviving piece of work, not the newest. A follow-up
            # re-records what it replays and adds a handful of events of its
            # own; taking the newest would date the whole record to the last
            # question asked about it, and "retained from the run at <now>" is
            # the exact sentence this labelling exists to prevent.
            "computed_at": min((at for _, _, at in self.events), default=now),
            # When the scan itself ran, kept separately because it is what a
            # verdict quotes and it must not drift to the start of whichever
            # run happened to replay it.
            "scan_at": next((at for e, _, at in reversed(self.events)
                             if e == "landscape"), None),
            "context": self.context,
            "level": self.level,
            "partner": self.partner,
            "genes": list(self.genes),
            "require_site": self.require_site,
            "measured": self.measured,
            "rows": self.rows,
            "evidence": self.evidence,
            "stages": self.stages,
            "events": [[e, p, at] for i, (e, p, at) in enumerate(self.events)
                       if i not in drop],
        }


# Words a model returns when it means "no region", which are not regions.
_NO_REGION = {"", "null", "none", "n/a", "na", "not stated", "not specified",
              "unknown", "not reported", "unclear", "full length", "full-length"}


def _open_to_reading(existing: MediatorLink | None) -> bool:
    """Whether a link read from a paper may replace what is already on file.

    Nothing on file, or a file that says "we looked and found no region": both
    are questions a paper can answer. A curated link that already maps a region
    is not — a person checked that one — and neither is a calibration control,
    whose whole purpose is to stay a known positive rather than become evidence.
    """
    return existing is None or not (existing.interacting_region_mapped
                                    or existing.calibration_only)


def _promote_reading(gene, verdict, papers, cfg, interface_evidence, emit) -> bool:
    """Carry a reading into the evidence the screen's gate consults.

    Both literature paths call this, so a region found in the ranked path and a
    region found for a named gene reach the gate the same way. Returns whether
    anything was promoted, and says so on the stage: evidence that appeared
    mid-run because a model read an abstract must be visible as that, not
    indistinguishable from a file a person wrote.
    """
    if not _open_to_reading(interface_evidence.get(gene)):
        return False
    link = _link_from_reading(gene, verdict, papers, cfg.partner_gene)
    if link is None:
        return False
    interface_evidence[gene] = link
    emit("stage", {
        "id": "literature", "state": "running",
        "detail": f"mapped region read for {gene}",
        "note": (f"{gene}–{cfg.partner_gene}: {link.tf_region}. Read from an "
                 "abstract in this run, not from a curated file. It opens the "
                 "screen, and it remains a reading of a paper rather than a "
                 "measurement made here.")})
    return True


def _link_from_reading(gene: str, verdict: dict, papers: list,
                       partner_gene: str) -> MediatorLink | None:
    """A `MediatorLink` from what the model read, or None.

    The screen's gate asks for a mapped interacting region, and until now the
    only way to have one was for a human to write a file in `examples/`. So the
    pipeline could retrieve the right paper, read it correctly, say "contact
    documented" on screen — and still refuse to screen, because nothing carried
    that reading into the evidence the gate consults. This is that carriage.

    It is deliberately hard to satisfy. The abstract must name the partner we
    are modelling, the support must be physical rather than genetic, and the
    region must be a region: a whole-protein pull-down with no residues is
    exactly the claim `CLAUDE.md` says to reject rather than pass downstream.
    A curated link always wins over this one — those were checked by a person.
    """
    if verdict.get("contact_documented") is not True:
        return None
    if verdict.get("support") not in ("structure", "biochemical"):
        return None
    region = (verdict.get("region") or "").strip()
    if region.lower() in _NO_REGION:
        return None
    # The partner has to be the protein this run is modelling against. An
    # abstract about the same TF touching a different coactivator documents a
    # contact, but not the one whose cavity the screen would dock into.
    partner = (verdict.get("partner") or "").upper()
    if partner_gene.upper() not in partner.replace("-", "").replace(" ", ""):
        return None
    # A claim cannot exist without its sources, and the model is asked which
    # abstract the region came from. If it named one, cite that; otherwise cite
    # everything the gene's search returned, which is still a real provenance.
    src = verdict.get("region_source")
    cited = [papers[src - 1]] if isinstance(src, int) and 1 <= src <= len(papers) else papers[:3]
    citations = [p.url or p.accession for p in cited if (p.url or p.accession)]
    if not citations:
        return None
    tract = {"short_linear_motif": InterfaceTractability.SHORT_LINEAR_MOTIF,
             "folded_domain": InterfaceTractability.FOLDED_DOMAIN}.get(
                 verdict.get("tractability"), InterfaceTractability.UNKNOWN)
    try:
        return MediatorLink(
            partner_gene=partner_gene, interacting_region_mapped=True,
            tf_region=region, tractability=tract,
            claims=[Claim(statement=(f"{gene} contacts {partner_gene} through "
                                     f"{region}."),
                          support=SupportType.DIRECT_EXPERIMENTAL,
                          citations=citations,
                          note=(verdict.get("note") or "")[:400] or None)])
    except ValidationError:
        # The model returned something the contract refuses. That is the
        # contract working, not an error to route around.
        return None


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
              # §17: a score without its geometry is the most convincing wrong
              # number this project can produce. Offset from the box centre,
              # whether the pose is inside the box at all, and whether it
              # clashes are what separate a pose in the site from a pose that
              # merely scored well somewhere.
              "offset": r["geometry"].get("offset_from_box_centre"),
              "inside_box": r["geometry"].get("inside_box"),
              "clash": bool(r.get("clash")),
              "smiles": r.get("smiles", ""),
              "provenance": r.get("provenance", ""),
              # Why this compound was proposed for this site, and whether the
              # structure is one the outside world recognises. A generated
              # library is only usable if both travel with it.
              "rationale": r.get("rationale", ""),
              "identity": r.get("identity") or {},
              # The molecule itself, twice: the heavy-atom skeleton of the pose
              # that was scored, in the receptor's frame, and the 2D structure
              # a chemist reads. A score with no molecule beside it is a number
              # about something the reader cannot see.
              "pose": parse_molblock(r.get("pose_sdf", "")),
              # The molblock as it came back, for the viewer that can read one
              # directly. Four kilobytes a compound, and it saves rebuilding a
              # molecule the screen already serialized.
              "sdf": r.get("pose_sdf", ""),
              # What the molecule is made of, not just what it scored. These
              # are the §18 descriptors the chemistry module already computes
              # and nothing displayed: molecular weight, lipophilicity, polar
              # surface, donors and acceptors. A reader deciding whether a
              # compound is worth an experiment reads these before the score.
              "descriptors": describe(r.get("smiles", "")),
              "svg": depict(r.get("smiles", ""))}
             for r in clean[:MAX_POSES_SHOWN]]
    return (summary, poses) if poses else None


LIBRARY_SIZE = 6           # proposal + identity checks + docking, kept interactive


def _live_screen(site, genes: list[str], emit, trace):
    """Propose a library for this site, check it, dock it, and keep the record.

    The site's residues and their chemistry are what the proposal is made from,
    so the library is about this groove rather than about docking in general.
    Every structure is standardized and looked up in PubChem by InChIKey before
    it is docked: a model asked for a named compound can return a SMILES that
    parses cleanly and is a different molecule, and only a check outside this
    process can catch that.

    Returns the same `(summary, poses)` shape a recorded screen returns, so the
    view cannot tell — and does not need to — whether the screen was read from
    disk or run just now. The stage note says which.
    """
    if not A.available():
        return None
    from reagent_workflow import screen as S

    gene = genes[0] if genes else ""
    pocket = CURATED_POCKETS.get("MED23", {})
    labels = [f"{r}" for r in site.residues]
    described = {
        "receptor": "MED23", "structure": "PDB 9F76 (apo)",
        "site_residues": labels,
        "site_size_angstrom": [round(x, 1) for x in site.size],
        "basis": site.basis,
        "note": (pocket.get("note", "")
                 + " This is a protein-protein interface groove, not an enzyme "
                   "active site."),
        "candidate_transcription_factor": gene,
    }
    emit("stage", {"id": "screening", "state": "running",
                   "detail": f"proposing a library for this site ({LIBRARY_SIZE} compounds)",
                   "note": "The library is proposed from the site's own residues, "
                           "then every structure is identity-checked before docking."})
    proposed, _ = A.propose_library(trace, described, n=LIBRARY_SIZE)
    emit("thinking", {"trace": trace.as_dict()})
    if not proposed:
        return None

    def log(msg):
        emit("stage", {"id": "screening", "state": "running",
                       "detail": f"screening {len(proposed)} proposed compounds",
                       "note": str(msg)[:220]})

    usable, rejected = S.prepare_compounds(proposed, log=log)
    if not usable:
        return None
    try:
        record = S.dock(site, parse_mmcif(Path(site.receptor_path)), usable,
                        None, seed=SCREEN_SEED, log=log)
    except Exception as e:                                   # noqa: BLE001
        emit("stage", {"id": "screening", "state": "blocked",
                       "detail": f"docking failed: {type(e).__name__}",
                       "note": str(e)[:220]})
        return None
    record["rejected_proposals"] = rejected

    out = ROOT / "runs" / "screens" / f"{(gene or 'MED23').upper()}_MED23.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return _read_screen(out, site)


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
        # Eight per axis, not four. One subprocess runs per axis either way, so
        # the extra results are nearly free — and the abstract that names the
        # residues is routinely not in the top four. Retrieval was the binding
        # constraint on whether anything could be mapped at all.
        ev, papers, errs = gather(gene, "", cfg.partner_gene, axes=axes, per_axis=8)
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
            # What was read becomes evidence the gate can consult, so a mapped
            # region found in the literature opens the screen the same way a
            # curated one does.
            _promote_reading(gene, verdict_ev, papers, cfg, interface_evidence, emit)
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

    if isinstance(emit, _Recorder):
        emit.genes = list(named)
        emit.partner = cfg.partner_gene
        emit.runtime.setdefault("trace", trace)
        emit.runtime.setdefault("interface_evidence", interface_evidence)
    _structure_site_and_screen(emit, cfg, named, interface_evidence, free_receptor,
                               predict, require_site, trace)

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
                               require_site: bool = False, trace=None) -> None:
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
    if predict and predict.upper() in {g.upper() for g in genes}:
        if predict.upper() in predicted:
            # It already ran. Refusing to pay for it twice is right; doing that
            # silently is not — the button spins, the run repeats, the screen is
            # identical, and the only honest reading available to the reader is
            # that the thing is broken.
            done, _rec = predicted[predict.upper()]
            emit("stage", {
                "id": "structure", "state": "running",
                "detail": f"{predict.upper()}: an ensemble is already on disk",
                "note": (f"Status {done.status}, {done.dominant_cluster_samples}/"
                         f"{done.total_samples} samples agree. Nothing was "
                         "dispatched and nothing was charged. Delete "
                         f"runs/interfaces/{predict.upper()}_{cfg.partner_gene}/ "
                         "to fold it again, or add seeds with "
                         "scripts/predict_med23_interface.py.")})
        else:
            got, predict_note = _predict_interface(predict.upper(), emit)
            if got is not None:
                predicted[predict.upper()] = got
    converged = {g: (c, r) for g, (c, r) in predicted.items() if c.converged}
    # One site is boxed per run. It used to be read from genes[0] alone, so a
    # second candidate's converged ensemble was computed and then thrown away
    # whenever the first candidate in the shortlist had not converged. Walk
    # the shortlist in order and take the first candidate that actually
    # converged — still deterministic, but no longer blind to everyone but
    # position zero.
    consensus_gene = next((g for g in genes if g in converged), None)
    consensus = converged[consensus_gene][0] if consensus_gene else None

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
        # An ensemble that ran and disagreed is not the same result as no
        # ensemble at all, and neither is a refusal. §10 of the science brief
        # names three states and licenses a different next action for each:
        # converged may build a site, ambiguous must preserve its hypotheses and
        # sample more, refused stops. Collapsing ambiguous into abstained throws
        # away the localized minority hypothesis that the ELK1 control showed a
        # majority vote can bury — which is the exact failure the three-state
        # classification exists to prevent.
        ambiguous = {g: (c, r) for g, (c, r) in predicted.items()
                     if c.status == "ambiguous"}
        rejected = [f"{g}: {'; '.join(c.blockers)[:90]}" for g, (c, _) in predicted.items()]
        if ambiguous:
            kept = []
            for g, (c, _) in ambiguous.items():
                local = [h for h in c.alternative_hypotheses if getattr(h, "localized", False)]
                kept.append(f"{g}: {len(c.alternative_hypotheses)} hypothes(es) retained, "
                            f"{len(local)} localized, best support "
                            f"{c.ensemble_support:.0%}")
            emit("stage", {
                "id": "structure", "state": "ambiguous",
                "detail": "; ".join(kept),
                "note": ("The ensemble holds localized interface hypotheses and none "
                         "of them carried the vote. They are kept, not discarded: a "
                         "minority hypothesis can be the correct one, which is why "
                         "this is not a refusal. Next action is to sample more seeds. "
                         "No site is built and nothing is docked from an unconverged "
                         "ensemble.")})
        else:
            emit("stage", {
                "id": "structure", "state": "abstained",
                "detail": ("; ".join(rejected) if rejected
                           else "no ensemble on file for any candidate in this run"),
                "note": (predict_note or
                         ("No defensible localized hypothesis in the ensemble, so "
                          "nothing is highlighted and no site is built from it."
                          if rejected else
                          "Structural discovery is a separate costed GPU step, not "
                          "run inline. Ask for one with the button on the structure "
                          "panel, or `python scripts/predict_med23_interface.py "
                          "<GENE> --dispatch`."))})

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
                       f"box on {cfg.partner_gene} around {len(site.residues)} residues"
                       # Which candidate's ensemble this box came from, so a run
                       # with more than one converged candidate does not leave
                       # the reader guessing which one actually supplied it.
                       + (f", consensus from {consensus_gene}"
                          if consensus is not None else "")),
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
    # "Only if a localized partner-side site is supported, proceed to
    # screening; otherwise abstain and state what is missing" is not a
    # sentence a request has to use for the pipeline to owe it. It is the
    # standing rule (§15-16): the screen requires evidence that places a site
    # on THIS partner for one of THESE candidates, and the two things that
    # count as that evidence are a documented interaction with a mapped
    # region on this partner, or an ensemble that converged. The curated
    # ELK1 cavity is neither — it is a labelled calibration surface, legal to
    # DISPLAY (see the site stage above) and never legal to dock into on a
    # candidate's behalf. This used to be enforced only when an LLM read the
    # question and decided it demanded a gate, so the default — no gate read,
    # no ensemble converged, nothing mapped — was to screen anyway on
    # whatever `receptor_residues` fell back to, which is the ELK1 pocket.
    # `require_site` can no longer be the thing that turns this check on; it
    # can only sharpen the message when the request said so explicitly.
    supported = consensus is not None or bool(mapped)
    screen = _recorded_screen(site, genes)
    if not supported:
        missing = []
        if not mapped:
            missing.append("no candidate here has a documented interaction with a "
                           f"mapped region on {cfg.partner_gene}")
        if consensus is None:
            missing.append("no ensemble has converged on an interface for any of "
                           "them")
        emit("stage", {
            "id": "screening", "state": "abstained",
            "detail": ("the request gated the screen on a supported partner-side site"
                       if require_site else
                       "no supported partner-side site for any shortlisted candidate"),
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
        # Nothing on file for this box, so run one: propose a library from the
        # site's own residues, check every structure, and dock it. The library
        # is generated rather than stored because a constant list answers the
        # same question for every site the pipeline will ever build.
        screen = _live_screen(site, genes, emit, trace) if trace is not None else None
        if screen is None:
            poses = []
            emit("stage", {
                "id": "screening", "state": "abstained",
                "detail": "no screen was produced for this box",
                "note": "Either no library could be proposed and identified, or "
                        "docking failed. Nothing is shown in place of a screen "
                        "that did not run."})
        else:
            s_, poses = screen
            emit("stage", {
                "id": "screening", "state": "done",
                "detail": (f"{s_['scored']}/{s_['docked']} compounds scored, "
                           f"{s_['clean_poses']} without geometry flags, best "
                           f"{s_['best']:.2f} kcal/mol"),
                "note": (f"Library proposed for this site and docked now, seed "
                         f"{s_['seed']}. Every structure was standardized and "
                         "identity-checked against PubChem by InChIKey before "
                         "docking. Vina scores rank poses — not affinities, not "
                         "evidence of binding."),
                # The same numbers the detail sentence states, as fields. A
                # reader parsing prose to recover a count is one rewording away
                # from a wrong number on screen, and this file has already been
                # bitten by exactly that twice.
                "docked": s_["docked"], "scored": s_["scored"],
                "clean_poses": s_["clean_poses"], "shown": len(poses),
                "best": s_["best"], "verified": s_.get("verified"),
                "best_verified": s_.get("best_verified"),
                })
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
                     "binding."),
            "docked": s["docked"], "scored": s["scored"],
            "clean_poses": s["clean_poses"], "shown": len(poses),
            "best": s["best"], "verified": s.get("verified"),
            "best_verified": s.get("best_verified")})

    # What the 3D view highlights on MED23 for each shortlisted candidate. The
    # two layers are different kinds of claim and stay separate: `ligands` are
    # docked poses this project computed, `residues` would be a predicted
    # interface, and there is no ensemble inline, so that list is empty and the
    # panel says "not predicted" rather than borrowing the pocket.
    #
    # `poses` is one set of coordinates, docked into one box, and that box
    # belongs to exactly one candidate: the one whose ensemble supplied
    # `consensus`, or — when the box is the curated cavity instead — whoever
    # the curated entry was measured on (ELK1, not any of `genes`, unless the
    # run is asking about ELK1 itself). Attaching those poses to every
    # shortlisted gene said a compound was docked against each of their
    # interfaces when only one box was ever built.
    site_owner = (consensus_gene if consensus is not None
                 else CURATED_POCKETS.get(cfg.partner_gene, {}).get("partner"))
    for g in genes:
        hit = converged.get(g)
        residues = sorted(set(hit[1].get("partner_contact_residues", []))) if hit else []
        own_poses = poses if (screen and g == site_owner) else []
        note = ((f"{len(residues)} {cfg.partner_gene} residues, from an ensemble "
                 f"where {hit[0].dominant_cluster_samples}/{hit[0].total_samples} "
                 "samples agree. A prediction, not a contact.") if hit else
                (f"No interface between {g} and {cfg.partner_gene} has been "
                 "predicted, so nothing is highlighted for this candidate."))
        if screen and consensus is None:
            note += " The poses shown are docked into the ELK1 cavity."
        elif screen and site_owner and g != site_owner:
            note += (f" The site here was boxed for {site_owner}, not {g}; no "
                     "poses are shown for this candidate.")
        ensemble = predicted.get(g)
        emit("highlight", {
            "gene": g, "residues": residues,
            # The compounds belong to the box, and the box belongs to one gene.
            # `ligands` stays candidate-owned — a pose is only this candidate's
            # if this candidate's ensemble built the box it sits in. But the
            # screen still ran, on a cavity this receptor really has, and
            # dropping the result on the floor for every gene but the owner
            # left the stage note promising poses that nothing displayed.
            # They travel as the site's, with the owner named, so the interface
            # can show them without attributing them to whoever is selected.
            "site_ligands": poses if (screen and not own_poses) else [],
            "site_owner": site_owner,
            # Whether an ensemble has been run for this candidate at all, and
            # what it decided. Without this the panel can only offer to start
            # one, including for a candidate whose ensemble already ran and
            # refused — which is the state that makes the button look dead.
            "ensemble_status": ensemble[0].status if ensemble else None,
            "ensemble_support": (round(ensemble[0].ensemble_support, 2)
                                 if ensemble else None),
            "ensemble_samples": ensemble[0].total_samples if ensemble else None,
            "ensemble_blockers": list(ensemble[0].blockers) if ensemble else [],
            "ligands": own_poses,
            # The union of what the shown poses contact. Computed, not
            # predicted and not observed: these are the residues a compound
            # this project docked came within 4.5 A of.
            "ligand_residues": sorted({r for p in own_poses for r in p["residues"]}),
            # A contract with the UI, not decoration: it needs to tell a
            # candidate-specific prediction apart from the labelled ELK1
            # calibration surface without re-deriving `consensus is None`
            # itself, so the basis and the residues that made the box travel
            # with every highlight, not just the site stage's own event.
            "calibration": consensus is None,
            "site_basis": basis,
            "site_residues": site.residues if site else [],
            "note": note})



def run_live(question: str, data_paths, cfg, emit: Callable[[str, dict], None],
             interface_evidence: dict[str, MediatorLink] | None = None,
             top_n: int = 3, free_receptor: Path | None = None,
             predict: str | None = None, runtime: dict | None = None) -> dict:
    """Stream the real pipeline for one question. `emit(event, payload)` per step.

    Returns what the run leaves behind: the whole event stream with each event
    stamped when it was computed, plus the indexes a follow-up needs. The
    caller decides whether to keep it — a run that is never asked about again
    costs nothing extra for having been recorded, and `ui/serve.py` bounds how
    many are held at once.

    `runtime`, when given, is filled in place with the live objects a follow-up
    in the same process can reuse: the docking box this run built, the evidence
    dict its literature stage may have promoted a reading into, the agent trace
    the reasoning rail is rendering. They are handed over as a side effect
    rather than in the return value because they are the half that cannot be
    serialized, and the return value is the half that can.
    """
    ge_path, model_path, tf_path = data_paths
    interface_evidence = interface_evidence or {}
    emit = _Recorder(emit)
    if runtime is not None:
        emit.runtime = runtime
    emit.partner = cfg.partner_gene
    emit.runtime["interface_evidence"] = interface_evidence

    if not Path(ge_path).exists():
        emit("stage", {"id": "question", "state": "blocked",
                       "detail": f"{Path(ge_path).name} is not present",
                       "note": "see team/TASKS.md for the fetch command"})
        for sid in DOWNSTREAM:
            emit("stage", {"id": sid, "state": "pending", "detail": "not run"})
        emit("done", {"ok": False})
        return emit.state()

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
    emit.runtime["trace"] = trace
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
            return emit.state()
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
        return emit.state()

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
        return emit.state()

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
    # A question can name a disease and a gene at once, and this path used to
    # answer only the first: "in osteosarcoma, does RUNX2 touch MED23" ranked
    # 1538 TFs, shortlisted nobody, and never looked at RUNX2 — the one gene the
    # reader asked about by name. Named genes now ride along, marked as exactly
    # what they are. Nothing about the dependency gate moves: they are not
    # shortlisted, they do not pass, and the screen's own evidence gate is
    # untouched, so a named gene still cannot be docked into without a mapped
    # region or a converged ensemble.
    # The partner is the receptor this run screens against, not a candidate for
    # it. `_named_genes` returns it deliberately for the partner-first path
    # ("what binds MED23?"), and letting that through here put MED23 in the
    # candidate table as a transcription factor that failed its own dependency
    # gate — a claim the pipeline does not make and cannot support.
    asked = [g for g in _named_genes(question, tf_path, cfg.partner_gene)
             if g not in {v.gene for v in top}
             and g != cfg.partner_gene.upper()]
    by_gene = {v.gene: v for v in verdicts}
    downstream = [v.gene for v in top] + asked
    # The genes this run followed past the gate. A follow-up naming one of them
    # is asking about work already done; a follow-up naming any other gene the
    # scan measured is asking for the literature and structural halves only.
    emit.genes = list(downstream)
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
                    if near else "")
                 + (f". Also following {', '.join(asked)}, named in the question "
                    f"and not shortlisted: the gate in {m.context} does not pass "
                    f"{'them' if len(asked) > 1 else 'it'}, and the evidence "
                    "gates below decide what may be done with "
                    f"{'them' if len(asked) > 1 else 'it'}." if asked else ""))})

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
        for v in top] + [
        # The same numbers, from the same scan — this gene was measured like
        # every other one, it simply did not pass. Showing the measurement next
        # to the refusal is the honest form of "you asked about this one".
        {"gene": g, "context": (vv.context if (vv := by_gene.get(g)) else m.context),
         "level": vv.context_level if vv else m.level,
         "n": vv.n_target if vv else None,
         "median": round(vv.median_target, 3) if vv else None,
         "sel": round(vv.median_other - vv.median_target, 3) if vv else None,
         "tfrac": round(vv.target_dependent_fraction, 3) if vv else None,
         "ofrac": round(vv.other_dependent_fraction, 3) if vv else None,
         "q": vv.qvalue if vv else None,
         "route": vv.route if vv else "none",
         "gate_pass": False, "gate_why": [], "awaiting": vv is None,
         "shortlisted": False,
         "partner": cfg.partner_gene, "partner_is_query": True,
         "involvement": "unknown", "region": None, "region_mapped": False,
         "tractability": "unknown", "control": False, "concerns": [],
         "ready": False,
         "blocked_because": (f"named in the question, not shortlisted: it does "
                             f"not clear the dependency gate in {m.context}"
                             if vv else
                             "named in the question; not measured in this context"),
         "claims": []}
        for g in asked]})

    # ── literature: six axes per candidate, on-target only ─────────────────
    if not downstream:
        emit("stage", {"id": "literature", "state": "abstained",
                       "detail": "no candidate survived the gate to search evidence for",
                       "note": "Retrieval follows the gate; searching all "
                               f"{len(verdicts)} TFs would find something for "
                               "every one of them."})
    else:
        emit("stage", {"id": "literature", "state": "running",
                       "detail": (f"six evidence axes for {len(downstream)} "
                                  "candidate(s)")})
        total_on, total_axes, hits, leads, errors = 0, 0, 0, [], []
        read: dict[str, dict] = {}
        for gene in downstream:
            ev, papers, errs = gather(gene, m.context, cfg.partner_gene, per_axis=8)
            errors.extend(errs)
            for p in papers:
                emit("paper", {"title": p.title, "id": p.accession, "url": p.url,
                               "abstract": p.abstract, "axis": p.axis,
                               "gene": gene, "support": p.suggested_support})
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
                verdict_ev, _ = A.read_evidence(trace, gene, papers)
                emit("thinking", {"trace": trace.as_dict()})
                _promote_reading(gene, verdict_ev, papers, cfg,
                                 interface_evidence, emit)
            read[gene] = verdict_ev
            if verdict_ev.get("contact_documented") is True:
                leads.append(gene)

            emit("evidence", {"gene": gene, "axes": {
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

    _structure_site_and_screen(emit, cfg, downstream,
                               interface_evidence, free_receptor, predict,
                               policy["require_interface_site"], trace)

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
    return emit.state()


# ── follow-ups ─────────────────────────────────────────────────────────────
#
# A follow-up answers from what a completed run left behind. It lives under the
# same rule as the screening and structure stages above: a stage never reports a
# conclusion it did not compute this run. That is honoured by labelling rather
# than by recomputation — every replayed event carries when it was computed,
# every replayed stage note opens with that, and the answer names what was
# retained. Nothing here re-decides a gate; a gate's verdict travels with the
# state it was decided on, and the router refuses the cheap path outright for
# any question that could have moved the context out from under it.

def _retained(payload: dict, at: float, now: float) -> dict:
    """A copy of an event, stamped with when it was actually computed.

    `retained_at_epoch` survives the round trip on purpose. A follow-up on a
    follow-up re-records what it replays, and without the original stamp the age
    would reset on every replay until a twenty-minute-old number read as fresh —
    the failure this labelling exists to prevent, arriving by drift instead.
    """
    out = dict(payload)
    out["retained"] = True
    out["retained_at_epoch"] = at
    out["retained_at"] = S_.iso(at)
    out["retained_age_s"] = max(0, int(now - at))
    return out


def _retained_stage(payload: dict, at: float, now: float) -> dict:
    out = _retained(payload, at, now)
    out["note"] = (f"Retained from the run at {S_.iso(at)}, {S_.ago(now - at)} ago; "
                   "not recomputed for this question. "
                   + (payload.get("note") or "")).strip()
    return out


# Replaying these would answer the previous question again. `answer` and
# `summary` are written about one question and do not transfer to the next;
# `done` closes a stream this follow-up has not finished.
_REPLAY_SKIP = {"answer", "summary", "done", "session", "provenance"}


def _replay(emit, record: dict, now: float, *, skip_stages=(), skip_events=(),
            skip_genes=(), extra_rows=()) -> list[str]:
    """Re-emit a completed run's stream, labelled with when each part was computed.

    Returns the stage ids served from retained state, in order and once each —
    a stage emits `running` before `done` and the ledger should read as a list
    of stages, not of events — so the caller can say so out loud rather than
    leaving the reader to notice the timestamps.
    """
    served: dict[str, None] = {}
    for event, payload, at in record.get("events", []):
        if event in _REPLAY_SKIP or event in skip_events:
            continue
        if event == "stage":
            if payload.get("id") in skip_stages:
                continue
            served[payload["id"]] = None
            emit(event, _retained_stage(payload, at, now))
            continue
        if event in ("evidence", "highlight", "paper") and payload.get("gene") in skip_genes:
            continue
        if event == "candidates" and extra_rows:
            # The added rows carry the retained scan's own numbers for a gene it
            # measured and did not shortlist, so the merged event is retained in
            # full and keeps the scan's timestamp rather than this question's.
            have = {r.get("gene") for r in payload.get("rows", [])}
            payload = {**payload, "rows": list(payload.get("rows", []))
                       + [r for r in extra_rows if r.get("gene") not in have]}
        emit(event, _retained(payload, at, now))
    return list(served)


def _measured_row(record: dict, gene: str, partner: str) -> dict:
    """A candidate row for a gene the retained scan measured but did not follow.

    Same shape and same claim as the rows `run_live` emits for a gene named in
    the question: this gene was measured like every other one and did not clear
    the gate. Every number in it comes from the scan, so it rides the scan's
    timestamp through `_replay` rather than this question's.
    """
    m = (record.get("measured") or {}).get(gene) or {}
    return {"gene": gene, "context": record.get("context"),
            "level": record.get("level"),
            "n": m.get("n"), "median": m.get("median"), "sel": m.get("sel"),
            "tfrac": m.get("tfrac"), "ofrac": m.get("ofrac"),
            "q": m.get("q"), "route": m.get("route", "none"),
            "gate_pass": bool(m.get("pass")), "gate_why": list(m.get("why") or []),
            "awaiting": not m, "shortlisted": False,
            "partner": partner, "partner_is_query": True,
            "involvement": "unknown", "region": None, "region_mapped": False,
            "tractability": "unknown", "control": False, "concerns": [],
            "ready": False,
            "blocked_because": (
                "named in a follow-up; the retained scan measured it and did not "
                "shortlist it" if m else
                "named in a follow-up; not measured in this context"),
            "claims": []}


def _pct(x) -> str:
    return "—" if x is None else f"{x:.0%}"


def _num(x, places: int = 3) -> str:
    return "—" if x is None else f"{x:.{places}f}"


def _verdict_answer(record: dict, gene: str, now: float) -> str:
    """Why a gene passed or failed, written from the numbers the scan computed.

    Composed here rather than by the model on purpose. This is the follow-up
    whose answer most obviously already exists, and it should not stop working
    when there is no API key — nor should prose be generated over numbers when
    the numbers are themselves the answer.
    """
    m = (record.get("measured") or {}).get(gene) or {}
    at = float(record.get("scan_at") or record.get("computed_at") or now)
    context = record.get("context") or "this context"
    shortlisted = any(r.get("gene") == gene and r.get("shortlisted")
                      for r in record.get("rows") or [])
    if not m:
        return (f"{gene} does not appear in the retained scan of {context}, so this "
                "session has no verdict on it to quote. Asking without a session "
                "scans for it.")
    if m.get("pass"):
        head = (f"{gene} cleared the dependency gate in {context} via the "
                f"{m.get('route')} path"
                + (" and was shortlisted." if shortlisted else
                   ", but did not reach the top of the ranking, so nothing "
                   "downstream ran for it."))
    else:
        why = "; ".join(m.get("why") or []) or "it cleared neither path"
        head = (f"{gene} was measured in {context} and did not clear the dependency "
                f"gate: {why}.")
    numbers = (f"Median gene effect {_num(m.get('median'))} across {m.get('n')} "
               f"{context} models, with {_num(m.get('sel'))} of selectivity against "
               f"the rest of DepMap; {_pct(m.get('tfrac'))} of {context} models are "
               f"dependent against {_pct(m.get('ofrac'))} elsewhere; "
               f"q = {_num(m.get('q'), 2)}.")
    if m.get("low_n"):
        numbers += (" The cohort is below the confidence floor, so this verdict "
                    "carries low confidence.")
    return (head + "\n\n" + numbers + "\n\n"
            + f"Every number here was computed by the scan at {S_.iso(at)}, "
              f"{S_.ago(now - at)} ago. Nothing was re-run for this question and no "
              "gate was re-decided: this is that scan's verdict, quoted back.")


def _retained_caveat(record: dict, now: float, recomputed: list[str]) -> str:
    at = float(record.get("computed_at") or now)
    if recomputed:
        return (f"Answered from this session. {', '.join(recomputed)} ran just now; "
                f"everything else is retained from the run at {S_.iso(at)}, "
                f"{S_.ago(now - at)} ago, and is labelled retained on each stage.")
    return (f"Answered entirely from state the run at {S_.iso(at)} computed, "
            f"{S_.ago(now - at)} ago. No stage ran for this question.")


def _no_model_answer(record: dict, fu, now: float, recomputed: list[str]) -> str:
    """What this session holds, with no model available to phrase it.

    A run with no API key still computed everything a follow-up serves, and
    withholding it because prose is unavailable would hide state the reader is
    entitled to. The stage lines, quoted, with their provenance attached.
    """
    at = float(record.get("computed_at") or now)
    lines = [f"{sid}: {v.get('state')} — {v.get('detail')}"
             for sid, v in (record.get("stages") or {}).items()
             if v.get("state") not in (None, "pending")]
    return ((f"No model is configured, so this is the session's own record rather "
             f"than prose over it. {fu.reason}.") + "\n\n" + "\n".join(lines) + "\n\n"
            + f"Those lines were produced by the run at {S_.iso(at)}, "
              f"{S_.ago(now - at)} ago"
            + (f", except {', '.join(recomputed)}, which ran for this question."
               if recomputed else ", and none of them ran for this question."))


def _literature_for(emit, cfg, gene: str, context: str, interface_evidence: dict,
                    trace) -> None:
    """The evidence axes for one added gene, run now against the retained context.

    This is the half of a "what about <GENE>?" follow-up that genuinely has to
    run: nothing was ever retrieved for this gene. The scan behind the context,
    the gate, and every other candidate's evidence are untouched, which is what
    makes the follow-up cheap — one subprocess per axis for one gene, rather
    than the whole pipeline again.
    """
    emit("stage", {"id": "literature", "state": "running",
                   "detail": f"six evidence axes for {gene}",
                   "note": f"Added by a follow-up. The scan of {context or 'this run'} "
                           "and every other candidate's evidence are retained; only "
                           "this gene's retrieval is new."})
    ev, papers, errors = gather(gene, context, cfg.partner_gene, per_axis=8)
    for p in papers:
        emit("paper", {"title": p.title, "id": p.accession, "url": p.url,
                       "abstract": p.abstract, "axis": p.axis,
                       "gene": gene, "support": p.suggested_support})
    read = {"contact_documented": False, "support": "none", "note": "not assessed"}
    if A.available():
        read, _ = A.read_evidence(trace, gene, papers)
        emit("thinking", {"trace": trace.as_dict()})
        # Same carriage as the ranked path: a region read here reaches the
        # screen's gate the way a curated one does, and stays labelled as a
        # reading rather than as a file a person wrote.
        _promote_reading(gene, read, papers, cfg, interface_evidence, emit)
    emit("evidence", {"gene": gene, "axes": {
        a: {"on_target": r.n_on_target, "returned": r.n_papers,
            "note": r.note, "query": r.query} for a, r in ev.axes.items()},
        "read": read})
    if errors:
        emit("stage", {"id": "literature", "state": "blocked",
                       "detail": f"{len(errors)} axis search(es) failed for {gene}",
                       "note": "; ".join(errors[:2])})
        return
    emit("stage", {
        "id": "literature", "state": "done" if papers else "abstained",
        "detail": f"{len(papers)} on-target papers for {gene} across "
                  f"{len(ev.axes_with_hits)} axes with hits",
        "note": ("Retrieved for this question. Only papers naming the gene are "
                 "counted. "
                 + ("Read: " + (read.get("note") or "")[:200] if A.available()
                    else "No model is configured, so nothing was read."))})


def follow_up(question: str, record: dict, runtime: dict, fu, emit, cfg,
              free_receptor: Path | None = None, require_site: bool = False) -> dict:
    """Answer a follow-up, recomputing only the stages whose inputs changed.

    `fu` is a `sessions.FollowUp`, and its `recompute` list is the contract:
    every stage not in it is replayed from `record` under the timestamp of the
    run that computed it, and every stage in it runs for real against the same
    context, the same scan and the same evidence — which is the point. A
    question that could have moved the context never reaches here; the router
    sends that to a full run and says why.

    Returns the session's new record. Replayed events keep their original
    stamps and recomputed ones take this moment's, so the next follow-up still
    knows which half of the state is which.
    """
    emit = _Recorder(emit)
    now = time.time()
    at = float(record.get("computed_at") or now)
    recompute = set(fu.recompute)
    genes = list(record.get("genes") or [])
    trace = runtime.get("trace") or A.AgentTrace()
    interface_evidence = runtime.get("interface_evidence") or {}
    emit.partner = record.get("partner") or cfg.partner_gene
    emit.context, emit.level = record.get("context"), record.get("level")
    # Carried forward explicitly, not only via the replayed `policy` event: a
    # condition the first request set has to survive into the record this
    # follow-up returns, or the follow-up after it inherits a weaker gate.
    emit.require_site = bool(require_site)
    # The session's own dict, mutated in place: whatever the structural tail
    # rebuilds below has to be what the *next* follow-up reuses, not a copy of
    # what the last one saw.
    emit.runtime = runtime
    emit.runtime["trace"] = trace

    # The one stage that is always this run's own work. Matching an intent
    # happened now, and the reader has to see which intent matched — and
    # therefore what was skipped — before anything retained appears below it.
    emit("stage", {
        "id": "question", "state": "done", "detail": question,
        "context": record.get("context"), "level": record.get("level"),
        "follow_up": fu.kind,
        "note": (f"Follow-up: {fu.reason}. The context "
                 + (f"{record.get('context')} " if record.get("context") else "")
                 + f"was resolved by the run at {S_.iso(at)} and is not re-resolved "
                   "here. "
                 + (f"Recomputed for this question: {', '.join(sorted(recompute))}. "
                    if recompute else "Nothing was recomputed. ")
                 + "Every stage below that did not run says so, and says when it did.")})

    extra_rows: tuple = ()
    if fu.gene and fu.gene not in genes and fu.kind in ("gene", "predict"):
        extra_rows = (_measured_row(record, fu.gene, emit.partner),)
        genes = genes + [fu.gene]
    emit.genes = list(genes)

    # A recomputed structural tail re-emits every candidate's highlight, so the
    # retained ones are dropped rather than left to be overwritten out of order.
    skip_events = {"highlight"} if "structure" in recompute else set()
    skip_genes = {fu.gene} if fu.kind == "gene" else set()
    served = _replay(emit, record, now, skip_stages={"question"} | recompute,
                     skip_events=skip_events, skip_genes=skip_genes,
                     extra_rows=extra_rows)

    if "literature" in recompute and fu.gene:
        _literature_for(emit, cfg, fu.gene, record.get("context") or "",
                        interface_evidence, trace)
    if "structure" in recompute:
        # The same call a full run makes, with the same evidence gate. All a
        # follow-up saves here is the scan and the retrieval above it; nothing
        # downstream is allowed to be cheaper than it was.
        _structure_site_and_screen(
            emit, cfg, genes, interface_evidence, free_receptor,
            fu.gene if fu.kind == "predict" else None, require_site, trace)

    # The ledger for this question: which stages were served from state and
    # which ran. `serve.py` has already said *that* this is a follow-up; this
    # says what that cost and what it did not.
    emit("provenance", {"kind": fu.kind, "reason": fu.reason,
                        "retained_at": S_.iso(at), "retained_age_s": int(now - at),
                        "retained_stages": served,
                        "recomputed_stages": sorted(recompute)})

    if fu.kind == "verdict" and fu.gene:
        emit("answer", {"text": _verdict_answer(record, fu.gene, now), "model": "",
                        "caveat": _retained_caveat(record, now, sorted(recompute))})
    elif A.available():
        # The model gets the same record a full run hands it, plus provenance.
        # Rule 1 of its system prompt is that every number must appear in the
        # record; the provenance block is what stops it presenting those numbers
        # as this question's work.
        body = dict(emit.record())
        body["provenance"] = {
            "this_is_a_follow_up": True,
            "retained_from_run_at": S_.iso(at),
            "retained_age": S_.ago(now - at),
            "stages_recomputed_for_this_question": sorted(recompute) or None,
            "note": ("Stages not listed as recomputed did not run for this "
                     "question. Say so when you quote their numbers."),
        }
        text, _ = A.answer(trace, question, body)
        if text:
            emit("answer", {"text": text, "model": A.MODEL,
                            "caveat": _retained_caveat(record, now, sorted(recompute))})
    else:
        emit("answer", {"text": _no_model_answer(record, fu, now, sorted(recompute)),
                        "model": "",
                        "caveat": _retained_caveat(record, now, sorted(recompute))})

    emit("thinking", {"trace": trace.as_dict()})
    emit("done", {"ok": True})
    return emit.state()
