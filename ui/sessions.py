"""What a finished run leaves behind, and what a follow-up may reuse of it.

The interface was stateless. Every question re-ran the whole pipeline — the
DepMap scan, one Paperclip subprocess per axis per candidate, the structural
tail — so "why was NKX2-1 rejected?" cost exactly as much as the question that
rejected it, and the answer to that one had already been computed and thrown
away. Even the fold button, which is about one candidate's structure, paid for
the entire scan again on its way to the structure stage.

Two rules shape this file, and they pull against each other:

  a follow-up must not re-run a stage whose inputs have not changed, and
  a stage must never report a conclusion it did not compute this run.

They are reconciled by provenance, not by recomputation. Retained state is
served, and it is served *labelled*: every event carries the epoch at which it
was actually computed, every replayed stage says so in its own note, and the
answer says which half of what the reader is looking at is retained and from
when. A reader can tell a number computed for their question from a number
computed four minutes ago for a different one, which is the only thing that
makes reuse honest here.

Routing is deliberately lopsided. A follow-up is served from retained state
only when it matches one of a small set of recognised intents *and* names
nothing that could have moved the context; everything else re-runs. A false
negative costs a re-run of a pipeline that was going to run anyway before this
file existed. A false positive answers a question about breast cancer with
numbers computed for lung, which is the failure this whole project is built to
catch.

Stdlib only. `ui/serve.py` is stdlib-only by design and this is the half of the
session it needs before pandas is anywhere near the process.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# A session retains a whole run's event stream: a ~1,500-point landscape, every
# abstract retrieved, and — when a screen ran — a few kilobytes of pose SDF per
# compound. Megabytes each, not kilobytes. Both bounds exist because serve.py is
# a long-lived process that a demo leaves open for hours: the TTL drops what
# nobody is coming back to, and the cap stops one-session-per-reload from
# growing the heap without limit. Eviction is by last use, so the conversation
# someone is in the middle of is the last thing dropped.
# A session lasts until a new one is asked for. It was 45 minutes in memory,
# which meant every restart of the server — three in one afternoon, for a config
# fix and two dependency fixes — silently destroyed the reader's conversation:
# the next follow-up found an id naming nothing, ran the whole Paperclip pull
# again, and looked like statefulness that did not work.
#
# `None` disables time expiry. The cap remains, because a record holds a run's
# whole event stream and unbounded growth on disk is its own failure; eviction
# is by least-recently-used, so the conversation in front of someone is the last
# thing to go.
TTL_SECONDS = None
MAX_SESSIONS = 50
STORE_DIR = Path(os.environ.get("REAGENT_SESSION_DIR")
                 or Path(__file__).resolve().parent.parent / "outputs" / "ui_sessions")


def iso(at: float) -> str:
    """UTC, to the second. The reader is being told when something was computed,
    and a bare epoch is not that."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


