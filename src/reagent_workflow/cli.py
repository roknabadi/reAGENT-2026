"""Command-line interface.

    agent init            agent structure validate    agent trace
    agent run             agent structure run         agent trace export-benchflow
    agent status          agent checkpoint show       agent trace validate-benchflow
    agent resume          agent checkpoint resolve    agent report

Every command reads and writes the run directory, so any of them can run in a
fresh process against a run started earlier.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .benchflow_export import (
    build_trace_manifest,
    export_trace,
    validate_file,
    validate_with_benchflow,
)
from .config import RunConfig
from .context import PromptTooLargeError
from .demo_export import DEMO_SCHEMA_VERSION, export_demo_json
from .improvement import evaluate
from .models import Stage
from .orchestrator import CheckpointBlocked, Orchestrator, StageError, load_bundle_file
from .scoring import rank
from .store import RunExistsError, RunLockError, RunStore, list_runs

DEFAULT_RUNS_ROOT = Path("runs")
FIXTURE_BUNDLE = Path(__file__).resolve().parent / "fixtures" / "candidates.fixture.json"


def _store(args: argparse.Namespace) -> RunStore:
    return RunStore(Path(args.runs_root), args.run_id)


def _orchestrator(args: argparse.Namespace) -> Orchestrator:
    config = RunConfig()
    if getattr(args, "allow_live_modal", False):
        config = config.model_copy(update={"allow_live_modal": True})
    return Orchestrator(_store(args), config, repo_root=Path.cwd())


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2, default=str))


# ----------------------------------------------------------------- commands
def _parse_support(pairs: list[str] | None) -> dict[str, float]:
    """`--interaction-support GENE=0.8` — a human supplies the number, not us."""
    values: dict[str, float] = {}
    for pair in pairs or []:
        gene, _, raw = pair.partition("=")
        if not gene or not raw:
            raise ValueError(f"expected GENE=VALUE, got {pair!r}")
        values[gene.strip()] = float(raw)
    return values


def _bundle_from_ranked(
    path: Path,
    support: dict[str, float],
    assays: dict[str, str],
) -> tuple[Any, list[str], list[str]]:
    """Convert `dependency-scout discover --output` JSON into a runnable bundle."""
    from dependency_scout.models import RankedCandidate  # noqa: PLC0415

    from .adapters import to_input_bundle  # noqa: PLC0415

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("candidates", [payload])
    ranked = [RankedCandidate.model_validate(row) for row in payload]
    return to_input_bundle(ranked, interaction_support=support, assays=assays)


def cmd_init(args: argparse.Namespace) -> int:
    conversion_notes: dict[str, list[str]] = {}
    if getattr(args, "from_ranked", None):
        from .adapters import ConversionRefused  # noqa: PLC0415

        try:
            bundle, refusals, losses = _bundle_from_ranked(
                Path(args.from_ranked),
                _parse_support(args.interaction_support),
                dict(pair.split("=", 1) for pair in (args.assay or [])),
            )
        except (ConversionRefused, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        conversion_notes = {"refused": refusals, "losses": losses}
        bundle_path = Path(args.from_ranked)
    else:
        bundle_path = Path(args.input) if args.input else FIXTURE_BUNDLE
        bundle = load_bundle_file(bundle_path)
    store = _store(args)
    orchestrator = Orchestrator(store, RunConfig(), repo_root=Path.cwd())
    try:
        state = orchestrator.init_run(bundle, force=args.force)
    except RunExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _print({
        "run_id": state.run_id,
        "run_dir": str(store.run_dir),
        "stage": state.stage,
        "fixture_run": state.fixture_run,
        "input": str(bundle_path),
        "candidates": len(bundle.candidates),
        **conversion_notes,
    })
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run to the hero checkpoint and stop. Structure needs a human first."""
    orchestrator = _orchestrator(args)
    with orchestrator.store.lock():
        try:
            checkpoint = orchestrator.run_until_checkpoint()
        except PromptTooLargeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
        state = orchestrator.store.load_state()
    _print({
        "run_id": state.run_id,
        "status": state.status,
        "stage": state.stage,
        "eligible": state.eligible_candidate_ids,
        "rejected": state.rejected_candidate_ids,
        "checkpoint": {
            "checkpoint_id": checkpoint.checkpoint_id,
            "requested_decision": checkpoint.requested_decision,
            "recommended_candidate_id": checkpoint.recommended_candidate_id,
            "structural_readiness": checkpoint.structural_readiness,
        },
        "next": (
            f"agent checkpoint resolve {checkpoint.checkpoint_id} "
            f"--decision approve --by <name>"
        ),
    })
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    store = _store(args)
    if not store.exists():
        print(f"error: no run {args.run_id!r} under {args.runs_root}", file=sys.stderr)
        available = list_runs(Path(args.runs_root))
        if available:
            print(f"available runs: {', '.join(available)}", file=sys.stderr)
        return 2
    orchestrator = Orchestrator(store, RunConfig(), repo_root=Path.cwd())
    state = store.load_state()
    scorecards = rank(orchestrator.load_scorecards())
    _print({
        "run_id": state.run_id,
        "stage": state.stage,
        "status": state.status,
        "fixture_run": state.fixture_run,
        "hero_candidate_id": state.hero_candidate_id,
        "open_checkpoint_id": state.open_checkpoint_id,
        "completed_stages": [str(s) for s in state.completed_stages],
        "candidates": len(state.candidate_ids),
        "eligible": state.eligible_candidate_ids,
        "rejected": state.rejected_candidate_ids,
        "ranking": [
            {"candidate_id": c.candidate_id, "total_score": c.total_score,
             "evidence_completeness": c.evidence_completeness,
             "missing": c.missing_components}
            for c in scorecards
        ],
        "manifest_drift": store.verify_manifest() if store.manifest_path.exists() else [],
        "notes": state.notes,
    })
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator(args)
    with orchestrator.store.lock():
        state = orchestrator.resume()
    _print({
        "run_id": state.run_id, "stage": state.stage, "status": state.status,
        "hero_candidate_id": state.hero_candidate_id,
        "open_checkpoint_id": state.open_checkpoint_id,
    })
    return 0


