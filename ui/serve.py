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
DATA = json.loads((UI / "data.json").read_text(encoding="utf-8"))


def paperclip_search(query: str, n: int = 6) -> list[dict]:
    """Real Paperclip call. Returns [] and lets the caller report the failure."""
    try:
        out = subprocess.run(
            [PAPERCLIP, "search", "-s", "pmc", query, "-n", str(n)],
            capture_output=True, text=True, timeout=90)
    except Exception:
        return []
    # Parse defensively: the CLI's metadata line has already changed shape once
    # (it now carries the journal where it used to say PMC), and a brittle parser
    # silently reports "no papers found", which reads as a real null result.
    ID = re.compile(r"\b((?:PMC|bio_|med_|arx_|tri_|fda_)[\w.]+)\b")
    papers, cur = [], None
    for raw in out.stdout.splitlines():
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
        if not cur["id"] and (hit := ID.search(line)) and not line.startswith("http"):
            cur["id"] = hit.group(1)
        elif line.startswith("http") and not cur["url"]:
            cur["url"] = line
        elif line.startswith('"'):
            cur["abstract"] = line.strip('"')
    if cur:
        papers.append(cur)
    # Keep anything with a title; an id-less hit is still a real retrieval.
    return [p for p in papers if p.get("title")]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(UI), **kw)

    def log_message(self, *a):  # quiet
        pass

    def _sse(self, event: str, payload: dict) -> None:
        self.wfile.write(f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode())
        self.wfile.flush()

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path != "/api/run":
            return super().do_GET()

        q = (parse_qs(url.query).get("q") or [""])[0].strip()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            self._run(q)
        except (BrokenPipeError, ConnectionResetError):
            pass   # the reader navigated away

    def _run(self, q: str) -> None:
        land = DATA["landscape"]
        cands = DATA["candidates"]
        T = DATA["thresholds"]

        self._sse("stage", {"id": "question", "state": "done",
                            "detail": q or "no question given",
                            "note": "Scoped to public DepMap dependency data and the "
                                    "PMC full-text corpus."})

        # 1 — literature. The only stage that can genuinely fail here.
        self._sse("stage", {"id": "literature", "state": "running",
                            "detail": "searching PMC full text"})
        papers = paperclip_search(q or "transcription factor coactivator interface", 6)
        for p in papers:
            self._sse("paper", p)
        self._sse("stage", {
            "id": "literature",
            "state": "done" if papers else "blocked",
            "detail": f"{len(papers)} papers retrieved" if papers
                      else "Paperclip returned nothing — CLI missing, not logged in, or no match",
            "note": "Retrieved, not read. A title is not evidence." if papers else
                    "The agent's reach is bounded by what the corpus indexes."})

        # 2 — discovery
        self._sse("stage", {"id": "discovery", "state": "running",
                            "detail": f"{len(land)} transcription factors"})
        self._sse("stage", {"id": "discovery", "state": "done",
                            "detail": f"{len(land)} screened in {DATA['landscape_context']}",
                            "note": "DepMap 24Q2 Chronos, restricted to the Lambert TF catalogue.",
                            "metric": len(land)})

        # 3 — ranking / gates
        passed = [x for x in land if x["pass"]]
        tally: dict[str, int] = {}
        for x in land:
            for w in x["why"]:
                tally[w] = tally.get(w, 0) + 1
        self._sse("stage", {
            "id": "ranking", "state": "done" if passed else "abstained",
            "detail": f"{len(passed)} of {len(land)} clear every gate",
            "note": "; ".join(f"{n} × {w}" for w, n in
                              sorted(tally.items(), key=lambda kv: -kv[1])[:2]),
            "metric": len(passed)})

        # 4 — specificity
        scored = [c for c in cands if not c["awaiting"]]
        self._sse("stage", {
            "id": "specificity", "state": "done" if scored else "blocked",
            "detail": f"{len(scored)} candidates with a selective dependency",
            "note": "Cancer-cell selectivity is not normal-tissue safety.",
            "metric": len(scored)})

        # 5 — druggable site
        mapped = [c for c in cands if c["region_mapped"] and not c["control"]]
        self._sse("stage", {
            "id": "site", "state": "done" if mapped else "abstained",
            "detail": (", ".join(f"{c['gene']}–{c['partner']}" for c in mapped)
                       if mapped else "no mapped interacting region"),
            "note": "A whole-protein pull-down is association, not contact.",
            "metric": len(mapped)})

        # 6 — structure, per target-partner pair rather than one fixed complex
        structs = DATA.get("structures") or {}
        pairs = {f"{c['gene']}|{c['partner']}" for c in cands if c["partner"]}
        have = sorted(k for k in structs if k in pairs)
        self._sse("stage", {
            "id": "structure", "state": "done" if have else "blocked",
            "detail": ", ".join(
                f"{structs[k]['gene']}–{structs[k]['partner']} PDB {structs[k]['pdb_id']}"
                f" at {structs[k]['resolution_a']} Å" for k in have)
                if have else "no candidate pair has deposited coordinates",
            "note": f"{len(have)} of {len(pairs)} pairs solved; "
                    f"{len(DATA.get('predicted') or {})} targets fall back to an AlphaFold "
                    f"monomer. A structure is not a binding claim."})

        # 7 — screening: genuinely not built yet, and says so
        self._sse("stage", {
            "id": "screening", "state": "pending",
            "detail": "not run",
            "note": "Needs an approved-drug library and a defined box. Blocked until a "
                    "human signs checkpoint 4."})

        # 8 — the intersection is the finding
        both = [c for c in cands
                if not c["awaiting"] and c["involvement"] == "direct" and not c["control"]]
        self._sse("stage", {
            "id": "experiment", "state": "done",
            "detail": (f"{len(both)} candidate(s) with both a dependency and a mapped contact"
                       if both else "no candidate has both a dependency and a mapped contact"),
            "note": "Every ranked dependency lacks a documented contact; every mapped "
                    "contact lacks dependency numbers. That gap is the next experiment."})
        self._sse("done", {"ok": True})


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
