"""The judgement half of the pipeline, and a record of every call it makes.

Until now nothing in this project called a model. The "agent" resolved a
question by token-matching against a vocabulary and ranked candidates by a
statistic — which is why an API key issued for it showed no traffic at all.

That was the right default for the parts that must be reproducible, and it
stays. Where a model is genuinely better is judgement, and there are exactly
three places here:

  decide_context     what disease is this question about? Free text is
                     ambiguous in ways token overlap cannot resolve — "the
                     tumours that make surfactant", "Ewing's", "the one driven
                     by EWSR1-FLI1" all name a context without naming it.
  read_evidence      does this retrieved abstract actually document a physical
                     contact between the TF and a coactivator, or is it a
                     review that mentions both? That is reading, and the
                     keyword triage this replaces cannot do it.
  explain            why these three, in prose, for a human.

Three rules hold everything together.

**The model never produces a number.** Dependency verdicts, medians, q-values,
docking scores and interface residues are computed and stay computed. Asking a
model to rank by selectivity would produce plausible numbers that no one could
reproduce, which is the exact failure this project exists to avoid.

**The model chooses from a closed set.** `decide_context` is given the real
Oncotree vocabulary and must return one of its entries; anything else is
rejected rather than accommodated. A model cannot invent a disease the data
cannot answer about.

**Everything it says is recorded and labelled.** `AgentTrace` keeps the prompt,
the reply, the token counts and the latency of every call, so a claim in the
interface can always be traced to the exact exchange that produced it, and a
reader can tell model output from computed output.

Without an API key the module degrades to the deterministic path and says so.
It never silently substitutes one for the other.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

MODEL = "claude-sonnet-5"
MAX_TOKENS = 2000


@dataclass
class Call:
    """One exchange with the model, kept whole."""
    step: str
    prompt: str
    reply: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class AgentTrace:
    """Every call this run made, in order.

    The interface renders this so a reader can see what the model was asked and
    what it said, rather than being handed a conclusion. A pipeline that shows
    only its output asks to be trusted; this one can be checked.
    """
    calls: list[Call] = field(default_factory=list)

    def add(self, call: Call) -> Call:
        self.calls.append(call)
        return call

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def seconds(self) -> float:
        return round(sum(c.seconds for c in self.calls), 2)

    def as_dict(self) -> dict:
        return {
            "model": MODEL,
            "calls": [
                {"step": c.step, "prompt": c.prompt, "reply": c.reply,
                 "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
                 "seconds": round(c.seconds, 2), "error": c.error}
                for c in self.calls
            ],
            "total_input_tokens": self.input_tokens,
            "total_output_tokens": self.output_tokens,
            "total_seconds": self.seconds,
        }


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def ask(trace: AgentTrace, step: str, system: str, prompt: str,
        max_tokens: int = MAX_TOKENS) -> Call:
    """One call, recorded whether it succeeds or fails.

    A failed call is recorded as a failed call. It must never look like a model
    that considered the question and had nothing to say.
    """
    if not available():
        return trace.add(Call(step=step, prompt=prompt, reply="", model=MODEL,
                              error="ANTHROPIC_API_KEY is not set"))
    t0 = time.time()
    try:
        msg = _client().messages.create(
            model=MODEL, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return trace.add(Call(step=step, prompt=prompt, reply=text, model=MODEL,
                              input_tokens=msg.usage.input_tokens,
                              output_tokens=msg.usage.output_tokens,
                              seconds=time.time() - t0))
    except Exception as e:
        return trace.add(Call(step=step, prompt=prompt, reply="", model=MODEL,
                              seconds=time.time() - t0,
                              error=f"{type(e).__name__}: {str(e)[:200]}"))


def _repair_escapes(block: str) -> str:
    """Double the backslashes JSON does not recognise as escapes.

    SMILES write cis/trans geometry with backslashes -- `C/C=C\\C` -- and a lone
    backslash before an ordinary character is not a legal JSON escape. A reply
    carrying one structure like that fails to parse *as a whole*, and the
    scanner below then succeeds on some smaller object nested inside it, so the
    caller receives a fragment of the answer and no error. Repairing the escape
    is safe because the alternative reading is invalid JSON either way.
    """
    return re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', block)


def _json_block(text: str) -> dict | None:
    """First JSON object in a reply. Models wrap JSON in prose and fences."""
    for m in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.S):
        for candidate in (m.group(0), _repair_escapes(m.group(0))):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    return None


CONTEXT_SYSTEM = """You map a researcher's question to one disease context from \
a fixed list taken from the DepMap Oncotree vocabulary.