def cmd_checkpoint_show(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator(args)
    checkpoints = orchestrator.load_checkpoints()
    if args.checkpoint_id:
        checkpoints = [c for c in checkpoints if c.checkpoint_id == args.checkpoint_id]
        if not checkpoints:
            print(f"error: no checkpoint {args.checkpoint_id!r}", file=sys.stderr)
            return 2
    _print([c.model_dump(mode="json") for c in checkpoints])
    return 0


def cmd_checkpoint_resolve(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator(args)
    with orchestrator.store.lock():
        try:
            checkpoint = orchestrator.resolve_checkpoint(
                args.checkpoint_id, args.decision,
                resolved_by=args.by, note=args.note,
            )
        except (KeyError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    _print({
        "checkpoint_id": checkpoint.checkpoint_id,
        "status": checkpoint.status,
        "resolved_by": checkpoint.resolved_by,
        "resolved_at": checkpoint.resolved_at,
        "resolution": checkpoint.resolution,
    })
    return 0


def cmd_structure_validate(args: argparse.Namespace) -> int:
    """Compile Proto inputs and check deployment without running anything."""
    orchestrator = _orchestrator(args)
    with orchestrator.store.lock():
        comparison = orchestrator.run_structure(validation_only=True)
    _print({
        "validation_only": True,
        "request": str(orchestrator.store.path("structure", "request.json")),
        "verdict": comparison.verdict if comparison else "no request built",
    })
    return 0


def cmd_structure_run(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator(args)
    with orchestrator.store.lock():
        try:
            comparison = orchestrator.run_structure()
        except CheckpointBlocked as exc:
            print(f"blocked: {exc}", file=sys.stderr)
            return 4
        except StageError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if comparison is None:
        _print({"structure": "skipped", "reason": "no structural request could be built"})
        return 0
    _print({
        "verdict": comparison.verdict,
        "agreements": comparison.agreements,
        "disagreements": comparison.disagreements,
        "confidence_delta": comparison.confidence_delta,
        "caveat": comparison.caveat,
    })
    return 0


def cmd_experiment(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator(args)
    with orchestrator.store.lock():
        experiment = orchestrator.run_next_experiment()
    _print({
        "candidate_id": experiment.candidate_id,
        "scientific_question": experiment.scientific_question,
        "outcomes": len(experiment.possible_outcomes),
        "rubric_score": evaluate(
            Stage.NEXT_EXPERIMENT, experiment.model_dump(mode="json")
        ).score,
    })
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator(args)
    with orchestrator.store.lock():
        report = orchestrator.run_complete()
    if args.json:
        _print(report.model_dump(mode="json"))
    else:
        print(orchestrator.store.path("reports", "final_report.md").read_text(encoding="utf-8"))
    return 0


def cmd_export_demo(args: argparse.Namespace) -> int:
    """Emit demo.json — the single artifact the UI reads (TASKS.md #2)."""
    orchestrator = _orchestrator(args)
    path = export_demo_json(orchestrator, Path(args.output) if args.output else None)
    payload = json.loads(path.read_text(encoding="utf-8"))
    _print({
        "demo_json": str(path),
        "schema_version": payload["schema_version"],
        "candidates": len(payload["candidates"]),
        "rejected": len(payload["rejected"]),
        "evidence": len(payload["evidence"]),
        "structure_results": len(payload["structure"]["results"]),
        "poses": len(payload["compounds"]["poses"]),
        "bytes": path.stat().st_size,
        "note": "self-contained; the UI reads this file with no live calls",
    })
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    store = _store(args)
    events = store.read_jsonl(store.internal_trace_path)
    if args.event_type:
        events = [e for e in events if e.get("event_type") == args.event_type]
    if args.summary:
        counts: dict[str, int] = {}
        for event in events:
            counts[event["event_type"]] = counts.get(event["event_type"], 0) + 1
        _print({"run_id": store.run_id, "events": len(events), "by_type": counts})
        return 0
    for event in events[-args.limit:]:
        print(json.dumps(event, default=str))
    return 0


def cmd_trace_export(args: argparse.Namespace) -> int:
    store = _store(args)
    try:
        path = export_trace(store, task_id=args.task_id, model=args.model)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    manifest = build_trace_manifest(
        store, task_id=args.task_id, model=args.model, repo_root=Path.cwd()
    )
    manifest_path = store.path("traces", "trace_manifest.json")
    store.write_model(manifest_path, manifest)
    store.rebuild_manifest()
    _print({
        "benchflow_trace": str(path),
        "trace_manifest": str(manifest_path),
        "records": manifest.record_count,
        "format": manifest.trace_format,
        "internal_trace_sha256": manifest.internal_trace_sha256,
        "benchflow_trace_sha256": manifest.benchflow_trace_sha256,
        "note": "not uploaded; publication requires explicit approval",
    })
    return 0


def cmd_trace_validate(args: argparse.Namespace) -> int:
    store = _store(args)
    result = (
        validate_file(store.benchflow_trace_path) if args.offline
        else validate_with_benchflow(store.benchflow_trace_path, Path.cwd())
    )
    _print({
        "ok": result.ok,
        "records": result.record_count,
        "benchflow_version": result.benchflow_version,
        "detected_format": result.detected_format,
        "parsed_steps": result.parsed_steps,
        "errors": result.errors,
        "warnings": result.warnings,
    })
    return 0 if result.ok else 1


def cmd_evidence_show(args: argparse.Namespace) -> int:
    """Rehydrate full evidence detail that compaction dropped from a prompt."""
    orchestrator = _orchestrator(args)
    record = orchestrator.rehydrate(args.evidence_id)
    if record is None:
        print(f"error: no evidence {args.evidence_id!r} in this run", file=sys.stderr)
        return 2
    _print(record.model_dump(mode="json"))
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Fixture demo: gates, ranking, checkpoint, approval, structure, report, trace."""
    args.input = str(FIXTURE_BUNDLE)
    args.force = True
    if cmd_init(args) != 0:
        return 2

    orchestrator = _orchestrator(args)
    with orchestrator.store.lock():
        checkpoint = orchestrator.run_until_checkpoint()
        print(f"\n== hero checkpoint ({checkpoint.checkpoint_id}) ==")
        print(checkpoint.requested_decision)
        print(f"recommended: {checkpoint.recommended_candidate_id}")
        print(f"alternatives: {checkpoint.alternatives}")
        print(f"rejected: {checkpoint.rejected_candidate_ids}")

        orchestrator.resolve_checkpoint(
            checkpoint.checkpoint_id, "approve",
            resolved_by=args.by,
            note="demo approval on synthetic fixture data",
        )
        print(f"\n== approved by {args.by} ==")

        comparison = orchestrator.run_structure()
        print(f"\n== structure ({comparison.verdict if comparison else 'skipped'}) ==")
        if comparison:
            for line in comparison.agreements + comparison.disagreements:
                print(f"- {line}")

        experiment = orchestrator.run_next_experiment()
        print(f"\n== next experiment ==\n{experiment.scientific_question}")

        report = orchestrator.run_complete()
        print(f"\n== report ==\nstatus={report.status} confidence={report.confidence}")

    demo_path = export_demo_json(orchestrator)
    print(f"\n== demo.json (schema {DEMO_SCHEMA_VERSION}) ==\n{demo_path}")

    export_trace(orchestrator.store, task_id=args.task_id)
    manifest = build_trace_manifest(orchestrator.store, task_id=args.task_id, repo_root=Path.cwd())
    orchestrator.store.write_model(
        orchestrator.store.path("traces", "trace_manifest.json"), manifest
    )
    orchestrator.store.rebuild_manifest()
    result = validate_with_benchflow(orchestrator.store.benchflow_trace_path, Path.cwd())
    print(f"\n== benchflow trace ==\nvalid={result.ok} "
          f"format={result.detected_format} steps={result.parsed_steps} "
          f"benchflow={result.benchflow_version}")
    if result.errors:
        for error in result.errors:
            print(f"- {error}")
    print(f"\nrun directory: {orchestrator.store.run_dir}")
    return 0 if result.ok else 1


# ------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent", description=__doc__)
    parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    sub = parser.add_subparsers(dest="command", required=True)

    def with_run(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sp.add_argument("run_id")
        return sp

    init = with_run(sub.add_parser("init", help="create a run from an input bundle"))
    init.add_argument("--input", help="path to a candidates bundle (default: fixture)")
    init.add_argument(
        "--from-ranked",
        help="dependency-scout discover output (JSON list of RankedCandidate); "
        "converted through the adapter, refusals and losses reported",
    )
    init.add_argument(
        "--interaction-support", action="append", metavar="GENE=VALUE",
        help="human-supplied TF-Mediator support value. Without one the link "
        "stays unsupported and the gate rejects it; the adapter never invents it.",
    )
    init.add_argument(
        "--assay", action="append", metavar="GENE=NAME",
        help="assay backing the interaction support value",
    )
    init.add_argument("--force", action="store_true", help="overwrite an existing run")
    init.set_defaults(func=cmd_init)

    run = with_run(sub.add_parser("run", help="run to the hero checkpoint and stop"))
    run.set_defaults(func=cmd_run)

    status = with_run(sub.add_parser("status", help="show run state and ranking"))
    status.set_defaults(func=cmd_status)

    resume = with_run(sub.add_parser("resume", help="continue from the stage on disk"))
    resume.add_argument("--allow-live-modal", action="store_true")
    resume.set_defaults(func=cmd_resume)

    checkpoint = sub.add_parser("checkpoint", help="human gates")
    checkpoint_sub = checkpoint.add_subparsers(dest="checkpoint_command", required=True)
    cp_show = with_run(checkpoint_sub.add_parser("show"))
    cp_show.add_argument("--checkpoint-id")
    cp_show.set_defaults(func=cmd_checkpoint_show)
    cp_resolve = with_run(checkpoint_sub.add_parser("resolve"))
    cp_resolve.add_argument("checkpoint_id")
    cp_resolve.add_argument("--decision", required=True,
                            choices=["approve", "reject", "revise"])
    cp_resolve.add_argument("--by", required=True, help="name of the human deciding")
    cp_resolve.add_argument("--note")
    cp_resolve.set_defaults(func=cmd_checkpoint_resolve)

    structure = sub.add_parser("structure", help="Proto/Modal structural modelling")
    structure_sub = structure.add_subparsers(dest="structure_command", required=True)
    st_validate = with_run(structure_sub.add_parser("validate"))
    st_validate.set_defaults(func=cmd_structure_validate)
    st_run = with_run(structure_sub.add_parser("run"))
    st_run.add_argument("--allow-live-modal", action="store_true",
                        help="permit paid Modal dispatch (off by default)")
    st_run.set_defaults(func=cmd_structure_run)

    experiment = with_run(sub.add_parser("experiment", help="propose the next experiment"))
    experiment.set_defaults(func=cmd_experiment)

    report = with_run(sub.add_parser("report", help="write the final report"))
    report.add_argument("--json", action="store_true")
    report.set_defaults(func=cmd_report)

    export_demo = with_run(sub.add_parser(
        "export-demo", help="emit demo.json, the single artifact the UI reads"
    ))
    export_demo.add_argument("--output", help="write elsewhere than the run directory")
    export_demo.set_defaults(func=cmd_export_demo)

    trace = sub.add_parser("trace", help="internal and BenchFlow traces")
    trace_sub = trace.add_subparsers(dest="trace_command", required=True)

    # `agent trace <run_id>` is the common case; normalise_argv inserts "show".
    tr_show = with_run(trace_sub.add_parser("show", help="print internal trace events"))
    tr_show.add_argument("--limit", type=int, default=20)
    tr_show.add_argument("--event-type")
    tr_show.add_argument("--summary", action="store_true")
    tr_show.set_defaults(func=cmd_trace)

    tr_export = with_run(trace_sub.add_parser("export-benchflow"))
    tr_export.add_argument("--task-id", default="reagent/tf-mediator-hero")
    tr_export.add_argument("--model")
    tr_export.set_defaults(func=cmd_trace_export)

    tr_validate = with_run(trace_sub.add_parser("validate-benchflow"))
    tr_validate.add_argument("--offline", action="store_true",
                             help="structural checks only, skip the BenchFlow parser")
    tr_validate.set_defaults(func=cmd_trace_validate)

    evidence = sub.add_parser("evidence", help="inspect ingested evidence")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)
    ev_show = with_run(evidence_sub.add_parser("show"))
    ev_show.add_argument("evidence_id")
    ev_show.set_defaults(func=cmd_evidence_show)

    demo = with_run(sub.add_parser("demo", help="end-to-end fixture demo"))
    demo.add_argument("--by", default="demo-operator",
                      help="name recorded as the approving human")
    demo.add_argument("--task-id", default="reagent/tf-mediator-hero")
    demo.set_defaults(func=cmd_demo)

    return parser


TRACE_SUBCOMMANDS = {"show", "export-benchflow", "validate-benchflow"}


def normalise_argv(argv: list[str]) -> list[str]:
    """Let ``agent trace <run_id>`` mean ``agent trace show <run_id>``."""
    try:
        index = argv.index("trace")
    except ValueError:
        return argv
    following = argv[index + 1:index + 2]
    if following and following[0] not in TRACE_SUBCOMMANDS and not following[0].startswith("-"):
        return [*argv[: index + 1], "show", *argv[index + 1:]]
    return argv


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalise_argv(list(argv if argv is not None else sys.argv[1:])))
    if getattr(args, "run_id", None) is None:
        parser.error("a run_id is required")
    try:
        return int(args.func(args))
    except RunLockError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
