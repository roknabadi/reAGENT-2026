---
name: biorisk-gateway
description: >
  Use before running the pipeline on any new request, when deciding whether a
  target or disease context is in scope, or when a run is refused or escalated
  by biosecurity screening. Explains what is refused, what is escalated, what is
  explicitly permitted, and why over-blocking is its own failure.
---

# Biorisk gateway

This pipeline proposes protein targets and small molecules that engage them.
That capability serves medicine and, pointed the wrong way, serves harm. Every
run passes a screen before any other stage.

```bash
reagent-agent biorisk check --text "..."          # 0 permitted, 6 review, 7 refused
reagent-agent biorisk check --input bundle.json
reagent-agent biorisk show <run_id>               # the recorded assessment
```

## The distinction that matters

**Direction of effect and intent — not whether a pathogen is mentioned.**

- Inhibiting a pathogen protein to treat an infection is **countermeasure
  development**. That is medicine. It is permitted, and blocking it would be a
  failure of this gate, not a success.
- Creating or increasing a hazardous capability — transmissibility, virulence,
  host range, immune escape, therapeutic resistance — is **refused**, and no
  framing changes that.

A gate that refuses the word "virus" blocks antiviral drug discovery. Such a
gate gets switched off, and then nothing is screened. **Over-blocking is not the
safe direction; it is a different way to fail.**

## Three outcomes

| Tier | Meaning | What happens |
|---|---|---|
| `permitted` | nothing known fired | the run proceeds |
| `review_required` | dual-use or out of scope | run stops at a human checkpoint |
| `refused` | hazardous capability | run refused before `INGEST`; nothing else executes |

### Refused

The capability is the hazard, so stated intent does not argue it away.
"To develop a better vaccine, first increase the transmissibility of the virus"
is refused.

- increasing transmissibility, infectivity, virulence, pathogenicity, lethality
- expanding host range or tropism; gain-of-function on a pathogen
- engineering escape from immunity, vaccines, or antibodies
- engineering resistance to antibiotics, antivirals, or therapeutics
- producing or enhancing select agents and high-consequence toxins
- weaponization or mass-casualty framing
- heritable human germline modification
- deliberately disabling human immune function, or harm to people

### Escalated to a human

Dual-use or out-of-scope rather than self-evidently hazardous. **The agent does
not decide these.**

- naming a select agent or high-consequence pathogen (often legitimate: vaccine
  and therapeutic work)
- laboratory evolution of a pathogen
- performance, cognitive, or cosmetic enhancement rather than treatment
- environmental release, gene drives, agricultural or industrial applications

### Permitted

Human disease targets with therapeutic intent, including anti-infective work:
antivirals, antibacterials, antifungals, antiparasitics, host-directed therapy,
and vaccine target identification.

## What this is not

**A first-pass screen, not a guarantee.** It matches phrases. It will produce
false positives, and a determined misuse written in ordinary language will pass
it. It is one layer, not a substitute for institutional biosafety review, IBC
approval, or export-control compliance.

Read a `permitted` verdict as *"nothing known fired"* — never as *"this was
reviewed and approved"*. The assessment says so in its own rationale text, on
purpose.

## Properties worth preserving

- **It runs first.** Before `INGEST`, before any candidate, evidence, or
  structural request is written. A refused request never reaches the pipeline.
- **Refusals are recorded, not silent.** The run directory is created for a
  refused request and contains `biosafety/assessment.json` with the reason and
  the matched text — an accountability record. What it does not contain is any
  candidate or evidence.
- **The agent cannot weaken its own gate.** The policy tables are hashed at
  import; screening verifies the hash and raises `BiosafetyPolicyTampered`
  rather than screening under an edited policy. Same principle as the
  self-improvement evaluator: the thing being measured does not get to edit the
  measure.
- **Escalation beats guessing.** Ambiguity goes to a person via the same
  checkpoint machinery as the hero gate.
- **The matched text is kept**, so a reviewer can judge whether the match was
  fair rather than trusting a verdict.

## Changing the policy

`src/reagent_workflow/biorisk.py` holds the tables, the models, and the engine
in one file, deliberately — a safety gate nobody can read in a sitting is a
safety gate nobody audits.

If you add a signal, add a test alongside it, and add a **counter-test** showing
a legitimate request that must still pass. Every widening of the refuse list is
a chance to break countermeasure work, and that is the failure this design is
most concerned with.

## Related

- `SOUL.md` — the `BIORISK` stage loads `biorisk-screen-first`,
  `capability-over-framing`, `countermeasures-are-medicine`, `escalate-dont-guess`
- `skills/use-case-discovery/SKILL.md` — choosing what to work on at all
- `skills/screening/SKILL.md` — constraints on the docking stage