def ago(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 90:
        return f"{s} second{'' if s == 1 else 's'}"
    m = s // 60
    if m < 90:
        return f"{m} minute{'' if m == 1 else 's'}"
    h = m // 60
    return f"{h} hour{'' if h == 1 else 's'}"


@dataclass
class Session:
    """One conversation, and the last completed run behind it.

    `record` is JSON-able and is what a follow-up replays from. `runtime` holds
    the live objects that cannot be serialized and are only meaningful inside
    this process — the docking box, the evidence dict the literature stage
    mutated, the agent trace the reasoning rail is rendering. Keeping the two
    apart means the record can be inspected, tested and served as JSON without
    dragging the pipeline's types along with it.
    """
    id: str
    created: float
    touched: float
    questions: list[str] = field(default_factory=list)
    record: dict = field(default_factory=dict)
    runtime: dict = field(default_factory=dict)
    # One question at a time per session. Two tabs on the same conversation is
    # ordinary, and two runs writing into one record interleave into a state
    # that describes neither of them — a table holding one run's rows under
    # another run's context is precisely the thing being guarded against here.
    # The second reader waits; a dropped connection releases it either way.
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # True when this came back from disk after a restart: the record survived,
    # the live objects did not.
    restored: bool = False

    @property
    def computed_at(self) -> float:
        return float(self.record.get("computed_at") or self.created)

    def summary(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        r = self.record
        return {
            "id": self.id,
            "created": iso(self.created),
            "age_s": int(now - self.created),
            "idle_s": int(now - self.touched),
            "questions": list(self.questions),
            "context": r.get("context"),
            "level": r.get("level"),
            "computed_at": iso(self.computed_at) if r else None,
            "genes": list(r.get("genes") or []),
            "measured": len(r.get("measured") or {}),
            "stages": {k: v.get("state") for k, v in (r.get("stages") or {}).items()},
        }


class Store:
    """Sessions, bounded two ways, safe to touch from more than one thread.

    `ThreadingHTTPServer` gives every request its own thread, and two tabs on
    the same session is the ordinary case rather than the exotic one, so the
    dict is behind a lock. Expiry is checked on access rather than on a timer:
    a background sweeper in a dev server is one more thread to get wrong, and
    nothing here needs memory freed at a particular moment — only bounded.
    """

    def __init__(self, ttl: float | None = TTL_SECONDS, limit: int = MAX_SESSIONS,
                 clock=time.time, store_dir=STORE_DIR) -> None:
        self._ttl = ttl
        self._limit = limit
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        # None disables persistence, which is what the tests want: a store with
        # a fake clock and a temp dir should not find yesterday's sessions.
        self._dir = Path(store_dir) if store_dir else None

    def __len__(self) -> int:
        with self._lock:
            self._purge()
            return len(self._sessions)

    def new(self) -> Session:
        # Unguessable rather than sequential. A session id is the key to another
        # reader's retained run, and this server has no other authentication.
        with self._lock:
            self._purge()
            now = self._clock()
            s = Session(id=secrets.token_urlsafe(12), created=now, touched=now)
            self._sessions[s.id] = s
            self._evict()
            return s

    def get(self, sid: str | None) -> Session | None:
        """The live session for `sid`, or None if it never existed or expired.

        Returning None for an expired session rather than resurrecting it is the
        point of the TTL: the caller then does a full run and says the session
        expired, instead of answering from state old enough that nobody in the
        room remembers what produced it.
        """
        if not sid:
            return None
        with self._lock:
            self._purge()
            s = self._sessions.get(sid)
            if s is None:
                # Not in memory is not the same as gone. The process may simply
                # be younger than the conversation.
                s = self._load(sid)
                if s is not None:
                    self._sessions[sid] = s
                    self._evict()
            if s is not None:
                s.touched = self._clock()
            return s

    def _path(self, sid: str) -> Path | None:
        return (self._dir / f"{sid}.json") if self._dir else None

    def save(self, s: Session) -> None:
        """Write a session's record so a restart does not destroy it.

        Only the JSON-able half. `runtime` holds live objects — the docking box,
        the evidence dict the literature stage mutated, the agent trace — and a
        restored session says so rather than pretending it has them.

        Written to a temporary file and renamed, so a reader never sees half a
        record: a crash mid-write would otherwise leave a session that parses as
        far as the truncation and looks like a short run.
        """
        path = self._path(s.id)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({
                "id": s.id, "created": s.created, "touched": s.touched,
                "questions": s.questions, "record": s.record,
            }), encoding="utf-8")
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError):
            # Persistence is a convenience; losing it must never take the run
            # down with it. The session stays live in memory either way.
            pass

    def _load(self, sid: str) -> Session | None:
        path = self._path(sid)
        if path is None or not path.is_file():
            return None
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if d.get("id") != sid:
            return None
        s = Session(id=sid, created=float(d.get("created") or self._clock()),
                    touched=self._clock(), questions=list(d.get("questions") or []),
                    record=d.get("record") or {})
        # The live objects did not survive the restart, and a follow-up that
        # needs them has to know. Evidence promoted by reading in the original
        # run is not in force here, which can only make a gate stricter.
        s.restored = True
        return s

    def _purge(self) -> None:
        if self._ttl is None:          # a session lasts until a new one is asked for
            return
        cut = self._clock() - self._ttl
        for sid in [k for k, s in self._sessions.items() if s.touched < cut]:
            del self._sessions[sid]

    def _evict(self) -> None:
        while len(self._sessions) > self._limit:
            oldest = min(self._sessions.values(), key=lambda s: s.touched)
            del self._sessions[oldest.id]
            path = self._path(oldest.id)
            if path is not None:
                path.unlink(missing_ok=True)


# ── routing ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FollowUp:
    """What kind of follow-up this is, and what it costs.

    `reason` is written to be read out loud on screen. The reader is being told
    that a stage did not run for their question, and "matched a follow-up
    intent" is not an explanation of that.
    """
    kind: str            # verdict | gene | screen | predict | recall | rerun
    gene: str | None
    reason: str
    cheap: bool

    @property
    def recompute(self) -> tuple[str, ...]:
        return RECOMPUTE.get(self.kind, ())


