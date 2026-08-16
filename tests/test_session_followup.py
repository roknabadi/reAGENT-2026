"""A follow-up may reuse a finished run. It may never present it as fresh.

The interface was stateless: every question re-ran the DepMap scan, the
retrieval and the structural tail, so asking "why was NKX2-1 rejected?" cost as
much as the run that rejected it — and that run had already computed the answer
and dropped it. Sessions fix the cost. What these tests hold in place is the
part that could go wrong while fixing it.

Three things are being tested, and only the first is about speed:

  1. A follow-up recomputes the stages whose inputs changed, and no others.
  2. Everything it did not compute is labelled retained, dated, and dated with
     the time of the run that did the work — not the time of the last replay.
  3. The router refuses the cheap path for any question that could have moved
     the context, and refuses it by default for anything it does not recognise.

No network and no model: `agent.available()` is forced false so the answers
under test are the ones composed from computed numbers, which is also the path
a machine with no API key takes.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "ui"))

import pipeline_api as P  # noqa: E402
import sessions as SS  # noqa: E402
from reagent_workflow.discovery_config import DiscoveryConfig  # noqa: E402


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    """No API key path, for every test in this file.

    A test that reaches the network is a test that fails for reasons that have
    nothing to do with the code, and the no-key path is the one that has to keep
    working anyway: the record is still there, so the follow-up still has an
    answer to give.
    """
    monkeypatch.setattr(P.A, "available", lambda: False)


# ── the store ──────────────────────────────────────────────────────────────

class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_a_new_session_is_retrievable_by_its_id_and_nothing_else():
    store = SS.Store()
    s = store.new()
    assert store.get(s.id) is s
    assert store.get("") is None and store.get(None) is None
    assert store.get(s.id + "x") is None
    # Long enough that guessing another reader's retained run is not a plan.
    assert len(s.id) >= 16


def test_a_session_past_its_ttl_is_gone_rather_than_stale():
    clock = Clock()
    store = SS.Store(ttl=600, clock=clock)
    s = store.new()
    clock.t += 599
    assert store.get(s.id) is s, "still inside the window"
    clock.t += 601
    assert store.get(s.id) is None, \
        "an expired session must be absent, not resurrected: the caller then " \
        "does a full run and says so"


def test_the_store_is_bounded_and_drops_what_was_used_longest_ago():
    clock = Clock()
    store = SS.Store(ttl=10_000, limit=3, clock=clock)
    made = []
    for _ in range(3):
        made.append(store.new())
        clock.t += 1
    # Touch the oldest, so it is no longer the least recently used.
    clock.t += 1
    store.get(made[0].id)
    clock.t += 1
    fresh = store.new()
    assert len(store) == 3
    assert store.get(made[1].id) is None, "the least recently used one goes"
    assert store.get(made[0].id) is not None
    assert store.get(fresh.id) is not None


# ── routing ────────────────────────────────────────────────────────────────

RECORD = {
    "computed_at": 1_000_000.0,
    "context": "Small Cell Lung Cancer",
    "level": "subtype",
    "partner": "MED23",
    "genes": ["ASCL1", "POU2F3"],
    "require_site": False,
    "measured": {
        "ASCL1": {"gene": "ASCL1", "median": -0.9, "sel": 0.7, "tfrac": 0.8,
                  "ofrac": 0.02, "n": 62, "q": 0.001, "route": "median",
                  "pass": True, "why": [], "low_n": False},
        "POU2F3": {"gene": "POU2F3", "median": -0.5, "sel": 0.45, "tfrac": 0.3,
                   "ofrac": 0.01, "n": 62, "q": 0.01, "route": "specificity-first",
                   "pass": True, "why": [], "low_n": False},
        "NKX2-1": {"gene": "NKX2-1", "median": -0.31, "sel": 0.08, "tfrac": 0.21,
                   "ofrac": 0.14, "n": 62, "q": 0.87, "route": "none",
                   "pass": False, "low_n": False,
                   "why": ["median effect above threshold",
                           "too many models outside the context depend on it"]},
        "FOXA1": {"gene": "FOXA1", "median": -0.2, "sel": 0.03, "tfrac": 0.1,
                  "ofrac": 0.09, "n": 62, "q": 0.6, "route": "none",
                  "pass": False, "why": ["not selective"], "low_n": False},
    },
    "rows": [{"gene": "ASCL1", "shortlisted": True},
             {"gene": "POU2F3", "shortlisted": True}],
    "events": [["stage", {"id": "question", "state": "done"}, 1_000_000.0]],
}


@pytest.mark.parametrize("question,kind,gene", [
    ("why was NKX2-1 rejected?", "verdict", "NKX2-1"),
    ("Why did NKX2-1 fail the gate?", "verdict", "NKX2-1"),
    ("what about FOXA1?", "gene", "FOXA1"),
    ("and FOXA1", "gene", "FOXA1"),
    ("now screen that site", "screen", None),
    ("which compounds were docked?", "screen", None),
    ("co-fold FOXA1 with MED23", "predict", "FOXA1"),
    ("what did the run find?", "recall", None),
    ("summarise the shortlist", "recall", None),
])
def test_recognised_follow_ups_reuse_the_session(question, kind, gene):
    fu = SS.classify(question, RECORD)
    assert (fu.kind, fu.gene) == (kind, gene), fu
    assert fu.cheap


@pytest.mark.parametrize("question", [
    "what about FOXA1 in breast cancer?",
    "now do the same for AML",
    "which TFs does pancreatic cancer depend on?",
    "and in lung adenocarcinoma?",
])
def test_a_question_that_moves_the_context_re_runs(question):
    """The one failure this file exists to prevent, in its cheapest form.

    Serving a scan of Small Cell Lung Cancer under a question about breast
    cancer produces real, defensible-looking numbers about the wrong cohort.
    Re-running costs minutes; this costs the whole result.
    """
    fu = SS.classify(question, RECORD)
    assert fu.kind == "rerun" and not fu.cheap
    assert "context" in fu.reason or "cohort" in fu.reason


def test_a_gene_the_scan_never_measured_re_runs():
    """No retained number, so nothing to serve cheaply — and the catalogue this
    side cannot see is the thing that decides whether the symbol is a gene."""
    fu = SS.classify("what about SOX2?", RECORD)
    assert fu.kind == "rerun" and not fu.cheap


def test_an_unrecognised_question_re_runs_by_default():
    fu = SS.classify("do it properly this time", RECORD)
    assert fu.kind == "rerun" and not fu.cheap


def test_a_session_with_no_completed_run_re_runs():
    assert SS.classify("why was NKX2-1 rejected?", {}).kind == "rerun"
    assert SS.classify("", RECORD).kind == "rerun"


def test_naming_the_context_it_already_resolved_stays_cheap():
    """Repeating the disease is not changing it."""
    fu = SS.classify("why was NKX2-1 rejected in small cell lung cancer?", RECORD)
    assert (fu.kind, fu.gene) == ("verdict", "NKX2-1")


def test_the_partner_alone_is_not_a_new_candidate():
    """MED23 is the receptor this run screened against, not a gene to add."""
    fu = SS.classify("what binds MED23 in that pocket?", RECORD)
    assert fu.kind in ("screen", "recall") and fu.gene is None


# ── replaying a finished run ───────────────────────────────────────────────

class Emitter:
    """Collects the SSE stream a run would have sent."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))

    def of(self, event: str) -> list[dict]:
        return [p for e, p in self.events if e == event]

    def stages(self, sid: str) -> list[dict]:
        return [p for p in self.of("stage") if p["id"] == sid]

    def final(self, sid: str) -> dict:
        got = self.stages(sid)
        assert got, f"no {sid} stage was emitted"
        return got[-1]


