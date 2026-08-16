"""The interface can cause the file it reads to exist.

`pipeline_api` looked for `runs/interfaces/<GENE>_MED23/consensus.json` and
never triggered its creation, so the structure stage abstained forever no
matter what the backend could do. The bridge added in `_predict_interface`
closes that, and this is the test that it stays closed.

Boltz and Modal are mocked. What is being tested is the wiring — that an
explicit request reaches the predictor, that what the predictor writes is what
the structure stage reads, and that each way this can fail keeps the stage
abstaining rather than inventing residues. The GPU path itself is exercised by
`scripts/predict_med23_interface.py` against real dispatches; a unit test that
needed an H100 would not be run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "ui"))

import pipeline_api as P  # noqa: E402
from reagent_workflow.discovery_config import DiscoveryConfig  # noqa: E402


class Emitter:
    """Collects the SSE stream a run would have sent."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))

    def stages(self, sid: str) -> list[dict]:
        return [p for e, p in self.events if e == "stage" and p["id"] == sid]

    def final(self, sid: str) -> dict:
        got = self.stages(sid)
        assert got, f"no {sid} stage was emitted"
        return got[-1]

    def highlights(self) -> list[dict]:
        return [p for e, p in self.events if e == "highlight"]


CONSENSUS = {
    "target": "TESTTF",
    "partner": "MED23",
    "partner_contact_residues": [339, 343, 379, 382, 383],
    "consensus": {
        "interpretation": "computational_prediction",
        "status": "converged",
        "next_action": "build_search_site",
        "total_samples": 3,
        "dominant_cluster_samples": 3,
        "ensemble_support": 1.0,
        "target_segment": {"start": 10, "end": 24, "occupancy": {},
                           "contact_coverage": 1.0},
        "partner_contact_residues": [339, 343, 379, 382, 383],
        "blockers": [],
    },
}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point the module's paths at a scratch tree, so nothing reads real runs."""
    monkeypatch.setattr(P, "ROOT", tmp_path)
    monkeypatch.setattr(P, "SCREEN_FILE", tmp_path / "runs" / "vina_smoke.json")
    (tmp_path / "runs" / "interfaces").mkdir(parents=True)
    (tmp_path / "runs" / "screens").mkdir(parents=True)
    return tmp_path


def write_consensus(root: Path, gene: str, record: dict) -> Path:
    d = root / "runs" / "interfaces" / f"{gene}_MED23"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "consensus.json"
    p.write_text(json.dumps(record), encoding="utf-8")
    return p


def install_fake_predictor(monkeypatch, root: Path, *, gene: str,
                           record: dict | None, accession="P00001",
                           accession_note="P00001", boom: Exception | None = None):
    """Stand in for `scripts.predict_med23_interface` without importing it.

    The bridge imports the module by name at call time, so putting a fake in
    `sys.modules` intercepts exactly the seam the real code uses — no GPU, no
    network, and no second copy of the dispatch logic in the test.
    """
    import types

    mod = types.ModuleType("predict_med23_interface")
    calls: list[tuple] = []

    def accession_for(g):
        return (accession, accession_note)

    def dispatch(g, acc, samples=3, log=print, cfg=None):
        calls.append((g, acc, samples))
        log(f"seed 1 -> sample_1.json")
        if boom is not None:
            raise boom
        if record is not None:
            write_consensus(root, gene, record)
        return record

    mod.accession_for = accession_for
    mod.dispatch = dispatch
    monkeypatch.setitem(sys.modules, "predict_med23_interface", mod)
    return calls


def run_downstream(emit, gene: str, predict: str | None):
    P._structure_site_and_screen(
        emit, DiscoveryConfig(), [gene], {},
        ROOT / "downloads" / "9F76.cif", predict)


def test_request_triggers_dispatch_and_stage_consumes_the_file(workspace, monkeypatch):
    """The whole point: candidate -> dispatch -> consensus.json -> structure done."""
    calls = install_fake_predictor(monkeypatch, workspace, gene="TESTTF",
                                   record=CONSENSUS)
    emit = Emitter()
    run_downstream(emit, "TESTTF", predict="TESTTF")

    assert calls == [("TESTTF", "P00001", P.PREDICT_SAMPLES)], \
        "the bridge must call the existing predictor exactly once, with its own gene"
    assert (workspace / "runs" / "interfaces" / "TESTTF_MED23" /
            "consensus.json").is_file()

    structure = emit.final("structure")
    assert structure["state"] == "done", structure
    assert "3/3" in structure["detail"]

    # And the residues it agreed on reach the view.
    hl = emit.highlights()
    assert hl and hl[0]["gene"] == "TESTTF"
    assert hl[0]["residues"] == [339, 343, 379, 382, 383]

    # Progress was streamed while it ran, not only at the end.
    assert any(s["state"] == "running" for s in emit.stages("structure"))


