"""CLI for discovery, evidence planning, and Proto validation."""
import argparse, json
from pathlib import Path
from .depmap import analyze_gene_effects
from .models import ProtoScreenSpec
from .planner import next_actions
from .proto_bridge import validate_proto_spec
from .ranking import rank_all


def dump(value, output):
    text = json.dumps(value, indent=2)
    if output: Path(output).write_text(text + "\n", encoding="utf-8")
    else: print(text)


def main():
    parser = argparse.ArgumentParser(prog="dependency-scout")
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover")
    discover.add_argument("--gene-effect", required=True); discover.add_argument("--models", required=True)
    discover.add_argument("--context", required=True); discover.add_argument("--genes")
    discover.add_argument("--context-column"); discover.add_argument("--source-version", default="DepMap Public 26Q1")
    discover.add_argument("--synthetic", action="store_true"); discover.add_argument("--output")
    plan = sub.add_parser("plan"); plan.add_argument("candidate_json"); plan.add_argument("--index", type=int, default=0); plan.add_argument("--output")
    proto = sub.add_parser("validate-proto"); proto.add_argument("spec_json"); proto.add_argument("--output")
    args = parser.parse_args()
    if args.command == "discover":
        genes = set(args.genes.split(",")) if args.genes else None
        records = analyze_gene_effects(args.gene_effect, args.models, context=args.context, genes=genes,
            context_column=args.context_column, source_version=args.source_version, synthetic=args.synthetic)
        dump([r.model_dump(mode="json") for r in rank_all(records)], args.output)
    elif args.command == "plan":
        from .models import RankedCandidate
        values = json.loads(Path(args.candidate_json).read_text(encoding="utf-8"))
        candidate = RankedCandidate.model_validate(values[args.index] if isinstance(values, list) else values)
        dump([a.model_dump(mode="json") for a in next_actions(candidate)], args.output)
    else:
        spec = ProtoScreenSpec.model_validate_json(Path(args.spec_json).read_text(encoding="utf-8"))
        dump(validate_proto_spec(spec), args.output)


if __name__ == "__main__": main()