# Which stages each kind of follow-up computes again. Everything not listed is
# replayed from the retained record, labelled with when it was computed. The
# lists are short on purpose: a stage is recomputed when its *inputs* changed,
# and a follow-up that adds a gene changes the literature stage's input (a new
# gene to search) and the structural tail's (a new candidate to box against),
# while leaving the question, the scan and the gate exactly where they were.
RECOMPUTE: dict[str, tuple[str, ...]] = {
    "verdict": (),
    "recall": (),
    "screen": (),
    "gene": ("literature", "structure", "site", "screening"),
    "predict": ("structure", "site", "screening"),
}

# Words that name a disease or a tissue. The guard below refuses a cheap answer
# whenever one of these appears and is not already part of the context the
# retained run resolved to: a follow-up that has moved to another disease is a
# different scan, and answering it from the old one's numbers is the exact
# failure this file exists to prevent. Long words match case-insensitively;
# abbreviations do not, because "all" is a common English word and "ALL" is a
# leukaemia.
_DISEASE = re.compile(
    r"\b(cancers?|carcinomas?|sarcomas?|tumou?rs?|leuk[ae]mias?|lymphomas?|"
    r"melanomas?|myelomas?|gliomas?|glioblastomas?|blastomas?|mesotheliomas?|"
    r"adenocarcinomas?|neoplasms?|malignanc\w+|metasta\w+|"
    r"breast|lung|prostate|ovarian|ovary|pancrea\w+|colorectal|colon|gastric|"
    r"stomach|liver|hepat\w+|kidney|renal|bladder|urothelial|skin|cutaneous|"
    r"brain|blood|bone|thyroid|uterine|endometrial|cervical|oesophageal|"
    r"esophageal|myeloid|lymphoid|rhabdoid|ewing|neuroendocrine|neuroblastoma)\b",
    re.I)
_DISEASE_ABBREV = re.compile(
    r"\b(SCLC|NSCLC|LUAD|LUSC|AML|CML|ALL|CLL|DLBCL|GBM|TNBC|HNSCC|CRC|PDAC|"
    r"HCC|RCC|MPNST|ATRT|DIPG)\b")

# A gene symbol as this project's catalogues write them: uppercase, digits
# allowed, one optional hyphenated suffix so NKX2-1 survives. Over-matching is
# harmless because every hit is then required to be a gene the retained scan
# actually measured — there is no gene called WHY.
_SYMBOL = re.compile(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)?\b")

# Uppercase tokens this project's own vocabulary produces, which are not genes.
# Anything else in capitals that the retained scan did not measure is treated as
# a gene it cannot answer about, and sends the question to a full run: a symbol
# with no retained number needs the real catalogue, which is not on this side.
_NOT_A_GENE = {
    "DNA", "RNA", "PDB", "GPU", "CPU", "FDR", "TF", "TFS", "PPI", "SPR", "ITC",
    "API", "CSV", "JSON", "URL", "SMILES", "SDF", "CIF", "MSA", "NMR", "GST",
    "IC50", "PMC", "DEPMAP", "CRISPR", "UNIPROT", "PUBMED", "VINA", "BOLTZ",
    "MODAL", "ALPHAFOLD", "MEDIATOR", "OK",
}

_WHY = re.compile(r"\bwhy\b|\bwhat\s+(?:kept|stopped|excluded)\b", re.I)
_PREDICT = re.compile(r"\b(co-?fold|refold|fold|predict|dispatch)\b", re.I)
_SCREEN = re.compile(
    r"\b(screen|screens|screened|screening|dock|docks|docked|docking|"
    r"compounds?|ligands?|librar\w+|poses?|molecules?)\b", re.I)
_RECALL = re.compile(
    r"\b(what|which|who|where|when|how|show|list|summar\w+|explain|recap|"
    r"remind|again|repeat|tell)\b", re.I)


def named_genes(question: str, known: set[str]) -> list[str]:
    """Symbols the question names, restricted to genes the retained run measured.

    Membership in the retained scan is the entire filter, and it is also the
    precondition for answering cheaply: a symbol the scan never measured has no
    retained number to serve, so the honest response to it is a re-run against
    the real catalogue rather than a guess from this side.
    """
    return [t for t in dict.fromkeys(_SYMBOL.findall(question or "")) if t in known]


def _other_context(question: str, context: str | None) -> str:
    """The first disease or tissue word that the retained context does not cover.

    Substring containment against the resolved context is crude and that is
    deliberate. "small cell lung cancer" against a run resolved to Small Cell
    Lung Cancer matches every word and stays cheap; the same phrase against a
    run resolved to the Lung *lineage* does not contain "cancer", and re-running
    is right there — the subtype and the lineage are different cohorts and
    pooling them is what buried ASCL1, POU2F3, NEUROD1 and INSM1 once already.
    """
    ctx = (context or "").lower()
    for m in list(_DISEASE.finditer(question)) + list(_DISEASE_ABBREV.finditer(question)):
        word = m.group(0)
        low = word.lower()
        if low not in ctx and low.rstrip("s") not in ctx:
            return word
    return ""


