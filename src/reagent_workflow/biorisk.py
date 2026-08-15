"""Biorisk gateway — the screen every run passes before any other stage.

This pipeline proposes protein targets and small molecules that engage them.
That is useful for medicine and, pointed the wrong way, useful for harm. The
gateway sits in front of `INGEST` and decides whether a request may proceed at
all.

Everything — the policy tables, the models, and the engine — lives in this one
file on purpose. A safety gate a reviewer cannot read in a single sitting is a
safety gate nobody audits.

## What this refuses, and what it deliberately does not

The distinction that matters is **direction of effect and intent**, not whether
a pathogen is mentioned:

- Inhibiting a pathogen protein to treat an infection is **countermeasure
  development**. That is medicine, and it is permitted.
- Enhancing transmissibility, virulence, host range, immune escape, or
  environmental stability is **refused**, and no framing makes it permitted.

A gate that refuses the word "virus" would block antiviral drug discovery. Such
a gate gets switched off, and then nothing is screened. Over-blocking is not the
safe direction; it is a different failure.

## What this is not

Signal matching is a **first-pass screen**, not a guarantee and not a substitute
for institutional review. It will produce false positives, and a determined
misuse framed in ordinary language will pass it. Its job is to catch the obvious,
force the ambiguous to a human, and leave a record of what was asked. Treat a
`PERMITTED` verdict as "nothing known fired", never as "this was approved".

Escalation, not silent blocking, is the default for anything uncertain: the
run stops and asks a person.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .store import content_hash, utc_now

BIORISK_POLICY_VERSION = "1.0"


class RiskTier(StrEnum):
    """Outcome of the screen. Ordered by severity."""

    PERMITTED = "permitted"
    REVIEW_REQUIRED = "review_required"
    REFUSED = "refused"


class ConcernCategory(StrEnum):
    PATHOGEN_ENHANCEMENT = "pathogen_enhancement"
    TRANSMISSIBILITY = "transmissibility"
    IMMUNE_EVASION = "immune_evasion"
    SELECT_AGENT_OR_TOXIN = "select_agent_or_toxin"
    THERAPEUTIC_RESISTANCE = "therapeutic_resistance"
    WEAPONIZATION = "weaponization"
    HUMAN_GERMLINE = "human_germline"
    NON_MEDICAL_USE = "non_medical_use"
    DELIBERATE_HARM = "deliberate_harm"


# --------------------------------------------------------------------- policy
# Each entry is (compiled pattern, human-readable description). Patterns are
# matched against the whole request text, lowercased. They are deliberately
# phrase-based rather than single words: "virus" is not a concern, "make the
# virus more transmissible" is.

def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# Refused outright. No intent statement makes these permitted, because the
# capability itself is the hazard.
REFUSE_SIGNALS: tuple[tuple[ConcernCategory, re.Pattern[str], str], ...] = (
    (ConcernCategory.TRANSMISSIBILITY,
     _p(r"\b(increas|enhanc|improv|boost|maximiz)\w*\b[^.]{0,40}\b(transmissibility|transmission|contagious|infectivity|spread)"),
     "increasing transmissibility or infectivity of a pathogen"),
    (ConcernCategory.PATHOGEN_ENHANCEMENT,
     _p(r"\b(increas|enhanc|improv|boost|maximiz)\w*\b[^.]{0,40}\b(virulence|patho?genicity|lethality|mortality)"),
     "increasing virulence, pathogenicity, or lethality"),
    (ConcernCategory.PATHOGEN_ENHANCEMENT,
     _p(r"\b(expand|broaden|alter|extend)\w*\b[^.]{0,40}\bhost\s+(range|tropism)"),
     "expanding pathogen host range or tropism"),
    (ConcernCategory.PATHOGEN_ENHANCEMENT,
     _p(r"\bgain[- ]of[- ]function\b(?![^.]{0,60}\b(ban|prohibit|refus|moratorium|polic|oversight)\w*)"),
     "gain-of-function work on a pathogen"),
    (ConcernCategory.IMMUNE_EVASION,
     _p(r"\b(evad|escap|defeat|circumvent|bypass|subvert)\w*\b[^.]{0,40}\b(immun\w+|vaccine|antibod\w+|neutraliz\w+)"),
     "engineering escape from immunity, vaccines, or antibodies"),
    (ConcernCategory.THERAPEUTIC_RESISTANCE,
     _p(r"\b(confer|engineer|introduc|creat|induc)\w*\b[^.]{0,40}\bresistan\w+\b[^.]{0,30}\b(antibiotic|antiviral|drug|therap\w+|treatment)"),
     "engineering resistance to antibiotics, antivirals, or therapeutics"),
    (ConcernCategory.THERAPEUTIC_RESISTANCE,
     _p(r"\bmake\b[^.]{0,30}\b(untreatable|incurable|resistant to (all|every)\b)"),
     "making a disease or pathogen untreatable"),
    (ConcernCategory.WEAPONIZATION,
     _p(r"\b(weapon\w*|bioweapon|biological weapon|mass casualt\w+|aerosoliz\w+ (a |the )?(pathogen|agent|spore|toxin))"),
     "weaponization or mass-casualty framing"),
    (ConcernCategory.SELECT_AGENT_OR_TOXIN,
     _p(r"\b(synthesiz|produc|manufactur|obtain|acquir|increas|enhanc)\w*\b[^.]{0,40}\b(ricin|botulinum|anthrax|variola|smallpox|nerve agent|sarin|vx\b|abrin|saxitoxin)"),
     "producing or enhancing a select agent or high-consequence toxin"),
    (ConcernCategory.HUMAN_GERMLINE,
     _p(r"\b(human\s+germline|heritable human|edit\w*\b[^.]{0,25}\bembryo)"),
     "heritable human germline modification"),
    (ConcernCategory.DELIBERATE_HARM,
     _p(r"\b(disabl|suppress|destroy|cripple)\w*\b[^.]{0,30}\b(human )?immune (system|response)\b(?![^.]{0,60}\b(autoimmun|transplant|graft|hypersensitiv|allerg)\w*)"),
     "deliberately disabling human immune function outside a stated clinical indication"),
    (ConcernCategory.DELIBERATE_HARM,
     _p(r"\b(poison|harm|incapacitat|kill)\w*\b[^.]{0,25}\b(a |the )?(person|people|human|population|civilian)"),
     "deliberate harm to people"),
)

# Escalated to a human. These are dual-use or out-of-scope rather than
# self-evidently hazardous — a person decides, not the agent.
REVIEW_SIGNALS: tuple[tuple[ConcernCategory, re.Pattern[str], str], ...] = (
    (ConcernCategory.SELECT_AGENT_OR_TOXIN,
     _p(r"\b(ricin|botulinum|anthrax|bacillus anthracis|variola|smallpox|ebola|marburg|nipah|yersinia pestis|francisella|burkholderia mallei)\b"),
     "names a select agent or high-consequence pathogen"),
    (ConcernCategory.PATHOGEN_ENHANCEMENT,
     _p(r"\b(serial passage|passaging|directed evolution)\b[^.]{0,40}\b(virus|viral|pathogen|bacteri\w+)"),
     "laboratory evolution of a pathogen"),
    (ConcernCategory.NON_MEDICAL_USE,
     _p(r"\b(performance[- ]enhanc\w+|athletic performance|doping|muscle[- ]building|cognitive enhanc\w+|nootropic)\b"),
     "performance or cognitive enhancement rather than treatment"),
    (ConcernCategory.NON_MEDICAL_USE,
     _p(r"\b(cosmetic|recreational|lifestyle drug|for fun|get high)\b"),
     "cosmetic or recreational rather than therapeutic use"),
    (ConcernCategory.NON_MEDICAL_USE,
     _p(r"\b(environmental release|release into the (wild|environment)|gene drive|agricultural pest)\b"),
     "environmental release or gene drive"),
    (ConcernCategory.NON_MEDICAL_USE,
     _p(r"\b(bioremediation|biofuel|industrial enzyme)\b"),
     "non-medical application outside this pipeline's scope"),
)

# Phrases that establish therapeutic intent. They do NOT override a refusal —
# "treat the disease by making the virus more transmissible" is still refused —
# but they resolve an otherwise-ambiguous pathogen mention toward countermeasure
# development, which is the medical use this pipeline exists to serve.
COUNTERMEASURE_SIGNALS: tuple[re.Pattern[str], ...] = (
    _p(r"\b(inhibit|block|antagoniz|degrad|disrupt|neutraliz)\w*\b[^.]{0,40}\b(protease|polymerase|replicat\w+|entry|capsid|integrase|viral|bacterial)"),
    _p(r"\b(antiviral|antibacterial|antibiotic|antimicrobial|antifungal|antiparasitic)\b"),
    _p(r"\b(vaccine|therapeutic|treatment|cure|countermeasure|host[- ]directed)\b"),
    _p(r"\b(treat|treating|therapy for|drug for)\b"),
)


def policy_hash() -> str:
    """Stable hash of the policy, so tampering is detectable.

    The agent may not weaken its own gate. `screen` verifies this hash against
    the value captured at import, and raises rather than screening if the tables
    changed underneath it.
    """
    return content_hash({
        "version": BIORISK_POLICY_VERSION,
        "refuse": [(c.value, p.pattern, d) for c, p, d in REFUSE_SIGNALS],
        "review": [(c.value, p.pattern, d) for c, p, d in REVIEW_SIGNALS],
        "countermeasure": [p.pattern for p in COUNTERMEASURE_SIGNALS],
    })


_POLICY_HASH_AT_IMPORT = policy_hash()


class BiosafetyPolicyTampered(RuntimeError):
    """The policy tables changed after import. Screening refuses to proceed."""


class BiosafetyRefusal(RuntimeError):
    """The request was refused. Carries the assessment for the caller to record."""

    def __init__(self, assessment: BiosafetyAssessment) -> None:
        self.assessment = assessment
        super().__init__(assessment.summary())


# --------------------------------------------------------------------- models
class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BiosafetyFlag(_Base):
    """One policy signal that fired, and the text that fired it."""

    category: ConcernCategory
    tier: RiskTier
    description: str
    matched_text: str
    """The excerpt that matched, so a reviewer can judge the match themselves."""


class BiosafetyAssessment(_Base):
    """The persisted verdict. Written before any other stage runs."""

    schema_version: str = "1.0"
    policy_version: str = BIORISK_POLICY_VERSION
    policy_hash: str
    assessed_at: str
    tier: RiskTier
    flags: list[BiosafetyFlag] = Field(default_factory=list)
    countermeasure_context: bool = False
    subject: str
    rationale: str
    reviewed_by: str | None = None
    review_decision: str | None = None
    review_note: str | None = None

    @property
    def permitted(self) -> bool:
        """True only when the screen passed outright or a human cleared it."""
        if self.tier is RiskTier.PERMITTED:
            return True
        if self.tier is RiskTier.REVIEW_REQUIRED:
            return self.review_decision == "approved"
        return False

    def summary(self) -> str:
        if not self.flags:
            return f"{self.tier}: no biorisk signal fired."
        lines = [f"{self.tier}: {len(self.flags)} signal(s) fired."]
        lines += [f"  [{f.category}] {f.description} — matched: {f.matched_text!r}"
                  for f in self.flags]
        return "\n".join(lines)


# --------------------------------------------------------------------- engine
def _excerpt(text: str, match: re.Match[str], width: int = 60) -> str:
    start = max(0, match.start() - 10)
    end = min(len(text), match.end() + 10)
    excerpt = text[start:end].strip().replace("\n", " ")
    return excerpt[:width] + ("..." if len(excerpt) > width else "")


def screen(text: str, *, subject: str = "request") -> BiosafetyAssessment:
    """Screen free text against the policy. Never raises on content.

    Returns an assessment; the caller decides whether to stop. Separating the
    verdict from the enforcement means the assessment is always recordable, even
    for a refusal.
    """
    if policy_hash() != _POLICY_HASH_AT_IMPORT:
        raise BiosafetyPolicyTampered(
            "the biorisk policy changed after import; refusing to screen"
        )

    haystack = " ".join(str(text).split())
    flags: list[BiosafetyFlag] = []

    for category, pattern, description in REFUSE_SIGNALS:
        match = pattern.search(haystack)
        if match:
            flags.append(BiosafetyFlag(
                category=category, tier=RiskTier.REFUSED,
                description=description, matched_text=_excerpt(haystack, match),
            ))

    for category, pattern, description in REVIEW_SIGNALS:
        match = pattern.search(haystack)
        if match:
            flags.append(BiosafetyFlag(
                category=category, tier=RiskTier.REVIEW_REQUIRED,
                description=description, matched_text=_excerpt(haystack, match),
            ))

    countermeasure = any(p.search(haystack) for p in COUNTERMEASURE_SIGNALS)

    refused = [f for f in flags if f.tier is RiskTier.REFUSED]
    review = [f for f in flags if f.tier is RiskTier.REVIEW_REQUIRED]

    if refused:
        tier = RiskTier.REFUSED
        rationale = (
            "Refused: the request describes creating or increasing a hazardous "
            "capability. Therapeutic framing does not change this, because the "
            "capability itself is the hazard."
        )
    elif review:
        tier = RiskTier.REVIEW_REQUIRED
        if countermeasure:
            rationale = (
                "Human review required: the request names a high-consequence "
                "agent or an out-of-scope application, alongside therapeutic "
                "language. Countermeasure development is a legitimate medical "
                "use, so this is escalated rather than refused — a person "
                "decides."
            )
        else:
            rationale = (
                "Human review required: the request names a high-consequence "
                "agent or a non-medical application, with no clear therapeutic "
                "intent stated. Escalated rather than refused."
            )
    else:
        tier = RiskTier.PERMITTED
        rationale = (
            "No biorisk signal fired. This means nothing known matched, not "
            "that the work was reviewed and approved."
        )

    return BiosafetyAssessment(
        policy_hash=policy_hash(),
        assessed_at=utc_now(),
        tier=tier,
        flags=flags,
        countermeasure_context=countermeasure,
        subject=subject,
        rationale=rationale,
    )


def screen_bundle(bundle: Any) -> BiosafetyAssessment:
    """Screen every free-text field of an `InputBundle`.

    Disease contexts, hypotheses, claims, notes, and class labels are all
    concatenated: a request is the whole thing it asks for, not just its title.
    """
    parts: list[str] = []
    for candidate in getattr(bundle, "candidates", []):
        parts += [
            candidate.disease_context,
            candidate.hypothesis or "",
            candidate.target_gene,
            candidate.partner_gene,
            candidate.target_class or "",
            candidate.partner_class or "",
        ]
        parts += list(getattr(candidate.tractability, "notes", []))
        parts += list(getattr(candidate.interaction, "limitations", []))
    for record in getattr(bundle, "evidence", []):
        parts += [record.claim, record.subject, *record.limitations]
    for source in getattr(bundle, "sources", []):
        parts += [source.name, source.notes or ""]

    subjects = {c.disease_context for c in getattr(bundle, "candidates", [])}
    subject = "; ".join(sorted(subjects)) or "input bundle"
    return screen(" . ".join(p for p in parts if p), subject=subject)


def enforce(assessment: BiosafetyAssessment) -> None:
    """Raise if the assessment does not permit the run to continue."""
    if assessment.tier is RiskTier.REFUSED:
        raise BiosafetyRefusal(assessment)
    if assessment.tier is RiskTier.REVIEW_REQUIRED and not assessment.permitted:
        raise BiosafetyRefusal(assessment)