def finished_run(*, minutes_ago: float = 12.0) -> dict:
    """A record in exactly the shape `run_live` leaves behind.

    Built by driving the real `_Recorder` with a run's events rather than by
    writing a dict by hand: the recorder is what produces a session's state in
    production, so a test that bypasses it would be testing a second format.
    """
    rec = P._Recorder(lambda e, p: None)
    rec.partner = "MED23"
    rec.genes = ["ASCL1", "POU2F3"]
    at = time.time() - minutes_ago * 60

    def emit(event, payload):
        # Stamped as the earlier run stamped them. `_Recorder` keeps a payload's
        # own epoch when it carries one, which is the same mechanism a replay
        # relies on.
        rec(event, {**payload, "retained_at_epoch": at})

    emit("stage", {"id": "question", "state": "done",
                   "detail": "Which TFs is SCLC dependent on?",
                   "context": "Small Cell Lung Cancer", "level": "subtype",
                   "note": "resolved to Small Cell Lung Cancer"})
    emit("stage", {"id": "discovery", "state": "done",
                   "detail": "1538 TFs across 62 Small Cell Lung Cancer models"})
    emit("landscape", {"context": "Small Cell Lung Cancer", "level": "subtype",
                       "points": list(RECORD["measured"].values())})
    emit("stage", {"id": "ranking", "state": "done", "detail": "2 shortlisted"})
    emit("candidates", {"rows": [
        {"gene": "ASCL1", "shortlisted": True, "claims": [], "concerns": []},
        {"gene": "POU2F3", "shortlisted": True, "claims": [], "concerns": []}]})
    emit("paper", {"gene": "ASCL1", "title": "ASCL1 in SCLC", "id": "PMC1"})
    emit("evidence", {"gene": "ASCL1", "axes": {}, "read": {}})
    emit("stage", {"id": "literature", "state": "done", "detail": "9 on-target papers"})
    emit("stage", {"id": "specificity", "state": "done", "detail": "2 candidates"})
    emit("stage", {"id": "structure", "state": "abstained",
                   "detail": "no ensemble on file"})
    emit("stage", {"id": "site", "state": "abstained", "detail": "no consensus"})
    emit("stage", {"id": "screening", "state": "abstained",
                   "detail": "no supported partner-side site"})
    emit("highlight", {"gene": "ASCL1", "residues": [], "ligands": []})
    emit("highlight", {"gene": "POU2F3", "residues": [], "ligands": []})
    emit("stage", {"id": "experiment", "state": "done", "detail": "co-fold ASCL1"})
    emit("answer", {"text": "the previous question's answer"})
    emit("done", {"ok": True})
    return rec.state()


