#!/usr/bin/env python
"""Static server + a live pipeline endpoint, stdlib only.

    python ui/serve.py            # http://localhost:8931

The page works without this — it falls back to the recorded run in data.json and
says so. With it, the query bar runs the real pipeline: Paperclip searches the
corpus, the gates run on real DepMap numbers, and each stage streams its own
result as it completes rather than appearing all at once at the end.

Nothing here fabricates a stage. A stage that cannot run reports that it could
not run, which is the whole point of the interface.
"""
from __future__ import annotations

import json
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
PAPERCLIP = shutil.which("paperclip") or str(ROOT / ".venv/bin/paperclip")
# data.json is the cold-start view only. A run computes its own numbers.
DATA = json.loads((UI / "data.json").read_text(encoding="utf-8"))


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

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path != "/api/run":
            return super().do_GET()

        params = parse_qs(url.query)
        q = (params.get("q") or [""])[0].strip()
        # An explicit request to fold one candidate on GPU during this run. It
        # is a query parameter rather than a setting because it is a decision
        # someone makes per run and pays for per run; nothing here starts a
        # dispatch on its own.
        predict = (params.get("predict") or [""])[0].strip().upper() or None
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            self._run(q, predict)
        except (BrokenPipeError, ConnectionResetError):
            pass   # the reader navigated away

    def _run(self, q: str, predict: str | None = None) -> None:
        """Delegate to the real pipeline. Nothing here is precomputed: the
        landscape, the gates and the candidate table are all computed for the
        context resolved from this question."""
        import sys
        if str(UI) not in sys.path:
            sys.path.insert(0, str(UI))
        from pipeline_api import run_live
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
        try:
            run_live(
                q,
                (release / "CRISPRGeneEffect.csv", release / "Model.csv",
                 D / "lambert_tfs.csv"),
                DiscoveryConfig(),
                self._sse,
                interface_evidence=evidence,
                free_receptor=D / "9F76.cif",
                predict=predict,
            )
        except (BrokenPipeError, ConnectionResetError):
            raise
        except Exception as e:
            self._sse("stage", {"id": "discovery", "state": "blocked",
                                "detail": f"{type(e).__name__}: {str(e)[:160]}",
                                "note": "the run failed; nothing is served in its place"})
            self._sse("done", {"ok": False})


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8931
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"→ http://localhost:{port}   (paperclip: {'found' if shutil.which('paperclip') or Path(PAPERCLIP).exists() else 'MISSING'})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