Rules:
- Choose ONLY from the numbered list given. Never invent a context.
- Prefer the most specific context the question actually justifies. A question \
about small cell lung cancer means the SCLC subtype, not the Lung lineage, \
because pooling dilutes subtype-restricted dependencies. A question about \
"lung cancer" generally means the lineage.
- If the question names no disease, or names one absent from the list, return \
index -1. Abstaining is correct and expected; guessing is not.
- Reply with JSON only: {"index": <int>, "reason": "<one sentence>", \
"confidence": "high"|"medium"|"low"}"""


def decide_context(trace: AgentTrace, question: str, options: list) -> tuple:
    """Pick a context from the real vocabulary, or abstain.

    Returns (chosen_option_or_None, reason, call). The chosen value is always an
    element of `options` — the index is bounds-checked rather than trusted, so a
    hallucinated context cannot reach the scan.
    """
    listing = "\n".join(
        f"{i}. {o.context} ({o.level}, {o.n_models} models"
        + (f", within {o.parent_lineage}" if o.parent_lineage else "") + ")"
        for i, o in enumerate(options))
    prompt = (f"Question:\n{question}\n\nContexts:\n{listing}\n\n"
              "Which context should be screened? JSON only.")
    call = ask(trace, "decide_context", CONTEXT_SYSTEM, prompt)
    if call.error:
        return None, call.error, call

    data = _json_block(call.reply) or {}
    idx = data.get("index")
    reason = str(data.get("reason") or "").strip()
    conf = data.get("confidence", "")
    if not isinstance(idx, int) or idx < 0 or idx >= len(options):
        return None, (reason or "the model named no context from the list"), call
    if conf == "low":
        return None, f"low confidence: {reason}", call
    return options[idx], reason, call


EVIDENCE_SYSTEM = """You triage retrieved abstracts for evidence that a \
transcription factor makes a PHYSICAL contact with a transcriptional \
coactivator or Mediator subunit.

What counts: a solved structure of the complex, co-immunoprecipitation, \
pull-down, crosslinking, ITC/SPR, or a mapped interacting region.
What does not: co-expression, being in the same pathway, a review that \
mentions both proteins, or a functional effect with no physical evidence.

You are reading titles and abstracts, not full papers. Say what the abstract \
supports and no more. If the abstract is ambiguous, say so — "unclear" is a \
useful answer and a false positive here sends a GPU run at a contact nobody \
has observed.

A mapped region is the whole game downstream: it is what separates "these two proteins interact" from "we know where". Only fill `region` when the abstract itself names the residues, motif or domain that makes the contact. A whole-protein pull-down, a co-IP with no region, or your own guess at which domain is probably involved is `null` — the pipeline will refuse to model a contact it cannot place, and that refusal is correct.

Reply with JSON only:
{"contact_documented": true|false|"unclear",
 "partner": "<protein named, or null>",
 "support": "structure"|"biochemical"|"genetic"|"none",
 "region": "<the region the abstract states, e.g. \"transactivation domain, residues 374-384\" — null unless the abstract names it>",
 "region_source": <abstract number the region comes from, e.g. 3, else null>,
 "tractability": "short_linear_motif"|"folded_domain"|"unknown",
 "note": "<one sentence, citing what the abstract actually says>"}"""


def read_evidence(trace: AgentTrace, gene: str, papers: list) -> tuple[dict, object]:
    """Judge whether retrieval actually found a documented contact for `gene`.

    This replaces a keyword scan that classified any abstract containing
    "crystal structure" as direct experimental evidence — including reviews and
    papers about an entirely different complex.
    """
    if not papers:
        return {"contact_documented": False, "support": "none",
                "note": "no on-target papers were retrieved for this gene"}, None
    # Twelve abstracts, not eight, and more of each. The region this is being
    # asked for is usually a clause deep in an abstract -- "residues 374-384 of
    # the transactivation domain" -- and truncating at 700 characters cut the
    # sentence that carries it about as often as it kept it.
    body = "\n\n".join(
        f"[{i + 1}] {p.title}\n{(p.abstract or '')[:1400]}"
        for i, p in enumerate(papers[:12]))
    prompt = (f"Transcription factor: {gene}\n\nAbstracts:\n{body}\n\n"
              f"Do these document a physical contact between {gene} and a "
              "coactivator or Mediator subunit? JSON only.")
    call = ask(trace, f"read_evidence:{gene}", EVIDENCE_SYSTEM, prompt)
    if call.error:
        return {"contact_documented": "unclear", "support": "none",
                "note": f"evidence reading failed: {call.error}"}, call
    return (_json_block(call.reply)
            or {"contact_documented": "unclear", "support": "none",
                "note": "the model's reply could not be parsed as JSON"}), call


EXPLAIN_SYSTEM = """You explain a ranked shortlist of transcription-factor \
drug targets to a working biologist.