def test_no_request_never_dispatches(workspace, monkeypatch):
    """A page load must not start a GPU job."""
    calls = install_fake_predictor(monkeypatch, workspace, gene="TESTTF",
                                   record=CONSENSUS)
    emit = Emitter()
    run_downstream(emit, "TESTTF", predict=None)

    assert calls == []
    assert emit.final("structure")["state"] == "abstained"
    assert emit.highlights()[0]["residues"] == []


def test_existing_consensus_is_not_recomputed(workspace, monkeypatch):
    """Asking the same question twice must not pay for the same ensemble twice."""
    write_consensus(workspace, "TESTTF", CONSENSUS)
    calls = install_fake_predictor(monkeypatch, workspace, gene="TESTTF",
                                   record=CONSENSUS)
    emit = Emitter()
    run_downstream(emit, "TESTTF", predict="TESTTF")

    assert calls == [], "a consensus already on disk must be read, not re-folded"
    assert emit.final("structure")["state"] == "done"


def test_failed_dispatch_abstains(workspace, monkeypatch):
    """A dead dispatch reports itself and highlights nothing."""
    install_fake_predictor(monkeypatch, workspace, gene="TESTTF", record=None,
                           boom=RuntimeError("modal said no"))
    emit = Emitter()
    run_downstream(emit, "TESTTF", predict="TESTTF")

    structure = emit.final("structure")
    assert structure["state"] in ("blocked", "abstained")
    assert "modal said no" in structure["note"]
    assert emit.highlights()[0]["residues"] == []


def unconverged(status: str, next_action: str) -> dict:
    rec = json.loads(json.dumps(CONSENSUS))
    rec["partner_contact_residues"] = []
    rec["consensus"].update(
        status=status, next_action=next_action, target_segment=None,
        dominant_cluster_samples=1, ensemble_support=1 / 3,
        blockers=["ensemble did not converge: dominant interface in 1/3 samples"])
    return rec


def test_ambiguous_ensemble_is_its_own_state(workspace, monkeypatch):
    """§10: ambiguous is not abstained.

    An ensemble that produced localized hypotheses and could not choose between
    them licenses a different next action from one that produced nothing —
    preserve them and sample more. Collapsing the two states loses the minority
    hypothesis that the ELK1 control showed a majority vote can bury.
    """
    install_fake_predictor(monkeypatch, workspace, gene="TESTTF",
                           record=unconverged("ambiguous", "sample_more"))
    emit = Emitter()
    run_downstream(emit, "TESTTF", predict="TESTTF")

    structure = emit.final("structure")
    assert structure["state"] == "ambiguous", structure
    assert "retained" in structure["detail"]
    assert "sample more" in structure["note"]
    # Kept, but never drawn and never docked.
    assert emit.highlights()[0]["residues"] == []


def test_refused_ensemble_abstains(workspace, monkeypatch):
    """§10: no defensible localized hypothesis stops, and says why."""
    install_fake_predictor(monkeypatch, workspace, gene="TESTTF",
                           record=unconverged("refused", "abstain"))
    emit = Emitter()
    run_downstream(emit, "TESTTF", predict="TESTTF")

    structure = emit.final("structure")
    assert structure["state"] == "abstained", structure
    assert "did not converge" in structure["detail"]
    assert emit.highlights()[0]["residues"] == []


def test_unknown_accession_abstains(workspace, monkeypatch):
    """A symbol that resolves to no protein is refused before anything is folded."""
    calls = install_fake_predictor(monkeypatch, workspace, gene="TESTTF",
                                   record=CONSENSUS, accession=None,
                                   accession_note="UniProt has no reviewed human entry")
    emit = Emitter()
    run_downstream(emit, "TESTTF", predict="TESTTF")

    assert calls == []
    structure = emit.final("structure")
    assert structure["state"] == "abstained"
    assert "no reviewed human entry" in structure["note"]