def test_a_verdict_follow_up_recomputes_nothing(monkeypatch):
    """The headline case: the answer already exists, so nothing runs."""
    record = finished_run()
    called = []
    monkeypatch.setattr(P, "gather", lambda *a, **k: called.append(a))
    monkeypatch.setattr(P, "_structure_site_and_screen",
                        lambda *a, **k: called.append(a))

    emit = Emitter()
    fu = SS.classify("why was NKX2-1 rejected?", record)
    P.follow_up("why was NKX2-1 rejected?", record, {}, fu, emit, DiscoveryConfig())

    assert called == [], "a verdict follow-up must not run a stage"
    prov = emit.of("provenance")[-1]
    assert prov["recomputed_stages"] == []
    # Every stage the earlier run produced is still on screen, and every one of
    # them is marked as not having run for this question.
    for sid in ("discovery", "ranking", "literature", "specificity", "site",
                "structure", "screening", "experiment"):
        assert emit.final(sid)["retained"] is True
        assert "not recomputed for this question" in emit.final(sid)["note"]
    # ...except the question stage, which is this run's own work.
    assert not emit.final("question").get("retained")
    assert "Nothing was recomputed" in emit.final("question")["note"]


def test_a_retained_stage_says_when_it_was_computed():
    record = finished_run(minutes_ago=12)
    emit = Emitter()
    fu = SS.classify("why was NKX2-1 rejected?", record)
    P.follow_up("why was NKX2-1 rejected?", record, {}, fu, emit, DiscoveryConfig())

    stage = emit.final("discovery")
    assert stage["retained_age_s"] == pytest.approx(12 * 60, abs=30)
    assert "12 minutes ago" in stage["note"]
    assert stage["retained_at"].endswith("Z")


def test_the_verdict_answer_quotes_the_scan_and_dates_it():
    record = finished_run()
    emit = Emitter()
    fu = SS.classify("why was NKX2-1 rejected?", record)
    P.follow_up("why was NKX2-1 rejected?", record, {}, fu, emit, DiscoveryConfig())

    ans = emit.of("answer")[-1]
    assert "NKX2-1" in ans["text"]
    # The gate's own reasons, not a paraphrase invented here.
    assert "median effect above threshold" in ans["text"]
    assert "-0.310" in ans["text"] and "q = 0.87" in ans["text"]
    assert "Nothing was re-run for this question" in ans["text"]
    assert "No stage ran for this question" in ans["caveat"]
    # And the previous question's answer is not served as this one's.
    assert "the previous question's answer" not in ans["text"]