You are given numbers that were already computed. Use them; never change them, \
never invent new ones, never add a number that is not in the input.

Be direct and specific. Say what the dependency data shows, what the literature \
did and did not find, and what would have to be true next for the target to be \
actionable. Name the biggest weakness in each case — a shortlist that sounds \
uniformly promising is not useful.

Three short paragraphs at most. No headings, no bullet lists, no preamble."""


def explain(trace: AgentTrace, context: str, candidates: list) -> tuple[str, object]:
    """Prose for the shortlist. Numbers come from the caller, not the model."""
    if not candidates:
        return "", None
    lines = []
    for c in candidates:
        lines.append(
            f"- {c['gene']}: median gene effect {c['median']:+.2f} in {context} "
            f"vs {c['median_other']:+.2f} elsewhere; {c['tfrac']:.0%} of "
            f"{context} models dependent vs {c['ofrac']:.0%} elsewhere; "
            f"n={c['n']}; q={c['q']:.2g}; passed via the {c['route']} path. "
            f"Literature: {c.get('evidence_note', 'not assessed')}")
    prompt = (f"Context: {context}\n\nShortlist:\n" + "\n".join(lines)
              + "\n\nExplain this shortlist.")
    call = ask(trace, "explain", EXPLAIN_SYSTEM, prompt, max_tokens=900)
    return (call.reply if not call.error else ""), call


READ_REQUEST_SYSTEM = """You read a researcher's request and report what \
CONDITIONS it places on the pipeline. You do not answer the question and you do \
not decide any science.

The pipeline runs: dependency scan -> literature -> interface evidence -> \
druggable site -> small-molecule screen. Every stage runs by default. Your job \
is to notice when the request says a stage must NOT run unless something holds.

The only condition the pipeline can enforce is this one:

  require_interface_site — the request says to screen compounds only if there \
is support for a site on the partner protein (a documented interaction with a \
mapped region, or a converged structural prediction), and otherwise to abstain \
and say what is missing. Phrasings that mean this: "only if ... proceed to \
screening", "do not dock unless", "gate the screen on", "abstain otherwise".

A request that simply asks for targets, evidence, or compounds places no \
condition: return false. Do not infer a condition from a request being \
detailed. Returning false is the common and correct answer.

Reply with JSON only:
{"require_interface_site": true|false, "quote": "<the words that say so, or null>"}"""


def read_request(trace: AgentTrace, question: str) -> tuple[dict, object]:
    """What the request demands of the pipeline, as flags it can actually honour.

    A multi-step request — "identify three targets, test the interaction
    evidence, and only screen if a site is supported" — used to be read as
    nothing but a disease name. Every stage ran regardless, so the interface
    answered a question the researcher did not ask and looked confident doing
    it. This reads the control flow instead of ignoring it, and reads it into a
    closed set: one flag the downstream stages know how to obey. A condition
    the pipeline cannot enforce is not returned as one it can.
    """
    if not question.strip():
        return {"require_interface_site": False, "quote": None}, None
    call = ask(trace, "read_request", READ_REQUEST_SYSTEM,
               f"Request:\n{question}\n\nWhat conditions does it place? JSON only.",
               max_tokens=300)
    if call.error:
        return {"require_interface_site": False, "quote": None}, call
    data = _json_block(call.reply) or {}
    return ({"require_interface_site": bool(data.get("require_interface_site")),
             "quote": data.get("quote")}, call)


ANSWER_SYSTEM = """You answer the researcher's question using ONLY the record \
of what this run actually did. The record is below the question; it lists every \
stage, whether it completed or abstained, and the numbers it produced.

Rules, in order of importance:

1. Every number you write must appear in the record. Never compute, round, \
estimate or infer one. If the record does not contain what the question asks \
for, say plainly that the run did not produce it.
2. Answer the question that was asked, in the order it was asked. If it had \
several parts, address each. If a part could not be answered, say which \
evidence was missing rather than substituting a part you can answer.
3. A stage that abstained is a result, not a gap to apologise for. Say what it \
abstained on and what would change it.
4. Never claim binding, efficacy, or safety. Docking scores rank poses. A \
predicted interface is a hypothesis. A dependency is a statement about cell \
lines, not patients.

