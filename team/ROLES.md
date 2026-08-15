# Roles — re:AGENT Track A

Four people. Two write code, one designs the solution, one decides.

| Person | GitHub | Role |
|---|---|---|
| Andrey | [@andreyf2022](https://github.com/andreyf2022) | **decides** — approvals, major calls, workflow direction |
| Kevin | [@Kyung-TaeLee](https://github.com/Kyung-TaeLee) | overall solution logic — what the pipeline should do and why |
| Vraj | [@vraj00222](https://github.com/vraj00222) | build — data in, screening out, demo surface |
| Amir | [@roknabadi](https://github.com/roknabadi) | build — agent orchestration, gates, structure, trace |

**Andrey decides.** Approvals, checkpoint sign-off, workflow changes, and any
call that changes what we are targeting. Also the source of scientific
suggestions — take them as direction, not as opinion.

**Kevin owns the solution logic.** What the stages are, which data sources,
which thresholds, how ranking works. He answers "how should this work"; Vraj and
Amir answer "how do we make it run".

**Vraj and Amir split the code.** See `team/TASKS.md`. If a task is not on that
list, it is not this weekend.

## Working rule

**Scrappy first, polish second.** Get one candidate through the whole pipe end
to end with whatever placeholders it takes, then improve the weakest link. A
polished stage that connects to nothing scores zero on Sunday.

## Judging goal

**Track A — Build an AI Scientist.** Carry a defined scientific workflow end to
end: gather evidence, use real tools and databases, generate and test
hypotheses, emit a structured output, make the reasoning easy to inspect.

- **Closing the loop:** the agent proposes the next experiment.
- **Inspectability:** show why it made each decision — including the rejections.
- **Validation:** compare against ELK1–MED23 / ELF3–MED23.
- **Sponsor tools:** Paperclip + Proto as core tools; Modal if extra compute is
  needed.

**Sunday outcome:** disease/target hypothesis → structural model → shortlist of
compounds worth testing.

## Deadlines

| When | What |
|---|---|
| Sat 9:45–10:15 PM | Overnight checkpoint — long runs must be queued |
| **Sun 10:45 AM** | **Final submission. Hard stop.** |
| Sun 12:30–2:00 PM | Demos and live judging |

Anything not demoable by Sunday 9:00 AM is not in the demo.