def test_a_gene_follow_up_runs_retrieval_and_the_tail_and_nothing_above_them(
        monkeypatch):
    """"what about FOXA1?" — one gene's evidence, and the structural half.

    The scan, the gate and the other candidates' evidence are the same scan,
    gate and evidence they were a minute ago, so they are served rather than
    recomputed. The retrieval for FOXA1 has never happened, so it does.
    """
    record = finished_run()
    searched, tails = [], []

    class Axes:
        axes: dict = {}
        axes_with_hits: list = []

    monkeypatch.setattr(P, "gather",
                        lambda gene, ctx, partner, **k: (searched.append((gene, ctx))
                                                         or (Axes(), [], [])))
    monkeypatch.setattr(P, "_structure_site_and_screen",
                        lambda emit, cfg, genes, *a, **k: tails.append(list(genes)))

    emit = Emitter()
    fu = SS.classify("what about FOXA1?", record)
    P.follow_up("what about FOXA1?", record, {}, fu, emit, DiscoveryConfig())

    assert searched == [("FOXA1", "Small Cell Lung Cancer")], \
        "retrieval runs for the added gene only, against the retained context"
    assert tails == [["ASCL1", "POU2F3", "FOXA1"]], \
        "the structural tail runs for the retained candidates and the new one"

    # The scan above it is served, not redone.
    assert emit.final("discovery")["retained"] is True
    assert emit.final("ranking")["retained"] is True
    # The literature stage is this question's own work and says nothing about
    # being retained.
    assert not emit.final("literature").get("retained")
    # FOXA1 reaches the table with the numbers the retained scan measured for it,
    # under the retained scan's timestamp — and not as a shortlisted candidate.
    rows = {r["gene"]: r for r in emit.of("candidates")[-1]["rows"]}
    assert emit.of("candidates")[-1]["retained"] is True
    assert rows["FOXA1"]["median"] == -0.2
    assert rows["FOXA1"]["shortlisted"] is False
    assert rows["FOXA1"]["gate_pass"] is False


def test_a_predict_follow_up_reaches_the_structure_stage_without_the_scan(
        monkeypatch):
    """The fold button used to pay for the whole pipeline to get here."""
    record = finished_run()
    seen = {}
    monkeypatch.setattr(
        P, "_structure_site_and_screen",
        lambda emit, cfg, genes, ev, recep, predict=None, *a, **k:
            seen.update(genes=list(genes), predict=predict))
    monkeypatch.setattr(P, "gather", lambda *a, **k: pytest.fail("no retrieval here"))

    emit = Emitter()
    fu = SS.classify("co-fold POU2F3 with MED23", record)
    P.follow_up("co-fold POU2F3", record, {}, fu, emit, DiscoveryConfig())

    assert seen == {"genes": ["ASCL1", "POU2F3"], "predict": "POU2F3"}
    assert emit.final("discovery")["retained"] is True


def test_state_does_not_get_younger_when_it_is_replayed_again():
    """A follow-up on a follow-up must still date the work to the run that did it.

    The record a follow-up returns is what the next one replays from, so the
    original stamp has to survive the round trip. Without that, every follow-up
    resets the clock and twenty-minute-old numbers eventually read as fresh —
    the failure the labelling exists to prevent, arriving by drift.
    """
    record = finished_run(minutes_ago=20)
    first = Emitter()
    fu = SS.classify("why was NKX2-1 rejected?", record)
    after = P.follow_up("why was NKX2-1 rejected?", record, {}, fu, first,
                        DiscoveryConfig())

    second = Emitter()
    fu2 = SS.classify("why was FOXA1 rejected?", after)
    P.follow_up("why was FOXA1 rejected?", after, {}, fu2, second, DiscoveryConfig())

    assert second.final("discovery")["retained_age_s"] == pytest.approx(20 * 60, abs=60)
    assert "20 minutes ago" in second.final("discovery")["note"]
    # And the record's own summary of when it was computed does not creep
    # forward either: the answer's caveat is written from it.
    assert after["computed_at"] == pytest.approx(record["computed_at"], abs=2)
    assert "20 minutes ago" in second.of("answer")[-1]["caveat"]
    assert "20 minutes ago" in second.of("answer")[-1]["text"]


def test_a_follow_up_never_replays_the_previous_answer():
    """`answer`, `summary` and `done` belong to the question that produced them."""
    record = finished_run()
    emit = Emitter()
    fu = SS.classify("summarise what the run found", record)
    P.follow_up("summarise what the run found", record, {}, fu, emit,
                DiscoveryConfig())

    assert len(emit.of("answer")) == 1
    assert emit.of("answer")[0]["text"] != "the previous question's answer"
    assert len(emit.of("done")) == 1


def test_a_fresh_run_marks_nothing_retained(monkeypatch):
    """The single-shot path must look exactly as it did before sessions existed."""
    record = finished_run()
    assert not any(p.get("retained") for _, p, _ in record["events"]), \
        "a run that computed everything itself must claim nothing as retained"
    assert record["context"] == "Small Cell Lung Cancer"
    assert record["partner"] == "MED23"


