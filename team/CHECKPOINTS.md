# Human-in-the-loop checkpoints

Gates on the pipeline in `PROJECT.md`. An agent may propose a candidate and fill
in the evidence, but only the named human flips a gate to `PASSED`. Do not
proceed past an `OPEN` gate — abstain and post a blocker instead.

Status: `OPEN` · `PASSED` · `FAILED` (say what to do differently).

---

## 1. TF shortlist — OPEN

**Signs off:** Kevin + Andrey
**Test:** hits are real disease dependencies, not just overexpression and not
broadly essential TFs.

- Evidence:
- Decision:
- Signed:

## 2. Mediator connection — OPEN

**Signs off:** Andrey
**Test:** the TF–Mediator relationship is genuinely supported and biologically
plausible.

- Evidence:
- Decision:
- Signed:

## 3. Hero target selection — OPEN

**Signs off:** whole team, Andrey leads
**Test:** the agent ranks candidates; humans pick the one
indication–TF–Mediator pair to pursue.

- Evidence:
- Decision:
- Signed:

## 4. Structural model review — OPEN

**Signs off:** Andrey + Vraj
**Test:** the predicted interface is credible before docking.

- Evidence:
- Decision:
- Signed:

## 5. Virtual-screen hit review — OPEN

**Signs off:** Andrey + Vraj
**Test:** top poses and chemistry inspected by hand. Docking score is not
binding affinity.

- Evidence:
- Decision:
- Signed:

---

Record the reasoning behind a `PASSED` gate in `DECISIONS.md` too — the gate
here is the receipt, the decision log is the argument.
