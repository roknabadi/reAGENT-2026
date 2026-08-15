# BenchFlow comparisons

BenchFlow runs the same frozen TF–Mediator task across agent/model
configurations. It records per-trial results, canonical trajectories, token
usage, health metadata, and a matrix summary. Compare these artifacts rather
than ad hoc chat logs.

## Configure

```bash
cp benchflow/matrix.example.yaml benchflow/matrix.yaml
# Replace the model placeholders in benchflow/matrix.yaml
```

For local-only comparison, no upload configuration is needed. To publish the
job folders and trajectories, create a private Hugging Face dataset and set:

```dotenv
HF_TOKEN=your_personal_token
BENCHFLOW_HF_REPO=your-org/your-private-trace-dataset
```

BenchFlow's `--publish-hf` flag is the supported publication path. There is no
generic BenchFlow-hosted log-upload endpoint in the current CLI.

## Run

Docker must be running.

```bash
source ./activate.sh
./scripts/run_benchflow_comparison.sh first-comparison benchflow/matrix.yaml 3
```

Outputs are written under ignored directories:

```text
benchflow/jobs/<run-id>/
benchflow/artifacts/<run-id>/
```

The main comparison is `matrix-summary.json`. Each trial also contains
`result.json`, trajectory events, configuration, token/cost information, and
verifier results. Publication occurs only when `BENCHFLOW_HF_REPO` is set.

The initial verifier scores artifact completeness, provenance, explicit
limitations, and justified structural abstention. It does not prove that the
selected biological hypothesis is correct.