Write for a working biologist: direct, specific, no preamble, no headings, no \
bullet lists. Three short paragraphs at most, fewer if the answer is short. \
Lead with the answer, not with what you did."""


def answer(trace: AgentTrace, question: str, record: dict) -> tuple[str, object]:
    """Respond to the question actually asked, from the record of what ran.

    `explain` narrates a shortlist, which only makes sense when there is one.
    This answers the request — including the requests that got no shortlist,
    named a gene rather than a disease, or asked for something the pipeline
    abstained on. The record is the whole input: nothing here reaches the data,
    so nothing here can produce a number the run did not.
    """
    if not question.strip():
        return "", None
    body = json.dumps(record, indent=1, default=str)[:12000]
    call = ask(trace, "answer", ANSWER_SYSTEM,
               f"Question:\n{question}\n\nRecord of this run:\n{body}\n\n"
               "Answer the question.", max_tokens=1100)
    return (call.reply if not call.error else ""), call


LIBRARY_SYSTEM = """You propose small molecules to screen against a described \
protein site. You are proposing a starting library, not predicting binders, and \
nothing you return will be treated as evidence of anything until it has been \
docked and checked.

You are given the site's actual residues and their chemistry. Use them. A \
hydrophobic groove and a charged surface call for different molecules, and a \
protein-protein interface is a wide shallow surface — flat, rigid, moderately \
lipophilic scaffolds are more appropriate than compact globular fragments.

Rules:
- Propose REAL, well-known compounds wherever possible: approved drugs, tool \
compounds, published PPI inhibitors, natural products. Their structures are \
checkable against public databases, and a checkable structure is worth more \
here than an imaginative one.
- Write each SMILES carefully and completely. A SMILES that parses into the \
wrong molecule is worse than no molecule: it is a wrong answer that looks \
correct. If you are not confident of a compound's exact structure, choose a \
different compound you do know exactly.
- Vary the set. Several near-identical scaffolds test one hypothesis, not many.
- Say in one SHORT clause -- twelve words at most -- why each is worth \
docking against THIS site. Long rationales truncate the reply and lose \
compounds.

Reply with JSON only:
{"compounds": [{"name": "<common name>", "smiles": "<SMILES>", \
"rationale": "<one clause about this site>", "kind": "approved"|"tool"|\
"ppi_inhibitor"|"natural_product"|"fragment"}]}"""


def propose_library(trace: AgentTrace, site: dict, n: int = 8) -> tuple[list, object]:
    """A screening library for one described site, proposed rather than stored.

    The alternative was a constant: twelve approved drugs written into a script,
    identical for every site the pipeline will ever build, which tests the
    docking machinery and nothing about the site. Proposing from the site's own
    residues at least asks the right question — and because a model can write a
    SMILES that parses into the wrong molecule, every structure returned here is
    standardized and then checked against PubChem by InChIKey before it is
    docked. Nothing about this makes a compound a binder; it makes the library
    relevant and its members identifiable.
    """
    prompt = (f"Site:\n{json.dumps(site, indent=1)}\n\n"
              f"Propose {n} compounds to dock against it. JSON only.")
    # Room to finish the list. At 2,000 the reply truncated mid-object and the
    # scanner below then parsed some inner fragment instead, so the caller got
    # an empty library and no error -- the failure mode this budget exists to
    # avoid, not a cost saving worth making.
    call = ask(trace, "propose_library", LIBRARY_SYSTEM, prompt, max_tokens=6000)
    if call.error:
        return [], call
    data = _json_block(call.reply) or {}
    compounds = list(data.get("compounds") or [])
    if compounds:
        return compounds, call

    # A list this long can be cut off mid-object, and a truncated outer object
    # does not parse -- at which point the scanner above succeeds on some
    # complete inner object instead and the caller gets a fragment with no
    # "compounds" key and no error. Recover every compound that arrived whole.
    for m in re.finditer(r'\{[^{}]*"smiles"\s*:\s*"[^"]*"[^{}]*\}', call.reply, re.S):
        for candidate in (m.group(0), _repair_escapes(m.group(0))):
            try:
                compounds.append(json.loads(candidate))
                break
            except json.JSONDecodeError:
                continue
    return compounds, call