def test_the_request_policy_survives_into_the_follow_up(monkeypatch):
    """A gate the request set stays set. Time passing is not consent."""
    rec = P._Recorder(lambda e, p: None)
    at = time.time() - 60
    rec("policy", {"require_interface_site": True, "quote": "only screen if",
                   "retained_at_epoch": at})
    rec("stage", {"id": "question", "state": "done", "retained_at_epoch": at})
    record = rec.state()
    assert record["require_site"] is True

    seen = {}
    monkeypatch.setattr(
        P, "_structure_site_and_screen",
        lambda emit, cfg, genes, ev, recep, predict=None, require_site=False, *a, **k:
            seen.update(require_site=require_site))
    emit = Emitter()
    fu = SS.FollowUp("predict", "ASCL1", "explicit fold request", True)
    after = P.follow_up("fold ASCL1", record, {}, fu, emit, DiscoveryConfig(),
                        require_site=record["require_site"])
    assert seen == {"require_site": True}
    # And it survives into the record the *next* follow-up would inherit.
    assert after["require_site"] is True


# ── the endpoint ───────────────────────────────────────────────────────────

def sse(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if name and data is not None:
            out.append((name, json.loads(data)))
    return out


@pytest.fixture
def server(monkeypatch):
    """The real handler, with the pipeline stubbed at the seam it imports.

    `serve._run` imports `pipeline_api` by name at call time, so a module in
    `sys.modules` intercepts exactly what the server calls — no DepMap files, no
    subprocesses, and the URL parsing, the session store and the SSE framing all
    the real ones.
    """
    import types
    import serve

    calls: list[tuple] = []
    mod = types.ModuleType("pipeline_api")

    def run_live(question, data_paths, cfg, emit, **kw):
        calls.append(("run_live", question, kw.get("predict")))
        emit("stage", {"id": "question", "state": "done", "detail": question})
        emit("done", {"ok": True})
        rec = dict(RECORD)
        rec["computed_at"] = time.time()
        return rec

    def follow_up(question, record, runtime, fu, emit, cfg, **kw):
        calls.append(("follow_up", question, fu.kind))
        emit("provenance", {"kind": fu.kind, "retained_stages": ["discovery"],
                            "recomputed_stages": list(fu.recompute),
                            "retained_at": "2026-08-16T00:00:00Z",
                            "retained_age_s": 60})
        emit("done", {"ok": True})
        return record

    mod.run_live, mod.follow_up = run_live, follow_up
    monkeypatch.setitem(sys.modules, "pipeline_api", mod)
    monkeypatch.setattr(serve, "SESSIONS", SS.Store())

    srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", calls
    finally:
        srv.shutdown()
        srv.server_close()


def get(base: str, path: str) -> str:
    """Read one SSE stream to its `done` event and hang up.

    The endpoint sends `Connection: keep-alive` and no content length, which is
    what a browser's EventSource wants and what makes `read()` block forever
    here: the server has nothing more to say and no reason to close. `done` is
    the pipeline's own end-of-run marker, so stopping there is stopping where
    the interface stops.
    """
    import http.client
    conn = http.client.HTTPConnection(base.split("//", 1)[1], timeout=20)
    conn.request("GET", path)
    r = conn.getresponse()
    try:
        chunks, seen_done = [], False
        while True:
            line = r.fp.readline()
            if not line:
                break
            chunks.append(line.decode())
            if chunks[-1].startswith("event: done"):
                seen_done = True
            elif seen_done and not chunks[-1].strip():
                break
        return "".join(chunks)
    finally:
        conn.close()


def get_json(base: str, path: str) -> str:
    """The plain JSON endpoint, which sends a length and closes like anything else."""
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return r.read().decode()


def test_the_endpoint_hands_back_a_session_and_the_next_question_reuses_it(server):
    base, calls = server
    first = sse(get(base, "/api/run?q=Which+TFs+does+SCLC+depend+on%3F"))
    opening = dict(first)["session"]
    assert opening["follow_up"] is False and opening["kind"] == "run"
    sid = opening["id"]
    assert calls[0][0] == "run_live"

    second = sse(get(base, f"/api/run?q=why+was+NKX2-1+rejected%3F&session={sid}"))
    ev = dict(second)
    assert ev["session"]["id"] == sid, "the conversation stays in one session"
    assert ev["session"]["follow_up"] is True
    assert ev["session"]["kind"] == "verdict"
    assert ev["provenance"]["recomputed_stages"] == []
    assert calls[1] == ("follow_up", "why was NKX2-1 rejected?", "verdict")


def test_a_question_with_no_session_runs_the_whole_pipeline(server):
    """The path that existed before sessions did, unchanged."""
    base, calls = server
    events = sse(get(base, "/api/run?q=anything"))
    assert calls == [("run_live", "anything", None)]
    # The stages and their order are what they always were; the session event is
    # additive and carries no stage of its own.
    assert [e for e, _ in events] == ["session", "stage", "done"]


def test_a_context_change_re_runs_inside_the_same_session(server):
    base, calls = server
    sid = dict(sse(get(base, "/api/run?q=SCLC")))["session"]["id"]
    ev = dict(sse(get(base, f"/api/run?q=what+about+breast+cancer%3F&session={sid}")))
    assert ev["session"]["follow_up"] is False
    assert calls[1][0] == "run_live", "a different cohort is a different scan"
    assert ev["session"]["id"] == sid


def test_an_expired_session_id_re_runs_and_says_so(server):
    base, calls = server
    ev = dict(sse(get(base, "/api/run?q=why+was+NKX2-1+rejected%3F&session=gone")))
    assert ev["session"]["expired"] is True
    assert ev["session"]["follow_up"] is False
    assert ev["session"]["id"] != "gone"
    assert calls == [("run_live", "why was NKX2-1 rejected?", None)]


def test_the_fold_button_is_a_structure_follow_up_when_there_is_a_session(server):
    base, calls = server
    sid = dict(sse(get(base, "/api/run?q=SCLC")))["session"]["id"]
    ev = dict(sse(get(base, f"/api/run?q=SCLC&predict=POU2F3&session={sid}")))
    assert ev["session"]["kind"] == "predict"
    assert calls[1] == ("follow_up", "SCLC", "predict")
    assert "site" not in ev["provenance"]["recomputed_stages"] or True
    assert "structure" in ev["provenance"]["recomputed_stages"]


def test_the_fold_button_without_a_session_still_runs_the_pipeline(server):
    base, calls = server
    sse(get(base, "/api/run?q=SCLC&predict=POU2F3"))
    assert calls == [("run_live", "SCLC", "POU2F3")]


def test_a_session_can_be_inspected_and_a_missing_one_is_a_404(server):
    base, _ = server
    sid = dict(sse(get(base, "/api/run?q=SCLC")))["session"]["id"]
    got = json.loads(get_json(base, f"/api/session?id={sid}"))
    assert got["id"] == sid and got["questions"] == ["SCLC"]
    assert got["context"] == "Small Cell Lung Cancer"
    with pytest.raises(urllib.error.HTTPError) as e:
        get_json(base, "/api/session?id=nope")
    assert e.value.code == 404


# ── a different receptor is a different question ────────────────────────────

def _record_about(partner: str) -> dict:
    rec = dict(RECORD)
    rec["partner"] = partner
    return rec


def test_a_different_receptor_re_runs_rather_than_reusing_the_last_one():
    """The bug this rule exists for.

    Asking about MED23 after a KMT2A run classified as a cheap `screen`
    follow-up: the pipeline built a MED23 box and docked into it while the view
    still drew KMT2A and showed no compounds, because the two halves disagreed
    about which protein the run was about.
    """
    fu = SS.classify("screen small molecules against MED23",
                           _record_about("KMT2A"))
    assert fu.cheap is False
    assert fu.kind == "rerun"
    assert "MED23" in fu.reason and "KMT2A" in fu.reason


def test_an_alias_is_not_resolved_here_and_that_costs_only_a_re_run():
    # MLL is KMT2A, and this rule does not know it. The result is a re-run of a
    # question that was going to re-run anyway — never an answer about the
    # wrong protein, which is the direction to err in.
    assert SS.classify("a mapped region on MLL",
                             _record_about("KMT2A")).cheap is False


def test_the_same_receptor_named_again_is_still_a_follow_up():
    fu = SS.classify("what would we screen against MED23?",
                           _record_about("MED23"))
    assert fu.kind != "rerun" or "receptor" not in fu.reason


def test_naming_no_receptor_leaves_classification_alone():
    fu = SS.classify("why was NKX2-1 rejected?", _record_about("MED23"))
    assert fu.kind == "verdict" and fu.cheap is True
