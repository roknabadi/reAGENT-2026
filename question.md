# re:AGENT

Four tools are ready in this workspace: Paperclip, CELLxGENE Census, Proto, and
Boltz. This file is a test drive.

## How to run it

1. Press `Cmd+J` to open the agent.
2. Pick one tool below and paste its steps one at a time, watching each one work.
3. Each tool finishes by writing a short result into `findings.tex`, which
  compiles to a PDF beside it, so you watch the agent's edits land as tracked
  changes you review.

## Paperclip: papers, trials, patents, regulatory (free key)

1. "Search PMC for prime-editing efficiency in human cells, list the top 3 papers, then open the most relevant one and add its reported efficiency to findings.tex with the citation."

If it asks for a key, grab a free one at https://paperclip.gxl.ai/keys and paste it.

## CELLxGENE Census: single-cell RNA-seq (no key)

1. "Open the Census pinned to 2025-11-08 and print the total cell and dataset counts."
2. "Count human primary blood B cells with a cheap count first, do not pull the expression matrix."
3. "Pull CD19 and MS4A1 for a small human blood B-cell slice and write the mean per gene into findings.tex."

## Proto: fold, design, and score proteins, RNA, DNA (no key for CPU tools)

1. "Run `proto-tools agent-context`, then `proto-tools list --cpu` to show the CPU tools."
2. "Fold this RNA on CPU with ViennaRNA and report the structure and MFE: GGGAAACCC."
3. "Write the tool key, input, and result into findings.tex with the method DOI from `proto-tools citation`."

## Boltz: 3D structure from sequence (no key, keep it small on CPU)

1. "Verify `boltz predict --help` runs." (set `BOLTZ_CACHE=/workspace/.boltz` first so the ~4 GB weights land on the persistent volume)
2. "Fold this 33-residue protein on CPU, msa empty, 1 recycling and 25 sampling steps: MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQ." (the first fold downloads ~4 GB of weights, so give it a few minutes; later runs are quick)
3. "Report its pLDDT and pTM from the confidence JSON into findings.tex."

## Build for the hackathon

Ready for a real project, not just a test drive? Ask "help me pick a re:AGENT
track" and I'll read `skills/reagent/SKILL.md` to pick a track, hit the judging
bar, and scope something you can demo by Sunday.
