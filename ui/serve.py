#!/usr/bin/env python
"""Static server + a live pipeline endpoint, stdlib only.

    python ui/serve.py            # http://localhost:8931

The page works without this — it falls back to the recorded run in data.json and
says so. With it, the query bar runs the real pipeline: Paperclip searches the
corpus, the gates run on real DepMap numbers, and each stage streams its own
result as it completes rather than appearing all at once at the end.

Nothing here fabricates a stage. A stage that cannot run reports that it could
not run, which is the whole point of the interface.

A run also leaves a session behind, and a follow-up question can be answered
from it. `?q=…` alone is exactly what it always was — one question, one full
run — and now returns a session id as well; `?q=…&session=<id>` is a follow-up,
which reuses whatever the earlier run computed and re-runs only the stages
whose inputs changed. Retained state is always labelled as retained and dated;
see `ui/sessions.py` for why that labelling is the whole of the design.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

UI = Path(__file__).resolve().parent
ROOT = UI.parent


def _load_env(path: Path = None) -> list[str]:
    """Read `.env` into the environment, without overwriting what is already set.

    `activate.sh` does this, and for a while it was the only thing that did — so
    `python ui/serve.py` started a server with no ANTHROPIC_API_KEY, every
    literature stage stopped at "Retrieved, not read", no abstract was ever
    assessed for a documented contact, and the Reasoning tab stayed empty. None
    of that looked like a missing credential from the page: it looked like the
    pipeline had read the papers and found nothing.

    Existing environment wins, so an operator who exported a different key in
    their shell keeps it. Values are taken literally apart from surrounding
    quotes; this is a dotenv reader for our own file, not a shell.
    """
    path = path or ROOT / ".env"
    loaded: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return loaded
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[key] = value
        loaded.append(key)
    return loaded


_ENV_LOADED = _load_env()
if str(UI) not in sys.path:
    sys.path.insert(0, str(UI))
import sessions  # noqa: E402  (needs UI on the path; stdlib-only, like this file)

PAPERCLIP = shutil.which("paperclip") or str(ROOT / ".venv/bin/paperclip")
# data.json is the cold-start view only. A run computes its own numbers.
DATA = json.loads((UI / "data.json").read_text(encoding="utf-8"))
# One store for the process. Bounded by count and by age, both in sessions.py:
# this server is left open for hours at a time and a session holds a whole run.
SESSIONS = sessions.Store()


def _parse_search(stdout: str) -> list[dict]:
    """Pull papers out of `paperclip search` output.

    Kept separate from the subprocess call so it can be tested without the CLI.
    Parse defensively: this format has already changed once — the metadata line
    now carries the journal where it used to read PMC — and the previous parser
    matched that literal, dropped every paper, and reported "nothing found".
    On screen that is indistinguishable from a real null result.
    """
    ID = re.compile(r"\b((?:PMC|bio_|med_|arx_|tri_|fda_)[\w.]+)\b")
    papers, cur = [], None
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\.\s+(.*)$", line)
        if m:
            if cur:
                papers.append(cur)
            cur = {"title": m.group(2), "id": "", "url": "", "abstract": ""}
            continue
        if cur is None:
            continue
        if line.startswith("http"):
            if not cur["url"]:
                cur["url"] = line
        elif line.startswith('"'):
            cur["abstract"] = line.strip('"')
        elif not cur["id"] and (hit := ID.search(line)):
            cur["id"] = hit.group(1)
    if cur:
        papers.append(cur)
    # An id-less hit is still a real retrieval; only a title is required.
    return [p for p in papers if p.get("title")]


def paperclip_search(query: str, n: int = 6) -> list[dict]:
    """Real Paperclip call. Returns [] and lets the caller report the failure."""
    try:
        out = subprocess.run(
            [PAPERCLIP, "search", "-s", "pmc", query, "-n", str(n)],
            capture_output=True, text=True, timeout=90)
    except Exception:
        return []
    return _parse_search(out.stdout)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(UI), **kw)

    def log_message(self, *a):  # quiet
        pass

    def end_headers(self):
        # Revalidate every static file. Rebuilding med23.json and reloading the
        # page showed the old one: the browser had a copy, the server never
        # said not to trust it, and the panel quietly described a structure
        # file that had been replaced. On a dev server serving a megabyte over
        # loopback, always asking is the right trade.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _sse(self, event: str, payload: dict) -> None:
        self.wfile.write(f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode())
        self.wfile.flush()

    def _json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/api/session":
            # What a session is holding, without running anything. Useful from a
            # console, and it is also how expiry is observable: a session past
            # its TTL is gone here before it is silently gone from a follow-up.
            s = SESSIONS.get((parse_qs(url.query).get("id") or [""])[0].strip())
            return self._json(s.summary() if s else
                              {"error": "no such session, or it expired"},
                              200 if s else 404)
        if url.path != "/api/run":
            return super().do_GET()

        params = parse_qs(url.query)
        q = (params.get("q") or [""])[0].strip()
        # An explicit request to fold one candidate on GPU during this run. It
        # is a query parameter rather than a setting because it is a decision
        # someone makes per run and pays for per run; nothing here starts a
        # dispatch on its own.
        predict = (params.get("predict") or [""])[0].strip().upper() or None
        # A completed run to build this question on. Absent, this is the
        # single-shot path it has always been.
        sid = (params.get("session") or [""])[0].strip() or None
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            self._run(q, predict, sid)
        except (BrokenPipeError, ConnectionResetError):
            pass   # the reader navigated away

    def _run(self, q: str, predict: str | None = None,
             sid: str | None = None) -> None:
        """Delegate to the real pipeline, or to a follow-up on a finished run.

        Nothing in a full run is precomputed: the landscape, the gates and the
        candidate table are all computed for the context resolved from this
        question. A follow-up reuses that work rather than redoing it, and
        `pipeline_api` labels every piece it reuses with when it was computed.
        Which of the two happened is the first thing the stream says.
        """
        import sys
        if str(UI) not in sys.path:
            sys.path.insert(0, str(UI))
        import pipeline_api
        from reagent_workflow.discovery_config import DiscoveryConfig
        from dependency_scout.models import MediatorLink

        evidence = {}
        for f in sorted((ROOT / "examples").glob("*_link_*.json")):
            gene = f.stem.split("_link_")[1].split("_")[0].upper()
            try:
                evidence[gene] = MediatorLink.model_validate_json(
                    f.read_text(encoding="utf-8"))
            except Exception:
                continue

        # The release the canonical gate is written against. Falling back to the
        # older files would answer with 24Q2 numbers under a 24Q4 label, so the
        # directory is named rather than searched for.
        D = ROOT / "downloads"
        release = D / "24Q4"
        cfg = DiscoveryConfig()

        session = SESSIONS.get(sid)
        # An id that names nothing is not a reason to answer from nothing. The
        # session either expired or never existed; either way this question gets
        # a real run, in a new session, and the stream says which happened
        # rather than leaving the page to wonder why its follow-up went slow.
        expired = bool(sid) and session is None
        if session is None:
            session = SESSIONS.new()

        # Classification reads the record that the run below replaces, so both
        # happen under the session's lock. Otherwise a second tab can decide
        # "this is a cheap follow-up" against a record that is halfway through
        # being rewritten by the first one's scan.
        with session.lock:
            fu = None
            if session.record:
                fu = (sessions.FollowUp(
                          "predict", predict,
                          f"an explicit request to fold {predict}, which needs the "
                          "structural tail and nothing above it", True)
                      # The fold button names one candidate on a run that already
                      # happened. Reading that back out of free text would be a
                      # worse way to learn something the caller stated outright.
                      if predict else sessions.classify(q, session.record))
            session.questions.append(q)

            self._sse("session", {
                "id": session.id,
                "follow_up": bool(fu and fu.cheap),
                "kind": fu.kind if fu else "run",
                "reason": (fu.reason if fu else
                           "the session expired, so this question is a full run in a "
                           "new one" if expired else
                           "a new session" if not sid else
                           "this session holds no completed run to build on"),
                "expired": expired,
                "questions": list(session.questions),
                "recomputed_stages": list(fu.recompute) if fu and fu.cheap else None,
            })

            try:
                if fu is not None and fu.cheap:
                    session.record = pipeline_api.follow_up(
                        q, session.record, session.runtime, fu, self._sse, cfg,
                        free_receptor=D / "9F76.cif",
                        # A condition the first request placed on the pipeline is
                        # still in force for the session. A follow-up that dropped
                        # it would run a screen the original request forbade, which
                        # is a gate weakened by nothing but the passage of time.
                        require_site=bool(session.record.get("require_site")))
                else:
                    # A full run replaces the session's state rather than adding
                    # to it: this question resolved its own context, and merging
                    # a new scan into an old one is how a table ends up holding
                    # two cohorts' numbers under one heading.
                    #
                    # Both halves are swapped in together, after the run
                    # returns. A run that raises leaves the session exactly as
                    # it was — a record whose live objects had already been
                    # thrown out is a session that half-remembers one run and
                    # half-remembers another.
                    fresh: dict = {}
                    session.record = pipeline_api.run_live(
                        q,
                        (release / "CRISPRGeneEffect.csv", release / "Model.csv",
                         D / "lambert_tfs.csv"),
                        cfg,
                        self._sse,
                        interface_evidence=evidence,
                        free_receptor=D / "9F76.cif",
                        predict=predict,
                        runtime=fresh,
                    )
                    session.runtime = fresh
            except (BrokenPipeError, ConnectionResetError):
                raise
            except Exception as e:
                self._sse("stage", {
                    "id": "discovery", "state": "blocked",
                    "detail": f"{type(e).__name__}: {str(e)[:160]}",
                    "note": "the run failed; nothing is served in its place"})
                self._sse("done", {"ok": False})


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8931
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    paperclip = "found" if shutil.which("paperclip") or Path(PAPERCLIP).exists() else "MISSING"
    # Say whether reading is on. Without a key the pipeline still runs, still
    # retrieves, and still fills the page — it just never reads an abstract, and
    # nothing on screen distinguishes that from a literature search that found
    # nothing. A line at startup is the cheapest place to notice.
    reading = "on" if os.environ.get("ANTHROPIC_API_KEY") else (
        "OFF — no ANTHROPIC_API_KEY, so abstracts are retrieved but never read")
    print(f"→ http://localhost:{port}   (paperclip: {paperclip}; reading: {reading})")
    if _ENV_LOADED:
        print(f"   loaded from .env: {', '.join(_ENV_LOADED)}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
