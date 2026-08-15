# Roles — re:AGENT Track A

Current as of 2026-08-15. This changes as the project changes; edit it and say
so in `DECISIONS.md` if the change affects who owns a checkpoint.

| Person | GitHub | Owns |
|---|---|---|
| Andrey | [@andreyf2022](https://github.com/andreyf2022) | scientific lead / Mediator |
| Amir | [@roknabadi](https://github.com/roknabadi) | agent workflow / target prioritization / Proto |
| Kevin | [@Kyung-TaeLee](https://github.com/Kyung-TaeLee) | disease specificity / omics |
| Vraj | [@vraj00222](https://github.com/vraj00222) | software / data integration / virtual screening |
| Tatiana | — | experimental biology |
| Sasha | [@seboulatov](https://github.com/seboulatov) | experimental biology |

## Andrey — scientific lead / Mediator

- Define what makes a good target.
- Review and rank disease–TF candidates.
- Choose the final TF–Mediator pair.
- Guide structural modeling.
- Work with Amir and Vraj on virtual screening and compound selection.

## Amir — agent workflow / target prioritization / Proto

- Build the main agent workflow.
- Connect search, scoring, and decision steps.
- Integrate Proto for structural modeling and downstream screening.
- Keep the reasoning/evidence trace clear for judging.

## Kevin — disease specificity / omics

- Check whether TF dependencies are specific to a cancer type or cell state.
- Use single-cell/multi-omics and normal-cell data.
- Assess selectivity and the possible safety window.

## Vraj — software / data integration / virtual screening

- Connect Paperclip and public databases.
- Build the data pipeline and structured candidate outputs.
- Keep the demo stable and easy to inspect.
- Work with Andrey and Amir on structure preparation, screening, and ranking.

## Tatiana — experimental biology

- Sanity-check biological relevance of top candidates.
- Help define a realistic validation experiment.

## Sasha — experimental biology

- Review whether the proposed TF dependency makes biological sense.
- Help think through cell-based validation and practical follow-up.

## Judging goal

- **Closing the loop:** the agent proposes the next experiment.
- **Inspectability:** show why it made each decision.
- **Validation:** compare against known cases such as ELK1–MED23 / ELF3–MED23.
- **Sponsor tools:** Paperclip + Proto as core tools; Modal if extra compute is
  needed.