# "on MED23", "against MLL", "partner KMT2A" — the phrasings a question uses to
# name the protein being screened, as opposed to the TF doing the binding.
_RECEPTOR_CUE = re.compile(
    r"\b(?:on|against|partner|receptor|subunit|target(?:ing)?)\s+"
    r"(?:the\s+)?([A-Z][A-Z0-9]{1,}(?:-[A-Z0-9]+)?)\b")


def _named_receptor(question: str) -> str | None:
    """The receptor a question names, or None. Symbols only, uppercase only."""
    for tok in _RECEPTOR_CUE.findall(question or ""):
        up = tok.upper()
        if up not in _NOT_A_GENE and len(up) >= 3:
            return up
    return None


def classify(question: str, record: dict) -> FollowUp:
    """What this follow-up is, given what the session already computed.

    The default is `rerun`. Every cheap branch below is a pattern someone can
    read and check; anything that does not match one of them is a question this
    session has no grounds to answer from state, and the pipeline runs again.
    """
    q = (question or "").strip()
    if not q:
        return FollowUp("rerun", None, "no question was given", False)
    if not record or not record.get("events"):
        return FollowUp("rerun", None,
                        "this session holds no completed run to build on", False)

    measured = set(record.get("measured") or {})
    followed = set(record.get("genes") or [])
    partner = (record.get("partner") or "").upper()
    known = measured | followed | ({partner} if partner else set())

    # A different receptor is a different question, exactly as a different
    # cohort is: the site, the screen and the structure all belong to the
    # protein being screened. Without this, asking about MED23 after a KMT2A run
    # came back as a cheap `screen` follow-up — the pipeline built a MED23 box
    # and docked twelve compounds into it while the view still drew KMT2A and
    # showed none of them, because the two halves disagreed about what the run
    # was about. Aliases are not resolved here (MLL and KMT2A read as different
    # symbols), which costs a re-run that was already going to be a re-run and
    # never answers about the wrong protein.
    asked_for = _named_receptor(q)
    if partner and asked_for and asked_for != partner:
        return FollowUp("rerun", None,
                        f"this question is about {asked_for}, and the retained run "
                        f"is about {partner} — a different receptor is a different "
                        "site, screen and structure", False)

    context = record.get("context")
    other = _other_context(q, context)
    if other:
        return FollowUp(
            "rerun", None,
            f"this question names {other!r}, which is not part of the context "
            + (f"{context!r} " if context else "")
            + "the retained run resolved — a different cohort is a different scan",
            False)

    # A symbol the retained scan never measured. "What about SOX2?" reads like a
    # cheap follow-up and is not one: there is no retained number for SOX2, and
    # whether SOX2 is even a gene this project answers about is a question for
    # the Lambert catalogue, which lives on the pipeline's side of the wall.
    unknown = next((t for t in _SYMBOL.findall(q)
                    if len(t) >= 3 and t not in known and t not in _NOT_A_GENE), None)
    if unknown:
        return FollowUp("rerun", None,
                        f"{unknown} is not in the retained scan, so both its numbers "
                        "and its place in the catalogue need a run", False)

    named = named_genes(q, known)
    gene = next((g for g in named if g != partner), None)

    if _WHY.search(q) and gene and gene in measured:
        return FollowUp("verdict", gene,
                        f"the verdict on {gene} was computed by the retained scan "
                        "and is answered from it", True)
    if _PREDICT.search(q) and gene:
        return FollowUp("predict", gene,
                        f"a structure request for {gene}, which needs the structural "
                        "tail and nothing above it", True)
    if gene and gene not in followed:
        return FollowUp("gene", gene,
                        f"{gene} was measured by the retained scan but not followed "
                        "downstream, so only its evidence and the structural tail "
                        "have to run", True)
    if _SCREEN.search(q):
        # Whatever the run decided about the site and the screen, including an
        # abstention, is what this serves. The box belongs to the receptor and
        # the library, not to the question, so nothing about it has changed —
        # and a screen that abstained is a result to be quoted, not a gap to
        # fill in on a second asking.
        return FollowUp("screen", gene,
                        "the site and the screen against it belong to the receptor, "
                        "and this session already holds what the run decided about "
                        "both", True)
    if _RECALL.search(q):
        return FollowUp("recall", gene,
                        "this asks about what the retained run produced, not for "
                        "something new to be computed", True)
    return FollowUp("rerun", None,
                    "nothing in this question matches a follow-up this session can "
                    "answer from what it already computed", False)
