"""Tests for the TF-Mediator agent workflow.

All fixtures are synthetic and labelled as such. They test software behaviour,
not biology.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from reagent_workflow.benchflow_export import (
    build_trace_manifest,
    export_trace,
    validate_file,
    validate_record,
    validate_with_benchflow,
)
from reagent_workflow.config import RunConfig
from reagent_workflow.demo_export import (
    DEMO_SCHEMA_VERSION,
    DemoPayload,
    export_demo_json,
)
from reagent_workflow.context import (
    ContextBudgetManager,
    EvidenceSummary,
    PromptTooLargeError,
)
from reagent_workflow.gates import evaluate_gates
from reagent_workflow.improvement import (
    EvaluatorTamperError,
    Revision,
    evaluate,
    improve_stage,
)
from reagent_workflow.ingest import InputBundle, ingest
from reagent_workflow.models import (
    ChainSpec,
    MediatorEvidence,
    RunState,
    Stage,
    StructuralModelRequest,
    StructuralModelResult,
)
from reagent_workflow.orchestrator import CheckpointBlocked, Orchestrator
from reagent_workflow.scoring import rank, score_candidate
from reagent_workflow.soul import load_soul
from reagent_workflow.store import RunExistsError, RunLockError, RunStore, redact
from reagent_workflow.structure import build_requests, compare_models, validate_request

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "src" / "reagent_workflow" / "fixtures" / "candidates.fixture.json"

# Optional integrations. A teammate who has not run scripts/setup.sh has neither,
# and these must skip rather than fail: a red suite nobody can explain means
# nobody can tell a real regression from a missing checkout.
from reagent_workflow.benchflow_export import find_benchflow_python  # noqa: E402

HAS_BENCHFLOW = find_benchflow_python(REPO_ROOT) is not None
try:
    import proto_tools  # noqa: F401
    HAS_PROTO = True
except ImportError:
    HAS_PROTO = False

needs_benchflow = unittest.skipUnless(
    HAS_BENCHFLOW, "BenchFlow interpreter not installed (run scripts/setup.sh or set BENCHFLOW_PYTHON)")
needs_proto = unittest.skipUnless(
    HAS_PROTO, "proto_tools not installed (run scripts/setup.sh)")


def load_fixture() -> InputBundle:
    return InputBundle.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


class TempRunCase(unittest.TestCase):
    """Each test gets its own runs root so nothing leaks between tests."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.runs_root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.bundle = load_fixture()

    def make_orchestrator(self, run_id: str = "test-run", **config: object) -> Orchestrator:
        store = RunStore(self.runs_root, run_id)
        return Orchestrator(
            store, RunConfig(**config), repo_root=REPO_ROOT  # type: ignore[arg-type]
        )


class IngestTests(TempRunCase):
    def test_fixture_bundle_validates_and_declares_itself(self):
        self.assertTrue(self.bundle.fixture)
        self.assertIn("SYNTHETIC", (self.bundle.fixture_note or "").upper())
        report, accepted = ingest(self.bundle)
        # Ingest validates provenance and internal consistency; gating comes later,
        # so every well-formed fixture candidate survives this stage.
        self.assertEqual(
            [c.candidate_id for c in accepted],
            [c.candidate_id for c in self.bundle.candidates],
        )
        self.assertFalse(report.rejected_candidates)
        self.assertTrue(any("FIXTURE RUN" in w for w in report.warnings))

    def test_synthetic_sources_must_be_declared_as_a_fixture(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["fixture"] = False
        with self.assertRaises(ValueError) as ctx:
            InputBundle.model_validate(payload)
        self.assertIn("synthetic", str(ctx.exception).lower())

    def test_public_source_needs_https_and_a_version(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["sources"][0] = {
            "source_id": "SRC-BAD", "name": "no version",
            "url": "https://example.org/data", "tier": "public_primary",
        }
        with self.assertRaises(ValueError):
            InputBundle.model_validate(payload)

    def test_inconsistent_selectivity_delta_is_rejected(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["candidates"][0]["dependency"]["selectivity_delta"] = -0.10
        report, accepted = ingest(InputBundle.model_validate(payload))
        self.assertNotIn("CAND-SELECTIVE", [c.candidate_id for c in accepted])
        self.assertTrue(
            any("selectivity_delta" in reason for _, reason in report.rejected_candidates)
        )


class GateTests(TempRunCase):
    def setUp(self) -> None:
        super().setUp()
        self.candidates = {c.candidate_id: c for c in self.bundle.candidates}
        self.thresholds = RunConfig().gates

    def test_selective_candidate_passes_every_gate(self):
        outcome = evaluate_gates(self.candidates["CAND-SELECTIVE"], self.thresholds)
        self.assertTrue(outcome.eligible)
        self.assertEqual(outcome.failures, [])

    def test_broadly_essential_gene_is_rejected_with_a_reason(self):
        outcome = evaluate_gates(self.candidates["CAND-BROAD"], self.thresholds)
        self.assertFalse(outcome.eligible)
        self.assertIn("broad_essentiality", outcome.failed_gates)
        self.assertTrue(any("too broad" in reason for reason in outcome.reasons))

    def test_unsupported_mediator_link_cannot_advance(self):
        outcome = evaluate_gates(
            self.candidates["CAND-UNSUPPORTED-MEDIATOR"], self.thresholds
        )
        self.assertFalse(outcome.eligible)
        self.assertIn("mediator_support", outcome.failed_gates)
        self.assertTrue(any("no named assay" in reason for reason in outcome.reasons))

    def test_every_negative_control_named_in_project_md_is_rejected(self):
        """PROJECT.md names three. A run that only says yes shows no judgment."""
        expected = {
            "CAND-BROAD": "broad_essentiality",            # pan-essential TF
            "CAND-OVEREXPRESSED": "dependency_strength",   # overexpressed, no dependency
            "CAND-PULLDOWN-ONLY": "mediator_region_mapped",  # association, not contact
        }
        for candidate_id, gate in expected.items():
            with self.subTest(candidate=candidate_id):
                outcome = evaluate_gates(self.candidates[candidate_id], self.thresholds)
                self.assertFalse(outcome.eligible)
                self.assertIn(gate, outcome.failed_gates)
                self.assertTrue(outcome.reasons)

    def test_overexpression_alone_does_not_survive_the_dependency_gate(self):
        candidate = self.candidates["CAND-OVEREXPRESSED"]
        self.assertIn("EV-DEP-F1", candidate.contradicting_evidence_ids)
        outcome = evaluate_gates(candidate, self.thresholds)
        self.assertFalse(outcome.eligible)
        self.assertIn("dependency_strength", outcome.failed_gates)
        self.assertIn("disease_specificity", outcome.failed_gates)
        # The Mediator contact is genuinely mapped; it is the dependency that fails.
        self.assertNotIn("mediator_region_mapped", outcome.failed_gates)

    def test_whole_protein_pulldown_without_a_mapped_region_is_rejected(self):
        """The negative control PROJECT.md names: association, not contact.

        This candidate has a strong, selective dependency and a real pull-down.
        It is rejected purely because no interacting region is mapped, which is
        the correlation-versus-contact distinction the project turns on.
        """
        candidate = self.candidates["CAND-PULLDOWN-ONLY"]
        self.assertLess(candidate.dependency.median_target_effect, -0.5)
        self.assertTrue(candidate.mediator.is_supported)

        outcome = evaluate_gates(candidate, self.thresholds)
        self.assertFalse(outcome.eligible)
        self.assertEqual(outcome.failed_gates, ["mediator_region_mapped"])
        self.assertTrue(any("no interacting region" in r for r in outcome.reasons))
        self.assertTrue(any("model or screen against" in r for r in outcome.reasons))

    def test_a_mapped_region_requires_direct_binding_evidence(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for candidate in payload["candidates"]:
            if candidate["candidate_id"] == "CAND-PULLDOWN-ONLY":
                candidate["mediator"]["interacting_region_mapped"] = True
                candidate["mediator"]["tf_region"] = "claimed without direct evidence"
        with self.assertRaises(ValueError) as ctx:
            InputBundle.model_validate(payload)
        self.assertIn("direct binding", str(ctx.exception))

    def test_mapped_region_must_name_the_region(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for candidate in payload["candidates"]:
            if candidate["candidate_id"] == "CAND-SELECTIVE":
                candidate["mediator"]["tf_region"] = None
        with self.assertRaises(ValueError) as ctx:
            InputBundle.model_validate(payload)
        self.assertIn("tf_region", str(ctx.exception))

    def test_only_a_mapped_contact_is_ready_for_structural_modeling(self):
        self.assertTrue(
            self.candidates["CAND-SELECTIVE"].mediator.ready_for_structural_modeling
        )
        self.assertFalse(
            self.candidates["CAND-PULLDOWN-ONLY"].mediator.ready_for_structural_modeling
        )

    def test_all_failures_are_collected_not_just_the_first(self):
        outcome = evaluate_gates(self.candidates["CAND-BROAD"], self.thresholds)
        self.assertGreaterEqual(len(outcome.failures), 2)


class ScoringTests(TempRunCase):
    def setUp(self) -> None:
        super().setUp()
        self.candidates = {c.candidate_id: c for c in self.bundle.candidates}
        self.config = RunConfig()

    def test_missing_evidence_lowers_completeness_and_never_scores(self):
        complete = score_candidate(self.candidates["CAND-SELECTIVE"], self.config)
        incomplete = score_candidate(
            self.candidates["CAND-INCOMPLETE-EVIDENCE"], self.config
        )
        self.assertGreater(complete.evidence_completeness, incomplete.evidence_completeness)
        self.assertIn("normal_cell_completeness", incomplete.missing_components)
        self.assertIn("structural_tractability", incomplete.missing_components)
        for component in incomplete.components:
            if component.missing:
                self.assertEqual(component.normalized, 0.0)
                self.assertIsNone(component.raw)

    def test_missing_weight_is_not_redistributed(self):
        card = score_candidate(self.candidates["CAND-INCOMPLETE-EVIDENCE"], self.config)
        self.assertAlmostEqual(sum(c.weight for c in card.components), 1.0, places=9)
        present = sum(c.weight for c in card.components if not c.missing)
        self.assertAlmostEqual(card.evidence_completeness, present, places=9)
        self.assertLess(card.evidence_completeness, 1.0)

    def test_every_component_carries_a_definition_and_normalization(self):
        card = score_candidate(self.candidates["CAND-SELECTIVE"], self.config)
        for component in card.components:
            self.assertTrue(component.definition.strip())
            self.assertTrue(component.normalization.strip())

    def test_ranking_prefers_the_better_supported_candidate(self):
        cards = rank([
            score_candidate(self.candidates["CAND-INCOMPLETE-EVIDENCE"], self.config),
            score_candidate(self.candidates["CAND-SELECTIVE"], self.config),
        ])
        self.assertEqual(cards[0].candidate_id, "CAND-SELECTIVE")


class StoreTests(TempRunCase):
    def test_run_is_not_silently_overwritten(self):
        orchestrator = self.make_orchestrator("dup")
        orchestrator.init_run(self.bundle)
        with self.assertRaises(RunExistsError):
            orchestrator.init_run(self.bundle)
        orchestrator.init_run(self.bundle, force=True)  # explicit is fine

    def test_lock_is_exclusive_across_processes(self):
        store = RunStore(self.runs_root, "locked")
        store.create()
        store.save_state(RunState(
            run_id="locked", stage=Stage.INGEST, status="initialized",
            created_at="2026-08-15T00:00:00Z", updated_at="2026-08-15T00:00:00Z",
            config_hash="x",
        ))
        script = (
            "import sys;"
            f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r});"
            "from reagent_workflow.store import RunStore, RunLockError;"
            f"s = RunStore({str(self.runs_root)!r}, 'locked');"
            "\ntry:\n"
            "    with s.lock():\n"
            "        print('acquired')\n"
            "except RunLockError:\n"
            "    print('blocked')\n"
        )
        with store.lock():
            result = subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
            )
        self.assertIn("blocked", result.stdout, msg=result.stderr)

    def test_atomic_write_leaves_no_partial_file(self):
        store = RunStore(self.runs_root, "atomic")
        store.create()
        target = store.path("input", "x.json")
        store.write_json(target, {"a": 1})
        store.write_json(target, {"a": 2})
        self.assertEqual(json.loads(target.read_text())["a"], 2)
        self.assertEqual(list(target.parent.glob(".tmp-*")), [])

    def test_credentials_are_redacted_but_token_counts_survive(self):
        cleaned = redact({
            "api_key": "sk-abcdefghijklmnopqrstuv",
            "note": "token hf_abcdefghijklmnopqrstuvwx here",
            "token_usage": 1234,
            "token_estimate": 99,
        })
        self.assertEqual(cleaned["api_key"], "[REDACTED]")
        self.assertNotIn("hf_abcdefghijklmnop", cleaned["note"])
        self.assertEqual(cleaned["token_usage"], 1234)
        self.assertEqual(cleaned["token_estimate"], 99)

    def test_manifest_hashes_every_artifact(self):
        orchestrator = self.make_orchestrator("manifest")
        orchestrator.init_run(self.bundle)
        orchestrator.run_ingest()
        manifest = orchestrator.store.rebuild_manifest()
        self.assertTrue(manifest.artifacts)
        self.assertEqual(orchestrator.store.verify_manifest(), [])
        (orchestrator.store.run_dir / "input" / "candidates.json").write_text("{}")
        self.assertTrue(orchestrator.store.verify_manifest())


class ContextBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.budget = ContextBudgetManager()

    def test_thresholds_escalate_with_size(self):
        self.assertEqual(str(self.budget.inspect("x" * 300).action), "ok")
        self.assertEqual(str(self.budget.inspect("x" * 26_000).action), "compact")
        self.assertEqual(str(self.budget.inspect("x" * 32_000).action), "checkpoint")
        self.assertEqual(str(self.budget.inspect("x" * 40_000).action), "fail")

    def test_oversized_prompt_fails_safely_instead_of_truncating(self):
        with self.assertRaises(PromptTooLargeError) as ctx:
            self.budget.enforce("x" * 60_000, stage="SCORE")
        self.assertIn("was not sent", str(ctx.exception))
        self.assertGreater(ctx.exception.tokens, self.budget.prompt_max)

    def test_reserve_is_held_back_for_tool_output(self):
        self.assertEqual(self.budget.reserved_tokens, 2400)
        self.assertEqual(self.budget.usable_tokens, 9600)

    def test_compaction_keeps_contradictions_and_missing_evidence(self):
        summaries = [
            EvidenceSummary(f"EV-{i}", "a supporting claim " * 30, "computed",
                            "https://example.org/a", value=1.0, unit="x")
            for i in range(40)
        ]
        summaries.append(EvidenceSummary(
            "EV-CONTRA", "this contradicts the others", "observed",
            "https://example.org/c", supports=False, contradicts=["EV-0"],
        ))
        summaries.append(EvidenceSummary(
            "EV-MISSING", "not ingested", "inference", "", missing=True,
        ))
        lines, dropped = self.budget.compact_evidence(summaries, budget_tokens=400)
        kept = " ".join(lines)
        self.assertIn("EV-CONTRA", kept)
        self.assertIn("EV-MISSING", kept)
        self.assertNotIn("EV-CONTRA", dropped)
        self.assertTrue(dropped)

    def test_compaction_preserves_urls_values_and_units(self):
        summary = EvidenceSummary(
            "EV-1", "claim", "computed", "https://example.org/x",
            value=-1.05, unit="gene_effect_score",
        )
        rendered = summary.render()
        self.assertIn("https://example.org/x", rendered)
        self.assertIn("-1.05", rendered)
        self.assertIn("gene_effect_score", rendered)
        self.assertIn("EV-1", rendered)


class SoulTests(unittest.TestCase):
    def test_only_relevant_rules_load_per_stage(self):
        soul = load_soul()
        self.assertEqual(soul.version, "1.0")
        structure_rules = soul.for_stage(Stage.STRUCTURE)
        self.assertTrue(any("Human review" in rule for rule in structure_rules))
        self.assertTrue(any("Model agreement" in rule for rule in structure_rules))
        self.assertFalse(any("Every score has" in rule for rule in structure_rules))
        self.assertLess(len(structure_rules), len(soul.rules))


class StructureTests(TempRunCase):
    def setUp(self) -> None:
        super().setUp()
        self.candidates = {c.candidate_id: c for c in self.bundle.candidates}
        self.config = RunConfig()

    @needs_proto
    def test_requests_use_the_installed_proto_contracts(self):
        requests = build_requests(self.candidates["CAND-SELECTIVE"], self.config)
        self.assertEqual(len(requests), 3)
        for request in requests:
            report = validate_request(request)
            self.assertTrue(report["valid"], msg=report["blockers"])
            self.assertIn(
                report["proto_contract"],
                {
                    "proto_tools.tools.structure_prediction.boltz2.Boltz2Input",
                    "proto_tools.tools.structure_prediction.esmfold2.ESMFold2Input",
                },
            )
            self.assertIn("modal_target", report)

    def test_model_roles_are_enforced(self):
        chains = [
            ChainSpec(chain_id="A", role="transcription_factor", sequence="MKALW"),
            ChainSpec(chain_id="B", role="mediator_subunit", sequence="MQVLA"),
        ]
        # ESMFold2 may not be asked to predict an interface.
        with self.assertRaises(ValueError):
            StructuralModelRequest(
                request_id="r", candidate_id="c", model="esmfold2",
                proto_tool_key="esmfold2-prediction", purpose="complex_interface",
                chains=chains, input_hash="h",
            )
        with self.assertRaises(ValueError):
            StructuralModelRequest(
                request_id="r", candidate_id="c", model="esmfold2",
                proto_tool_key="esmfold2-prediction", purpose="monomer_confidence",
                chains=chains, input_hash="h",
            )
        # Boltz2 takes the complex.
        request = StructuralModelRequest(
            request_id="r", candidate_id="c", model="boltz2",
            proto_tool_key="boltz2-prediction", purpose="complex_interface",
            chains=chains, input_hash="h",
        )
        self.assertEqual(request.purpose, "complex_interface")

    def test_boltz2_gets_the_complex_and_esmfold2_gets_monomers(self):
        requests = build_requests(self.candidates["CAND-SELECTIVE"], self.config)
        boltz = [r for r in requests if r.model == "boltz2"]
        esm = [r for r in requests if r.model == "esmfold2"]
        self.assertEqual(len(boltz), 1)
        self.assertEqual(len(boltz[0].chains), 2)
        self.assertEqual(len(esm), 2)
        for request in esm:
            self.assertEqual(len(request.chains), 1)
            self.assertEqual(request.purpose, "monomer_confidence")

    def test_no_request_is_built_without_sequences(self):
        requests = build_requests(
            self.candidates["CAND-INCOMPLETE-EVIDENCE"], self.config
        )
        self.assertEqual(requests, [])

    def test_no_structural_request_without_a_mapped_contact_point(self):
        """Modelling an unmapped association would invent the interface."""
        candidate = self.candidates["CAND-PULLDOWN-ONLY"].model_copy(deep=True)
        candidate.tractability.tf_sequence = "MSDLQTPVSEAKALLQRLEEAG"
        candidate.tractability.mediator_sequence = "MAQVSTLLDRLNQAGDKVAQQL"
        self.assertEqual(build_requests(candidate, self.config), [])

    def test_chain_sequences_must_be_amino_acids(self):
        with self.assertRaises(ValueError):
            ChainSpec(chain_id="A", role="transcription_factor", sequence="MKZZ123")

    def test_comparison_never_calls_agreement_validation(self):
        boltz = StructuralModelResult(
            request_id="b", candidate_id="c", model="boltz2", status="cached",
            source="fixture", confidence={"plddt": 0.8, "iptm": 0.7},
            input_hash="h", output_hash="o",
        )
        esm = StructuralModelResult(
            request_id="e", candidate_id="c", model="esmfold2", status="cached",
            source="fixture", confidence={"plddt": 0.82}, input_hash="h2",
            output_hash="o2", chain_map={"A": "transcription_factor"},
        )
        comparison = compare_models("c", boltz, [esm])
        self.assertEqual(comparison.verdict, "consistent")
        self.assertIn("not experimental validation", comparison.caveat)
        self.assertEqual(str(comparison.interpretation), "predicted")

    def test_comparison_reports_insufficient_when_a_model_is_missing(self):
        comparison = compare_models("c", None, [])
        self.assertEqual(comparison.verdict, "insufficient")

    def test_live_dispatch_is_off_by_default(self):
        self.assertFalse(RunConfig().allow_live_modal)
        self.assertTrue(RunConfig().require_human_approval_before_structure)


class ImprovementTests(unittest.TestCase):
    def test_bounded_to_two_iterations_and_records_before_after(self):
        thin = {
            "scientific_question": "short question",
            "perturbation": "", "readout": "",
            "positive_controls": [], "negative_controls": [],
            "possible_outcomes": [], "limitations": [],
        }
        calls: list[str] = []

        def rerun(revision: Revision) -> dict:
            calls.append(revision.criterion)
            return dict(thin)  # never improves

        _, iterations = improve_stage(
            run_id="r", stage=Stage.NEXT_EXPERIMENT, artifact=thin, rerun=rerun
        )
        self.assertLessEqual(len(iterations), 2)
        self.assertEqual(iterations[0].before_score, iterations[0].after_score)
        self.assertFalse(iterations[0].applied)
        self.assertIn("did not improve", iterations[0].stopping_reason)

    def test_iteration_limit_cannot_be_raised_past_two(self):
        artifact = {"scientific_question": "x", "possible_outcomes": [], "limitations": []}
        _, iterations = improve_stage(
            run_id="r", stage=Stage.NEXT_EXPERIMENT, artifact=artifact,
            rerun=lambda rev: {**artifact, "limitations": ["a", "b"]},
            max_iterations=99,
        )
        self.assertLessEqual(len(iterations), 2)

    def test_revision_target_outside_the_allowed_set_is_refused(self):
        with self.assertRaises(ValueError):
            Revision(criterion="x", target="scoring_rules", instruction="raise my score")
        with self.assertRaises(ValueError):
            Revision(criterion="x", target="evaluator", instruction="pass me")

    def test_tampering_with_the_rubric_raises_instead_of_scoring(self):
        import reagent_workflow.improvement as improvement

        artifact = {
            "scientific_question": "a" * 200, "perturbation": "p", "readout": "r",
            "positive_controls": ["p"], "negative_controls": ["n"],
            "possible_outcomes": [], "limitations": [],
        }

        def cheating_rerun(revision: Revision) -> dict:
            # The agent rewrites its own evaluator to make itself pass.
            improvement.RUBRICS[Stage.NEXT_EXPERIMENT] = ()
            return artifact

        with self.assertRaises(EvaluatorTamperError):
            improve_stage(
                run_id="r", stage=Stage.NEXT_EXPERIMENT,
                artifact=artifact, rerun=cheating_rerun,
            )
        improvement.RUBRICS[Stage.NEXT_EXPERIMENT] = improvement.NEXT_EXPERIMENT_RUBRIC


class WorkflowTests(TempRunCase):
    def test_fixture_run_reaches_the_hero_checkpoint_and_stops(self):
        orchestrator = self.make_orchestrator("hero")
        orchestrator.init_run(self.bundle)
        checkpoint = orchestrator.run_until_checkpoint()
        state = orchestrator.store.load_state()

        self.assertEqual(state.status, "awaiting_human")
        self.assertEqual(state.stage, Stage.HERO_CHECKPOINT)
        self.assertEqual(checkpoint.status, "open")
        self.assertEqual(checkpoint.recommended_candidate_id, "CAND-SELECTIVE")
        self.assertIsNone(state.hero_candidate_id)
        self.assertTrue(checkpoint.contradicting_evidence_ids)
        self.assertTrue(checkpoint.alternatives)
        self.assertIn("CAND-BROAD", checkpoint.rejected_candidate_ids)

    def test_structure_is_blocked_without_human_approval(self):
        orchestrator = self.make_orchestrator("blocked")
        orchestrator.init_run(self.bundle)
        orchestrator.run_until_checkpoint()
        with self.assertRaises(CheckpointBlocked):
            orchestrator.run_structure()

    def test_rejections_are_written_with_reasons(self):
        orchestrator = self.make_orchestrator("rejects")
        orchestrator.init_run(self.bundle)
        orchestrator.run_until_checkpoint()
        rejections = orchestrator.store.read_jsonl(orchestrator.rejections_path)
        by_id = {r["candidate_id"]: r for r in rejections}
        self.assertIn("CAND-BROAD", by_id)
        self.assertIn("CAND-UNSUPPORTED-MEDIATOR", by_id)
        self.assertTrue(by_id["CAND-BROAD"]["reasons"])
        self.assertIn("broad_essentiality", by_id["CAND-BROAD"]["failed_gates"])

    def test_checkpoint_resolution_requires_a_named_human(self):
        orchestrator = self.make_orchestrator("named")
        orchestrator.init_run(self.bundle)
        checkpoint = orchestrator.run_until_checkpoint()
        with self.assertRaises(ValueError):
            orchestrator.resolve_checkpoint(
                checkpoint.checkpoint_id, "approve", resolved_by="  "
            )
        with self.assertRaises(ValueError):
            orchestrator.resolve_checkpoint(
                checkpoint.checkpoint_id, "sneak-past", resolved_by="someone"
            )

    def test_a_resolved_checkpoint_cannot_be_silently_reversed(self):
        orchestrator = self.make_orchestrator("once")
        orchestrator.init_run(self.bundle)
        checkpoint = orchestrator.run_until_checkpoint()
        orchestrator.resolve_checkpoint(
            checkpoint.checkpoint_id, "approve", resolved_by="tester"
        )
        with self.assertRaises(ValueError):
            orchestrator.resolve_checkpoint(
                checkpoint.checkpoint_id, "reject", resolved_by="tester"
            )

    def test_rejecting_the_hero_checkpoint_abstains(self):
        orchestrator = self.make_orchestrator("abstain")
        orchestrator.init_run(self.bundle)
        checkpoint = orchestrator.run_until_checkpoint()
        orchestrator.resolve_checkpoint(
            checkpoint.checkpoint_id, "reject", resolved_by="tester",
            note="not convinced by the fixture evidence",
        )
        report = orchestrator.run_complete()
        self.assertEqual(report.status, "abstained")
        self.assertIsNone(report.hero_candidate_id)

    def test_run_resumes_from_disk_in_a_fresh_orchestrator(self):
        first = self.make_orchestrator("resume")
        first.init_run(self.bundle)
        checkpoint = first.run_until_checkpoint()
        first.resolve_checkpoint(
            checkpoint.checkpoint_id, "approve", resolved_by="tester"
        )
        del first

        # Nothing is carried over but the run directory.
        second = self.make_orchestrator("resume")
        state = second.resume()
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.hero_candidate_id, "CAND-SELECTIVE")
        self.assertTrue(second.store.path("reports", "final_report.md").exists())
        self.assertTrue(second.store.path("structure", "comparison.json").exists())

    def test_cached_structural_results_work_without_live_modal(self):
        orchestrator = self.make_orchestrator("cached")
        orchestrator.init_run(self.bundle)
        checkpoint = orchestrator.run_until_checkpoint()
        orchestrator.resolve_checkpoint(
            checkpoint.checkpoint_id, "approve", resolved_by="tester"
        )
        comparison = orchestrator.run_structure()
        self.assertIsNotNone(comparison)
        assert comparison is not None
        self.assertIn(comparison.verdict, {"consistent", "inconsistent"})

        results = [
            json.loads(p.read_text())
            for p in orchestrator.store.path("structure").rglob("*.json")
            if p.name.startswith("CAND-")
        ]
        self.assertEqual(len(results), 3)
        for result in results:
            self.assertEqual(result["status"], "cached")
            self.assertEqual(result["source"], "fixture")
            self.assertFalse(orchestrator.config.allow_live_modal)
            self.assertTrue(any("SYNTHETIC" in x for x in result["limitations"]))

    def test_validation_only_mode_runs_nothing(self):
        orchestrator = self.make_orchestrator("validate-only")
        orchestrator.init_run(self.bundle)
        orchestrator.run_until_checkpoint()
        comparison = orchestrator.run_structure(validation_only=True)
        self.assertIsNotNone(comparison)
        self.assertTrue(orchestrator.store.path("structure", "request.json").exists())

    def test_context_artifacts_support_rehydration(self):
        orchestrator = self.make_orchestrator("context")
        orchestrator.init_run(self.bundle)
        orchestrator.run_until_checkpoint()
        index_path = orchestrator.store.path("context", "evidence_index.json")
        digest_path = orchestrator.store.path("context", "digest.md")
        self.assertTrue(index_path.exists())
        self.assertTrue(digest_path.exists())
        index = json.loads(index_path.read_text())
        self.assertIn("EV-CONTRA-A1", index["evidence"])
        record = orchestrator.rehydrate("EV-CONTRA-A1")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertFalse(record.supports)

    @needs_proto
    def test_internal_trace_reconstructs_every_decision(self):
        orchestrator = self.make_orchestrator("trace")
        orchestrator.init_run(self.bundle)
        checkpoint = orchestrator.run_until_checkpoint()
        orchestrator.resolve_checkpoint(
            checkpoint.checkpoint_id, "approve", resolved_by="tester"
        )
        orchestrator.run_structure()
        orchestrator.run_next_experiment()
        orchestrator.run_complete()

        events = orchestrator.tracer.read_events()
        types = {e.event_type for e in events}
        for required in (
            "run.started", "stage.started", "stage.completed",
            "evidence.accepted", "candidate.gated", "candidate.rejected",
            "candidate.scored", "checkpoint.created", "checkpoint.resolved",
            "proto.request_validated", "structure.cache_hit",
            "structure.boltz2_output", "structure.esmfold2_output",
            "structure.model_comparison", "improvement.iteration",
            "experiment.proposed", "report.written", "run.completed",
        ):
            self.assertIn(required, types, msg=f"missing trace event {required}")

        for event in events:
            self.assertTrue(event.config_hash)
            self.assertEqual(event.run_id, "trace")
            self.assertEqual(event.schema_version, "1.0")

        # Every rejection is reconstructable from the trace alone.
        rejected = {
            e.detail["candidate_id"] for e in events
            if e.event_type == "candidate.rejected"
        }
        self.assertEqual(
            rejected,
            {
                "CAND-BROAD",                 # pan-essential
                "CAND-OVEREXPRESSED",         # overexpressed, no dependency
                "CAND-PULLDOWN-ONLY",         # association, no mapped contact
                "CAND-UNSUPPORTED-MEDIATOR",  # no assay, no source
            },
        )

    def test_improvement_iteration_is_recorded(self):
        orchestrator = self.make_orchestrator("improve")
        orchestrator.init_run(self.bundle)
        checkpoint = orchestrator.run_until_checkpoint()
        orchestrator.resolve_checkpoint(
            checkpoint.checkpoint_id, "approve", resolved_by="tester"
        )
        orchestrator.run_structure()
        orchestrator.run_next_experiment()
        iterations = orchestrator.store.read_jsonl(orchestrator.improvement_path)
        self.assertTrue(iterations)
        self.assertLessEqual(len(iterations), 2)
        first = iterations[0]
        for key in ("stage", "iteration", "critique", "proposed_revision",
                    "changed_artifact", "before_score", "after_score",
                    "stopping_reason"):
            self.assertIn(key, first)
        self.assertGreaterEqual(first["after_score"], first["before_score"])

    def test_final_report_labels_the_fixture_run(self):
        orchestrator = self.make_orchestrator("report")
        orchestrator.init_run(self.bundle)
        checkpoint = orchestrator.run_until_checkpoint()
        orchestrator.resolve_checkpoint(
            checkpoint.checkpoint_id, "approve", resolved_by="tester"
        )
        orchestrator.run_structure()
        orchestrator.run_next_experiment()
        report = orchestrator.run_complete()
        self.assertTrue(report.fixture_run)
        self.assertEqual(report.confidence, "low")
        self.assertTrue(any("FIXTURE RUN" in x for x in report.limitations))
        markdown = orchestrator.store.path("reports", "final_report.md").read_text()
        self.assertIn("FIXTURE RUN", markdown)
        self.assertIn("not experimental validation", markdown)


class BenchFlowExportTests(TempRunCase):
    def _completed_run(self, run_id: str = "bf") -> Orchestrator:
        orchestrator = self.make_orchestrator(run_id)
        orchestrator.init_run(self.bundle)
        checkpoint = orchestrator.run_until_checkpoint()
        orchestrator.resolve_checkpoint(
            checkpoint.checkpoint_id, "approve", resolved_by="tester"
        )
        orchestrator.run_structure()
        orchestrator.run_next_experiment()
        orchestrator.run_complete()
        return orchestrator

    def test_export_is_generated_and_validates_structurally(self):
        orchestrator = self._completed_run()
        path = export_trace(orchestrator.store)
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "benchflow_trace.jsonl")
        result = validate_file(path)
        self.assertTrue(result.ok, msg=result.errors)
        self.assertEqual(result.record_count, 1)

    def test_export_preserves_internal_event_ids_and_artifact_paths(self):
        orchestrator = self._completed_run("bf-links")
        path = export_trace(orchestrator.store)
        record = json.loads(path.read_text().strip())
        internal_ids = {e.event_id for e in orchestrator.tracer.read_events()}
        exported_ids = {step["internal_event_id"] for step in record["steps"]}
        self.assertEqual(exported_ids, internal_ids)
        artifacts = {
            ref for step in record["steps"] for ref in step.get("output_refs", [])
        }
        self.assertIn("reports/final_report.md", artifacts)
        self.assertEqual(
            record["internal_trace"]["path"], "traces/internal_trace.jsonl"
        )

    @needs_benchflow
    def test_benchflow_itself_parses_the_trace(self):
        orchestrator = self._completed_run("bf-real")
        export_trace(orchestrator.store)
        result = validate_with_benchflow(
            orchestrator.store.benchflow_trace_path, REPO_ROOT
        )
        self.assertTrue(result.ok, msg=result.errors + result.warnings)
        self.assertEqual(result.detected_format, "opentraces")
        self.assertIsNotNone(result.benchflow_version)
        self.assertGreater(result.parsed_steps or 0, 0)

    @needs_benchflow
    def test_trace_manifest_carries_hashes_and_run_metadata(self):
        orchestrator = self._completed_run("bf-manifest")
        export_trace(orchestrator.store)
        manifest = build_trace_manifest(orchestrator.store, repo_root=REPO_ROOT)
        self.assertEqual(manifest.run_id, "bf-manifest")
        self.assertEqual(manifest.task_id, "reagent/tf-mediator-hero")
        self.assertEqual(manifest.completion_status, "completed")
        self.assertEqual(len(manifest.internal_trace_sha256 or ""), 64)
        self.assertEqual(len(manifest.benchflow_trace_sha256 or ""), 64)
        self.assertTrue(manifest.tool_calls)
        self.assertEqual(manifest.trace_format, "opentraces")
        self.assertIsNotNone(manifest.benchflow_version)
        self.assertTrue(manifest.environment)

    def test_a_broken_record_is_reported_not_accepted(self):
        errors = validate_record({"schema_version": "0.3"}, 1)
        self.assertTrue(errors)
        errors = validate_record({
            "schema_version": "0.3", "trace_id": "t", "task": {}, "agent": {"name": "a"},
            "outcome": {"status": "success"},
            "steps": [{"step_id": 5, "internal_event_id": "e"}],
        }, 1)
        self.assertTrue(any("sequential" in e for e in errors))

    def test_no_credentials_appear_in_the_exported_trace(self):
        orchestrator = self._completed_run("bf-clean")
        path = export_trace(orchestrator.store)
        blob = path.read_text()
        for marker in ("hf_", "sk-", "ghp_", "BEGIN PRIVATE KEY", "TAMARIND_API_KEY"):
            self.assertNotIn(marker, blob)


class FrozenBenchFlowTaskTests(TempRunCase):
    """The workflow's hero artifact uses the frozen task's schema.

    The frozen task in ``benchflow/tasks/tf-mediator-hero`` is unchanged. These
    tests confirm the workflow emits an artifact that task can read, and that a
    synthetic run still fails its public-provenance assertions — a fixture must
    never satisfy a public-evidence check.
    """

    VERIFIER = (
        REPO_ROOT / "benchflow" / "tasks" / "tf-mediator-hero"
        / "verifier" / "test_output.py"
    )

    def _hero_artifact(self) -> Path:
        orchestrator = self.make_orchestrator("frozen")
        orchestrator.init_run(self.bundle)
        checkpoint = orchestrator.run_until_checkpoint()
        orchestrator.resolve_checkpoint(
            checkpoint.checkpoint_id, "approve", resolved_by="tester"
        )
        orchestrator.run_structure()
        orchestrator.run_next_experiment()
        orchestrator.run_complete()
        return orchestrator.store.path("reports", "hero_hypothesis.json")

    def _verifier_results(self, workspace: Path) -> dict[str, str]:
        import importlib.util  # noqa: PLC0415

        spec = importlib.util.spec_from_file_location("frozen_verifier", self.VERIFIER)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        os.environ["BENCHFLOW_WORKSPACE"] = str(workspace)
        try:
            spec.loader.exec_module(module)
            results = {}
            for name in [n for n in dir(module) if n.startswith("test_")]:
                try:
                    getattr(module, name)()
                    results[name] = "pass"
                except AssertionError as exc:
                    results[name] = f"fail: {exc}"
            return results
        finally:
            os.environ.pop("BENCHFLOW_WORKSPACE", None)

    def test_hero_artifact_satisfies_the_frozen_task_structure(self):
        artifact = self._hero_artifact()
        workspace = artifact.parent
        results = self._verifier_results(workspace)

        for name in (
            "test_required_sections",
            "test_hero_is_complete",
            "test_alternative_and_limitations_are_explicit",
            "test_structural_abstention_is_valid",
            "test_no_proprietary_project_material_is_named",
        ):
            self.assertEqual(results[name], "pass", msg=results[name])

    def test_synthetic_run_fails_the_public_provenance_checks(self):
        artifact = self._hero_artifact()
        results = self._verifier_results(artifact.parent)
        self.assertTrue(results["test_evidence_has_public_provenance"].startswith("fail"))
        self.assertIn("synthetic://", results["test_evidence_has_public_provenance"])

    def test_frozen_task_definition_is_unmodified(self):
        task = REPO_ROOT / "benchflow" / "tasks" / "tf-mediator-hero" / "task.md"
        text = task.read_text(encoding="utf-8")
        self.assertIn('schema_version: "1.3"', text)
        self.assertIn("name: reagent/tf-mediator-hero", text)
        self.assertTrue(self.VERIFIER.exists())


class DemoJsonTests(TempRunCase):
    """TASKS.md #2 — the single artifact the UI reads.

    Done-when: the shape is agreed with Vraj, one file loads standalone, and
    there are no live calls. These tests pin the parts the six UI screens in
    band 3 depend on.
    """

    def _completed(self, run_id: str = "demo-json") -> Orchestrator:
        orchestrator = self.make_orchestrator(run_id)
        orchestrator.init_run(self.bundle)
        checkpoint = orchestrator.run_until_checkpoint()
        orchestrator.resolve_checkpoint(
            checkpoint.checkpoint_id, "approve", resolved_by="tester"
        )
        orchestrator.run_structure()
        orchestrator.run_next_experiment()
        orchestrator.run_complete()
        return orchestrator

    def test_file_loads_standalone_with_only_the_json_module(self):
        orchestrator = self._completed()
        path = export_demo_json(orchestrator)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], DEMO_SCHEMA_VERSION)
        self.assertTrue(payload["generated_at"])

    def test_every_top_level_key_is_always_present(self):
        """Screen #9's done-when is "no crash on missing fields"."""
        expected = {
            "schema_version", "generated_at", "run", "summary", "candidates",
            "rejected", "evidence", "sources", "structure", "compounds",
            "checkpoints", "next_experiment", "limitations",
        }
        # A run stopped at the checkpoint has no structure, report, or experiment.
        partial = self.make_orchestrator("partial")
        partial.init_run(self.bundle)
        partial.run_until_checkpoint()
        payload = json.loads(export_demo_json(partial).read_text(encoding="utf-8"))

        self.assertEqual(set(payload), expected)
        # The absent stages are empty, not missing.
        self.assertEqual(payload["structure"]["results"], [])
        self.assertEqual(payload["structure"]["status"], "none")
        self.assertEqual(payload["compounds"]["poses"], [])
        self.assertIsNone(payload["next_experiment"]["scientific_question"])
        self.assertTrue(payload["candidates"])

    def test_rejection_view_has_a_gate_and_a_reason_for_every_control(self):
        """Screen #11 — the one that wins. Both negative controls, with reasons."""
        orchestrator = self._completed("rejections")
        payload = json.loads(export_demo_json(orchestrator).read_text(encoding="utf-8"))
        rejected = {row["candidate_id"]: row for row in payload["rejected"]}

        for control in ("CAND-BROAD", "CAND-OVEREXPRESSED", "CAND-PULLDOWN-ONLY"):
            with self.subTest(candidate=control):
                self.assertIn(control, rejected)
                failures = rejected[control]["gate_failures"]
                self.assertTrue(failures, "a rejected candidate must say which gate fired")
                for failure in failures:
                    self.assertTrue(failure["gate"])
                    self.assertTrue(failure["reason"])
                self.assertFalse(rejected[control]["gate_eligible"])

    def test_every_claim_carries_its_support_type_and_citations(self):
        """Screen #10 — each claim traceable to a real URL."""
        orchestrator = self._completed("claims")
        payload = json.loads(export_demo_json(orchestrator).read_text(encoding="utf-8"))
        hero = next(c for c in payload["candidates"] if c["status"] == "hero")
        self.assertTrue(hero["claims"])
        valid_support = {
            "direct_experimental", "genetic_functional",
            "computational_prediction", "inference",
        }
        for claim in hero["claims"]:
            self.assertIn(claim["support"], valid_support)
            self.assertTrue(claim["statement"])
            self.assertTrue(claim["citations"])

    def test_candidate_table_has_what_screen_nine_sorts_on(self):
        orchestrator = self._completed("table")
        payload = json.loads(export_demo_json(orchestrator).read_text(encoding="utf-8"))
        ranks = [row["rank"] for row in payload["candidates"]]
        self.assertEqual(ranks, sorted(ranks))
        for row in payload["candidates"]:
            for key in ("transcription_factor", "disease_context", "involvement", "score"):
                self.assertIsNotNone(row[key], msg=f"{row['candidate_id']} missing {key}")
            self.assertIsNotNone(row["selectivity"]["selectivity_delta"])

    def test_one_screen_summary_chain_covers_disease_to_compounds(self):
        """Screen #14 — the hypothesis chain, each hop labelled."""
        orchestrator = self._completed("chain")
        payload = json.loads(export_demo_json(orchestrator).read_text(encoding="utf-8"))
        steps = [link["step"] for link in payload["summary"]["chain"]]
        self.assertEqual(
            steps,
            ["disease", "transcription_factor", "mediator_contact", "interface", "compounds"],
        )
        for link in payload["summary"]["chain"]:
            self.assertIn(
                link["status"], {"established", "predicted", "missing", "blocked"}
            )

    def test_compounds_slot_exists_before_the_docking_run_does(self):
        """Screen #13 can be built today; the docking stage fills poses later."""
        orchestrator = self._completed("compounds")
        payload = json.loads(export_demo_json(orchestrator).read_text(encoding="utf-8"))
        compounds = payload["compounds"]
        self.assertIn(compounds["status"], {"not_run", "blocked", "screened"})
        self.assertEqual(compounds["poses"], [])
        self.assertIn("not a binding affinity", compounds["caveat"])
        self.assertTrue(compounds["blockers"])

    def test_predictions_are_never_labelled_as_observations(self):
        orchestrator = self._completed("labels")
        payload = json.loads(export_demo_json(orchestrator).read_text(encoding="utf-8"))
        self.assertTrue(payload["structure"]["caveats"])
        joined = " ".join(payload["structure"]["caveats"]).lower()
        self.assertIn("not experimental validation", joined)
        interface = next(
            link for link in payload["summary"]["chain"] if link["step"] == "interface"
        )
        self.assertEqual(interface["status"], "predicted")

    def test_fixture_run_is_flagged_for_the_ui(self):
        orchestrator = self._completed("flagged")
        payload = json.loads(export_demo_json(orchestrator).read_text(encoding="utf-8"))
        self.assertTrue(payload["run"]["fixture_run"])
        self.assertIn("FIXTURE", (payload["summary"]["headline_limitation"] or "").upper())

    def test_no_credentials_reach_the_ui_payload(self):
        orchestrator = self._completed("clean-demo")
        blob = export_demo_json(orchestrator).read_text(encoding="utf-8")
        for marker in ("hf_", "sk-", "ghp_", "TAMARIND_API_KEY", "BEGIN PRIVATE KEY"):
            self.assertNotIn(marker, blob)

    def test_payload_round_trips_through_its_own_model(self):
        """The shape is frozen: extra="forbid" catches an undeclared field."""
        orchestrator = self._completed("roundtrip")
        payload = json.loads(export_demo_json(orchestrator).read_text(encoding="utf-8"))
        self.assertEqual(
            DemoPayload.model_validate(payload).model_dump(mode="json"), payload
        )


class MediatorContractParityTests(unittest.TestCase):
    """The workflow and `dependency_scout` must mean the same thing by "direct".

    `MediatorEvidence.ready_for_structural_modeling` and
    `MediatorLink.ready_for_structural_modeling` are separate implementations of
    one rule. This checks them against Andrey's round-01 typed examples, which
    carry verified sources and human classifications: ELK1 and RUNX2 have mapped
    regions, CEBPB is the whole-protein pull-down rejected in DECISIONS.md, and
    ETV1 is computational only. If these two ever disagree, one half of the repo
    is advancing a contact the other half rejects.
    """

    EXAMPLES = sorted((REPO_ROOT / "examples").glob("mediator_link_*_med23.json"))

    def test_examples_are_present(self):
        self.assertGreaterEqual(len(self.EXAMPLES), 4)

    def test_both_contracts_agree_on_every_verified_example(self):
        from dependency_scout.models import MediatorLink  # noqa: PLC0415

        expected = {
            "cebpb": False,  # whole-protein pull-down, no region mapped
            "elk1": True,    # positive control, 3.0 A structure, motif mapped
            "etv1": False,   # computational prediction only
            "runx2": True,   # mapped domains, direct experimental
        }
        seen = {}
        for path in self.EXAMPLES:
            payload = json.loads(path.read_text(encoding="utf-8"))
            link = MediatorLink.model_validate(payload)
            tf = path.stem.split("_")[2]
            mirrored = MediatorEvidence(
                transcription_factor=tf.upper(),
                mediator_subunit=payload.get("partner_gene", "MED23"),
                interaction_support=0.8,
                interaction_type=(
                    "direct_binding" if link.interacting_region_mapped else "complex_member"
                ),
                assay="see claims",
                interacting_region_mapped=link.interacting_region_mapped,
                tf_region=payload.get("tf_region"),
                evidence_ids=["e1"],
                source_id="s1",
            )
            with self.subTest(candidate=tf):
                self.assertEqual(
                    mirrored.ready_for_structural_modeling,
                    link.ready_for_structural_modeling,
                    msg=f"{tf}: the two contracts disagree on structural readiness",
                )
                if tf in expected:
                    self.assertEqual(
                        link.ready_for_structural_modeling, expected[tf],
                        msg=f"{tf}: classification drifted from the round-01 handoff",
                    )
            seen[tf] = link.ready_for_structural_modeling

        self.assertTrue(set(expected) <= set(seen), f"missing examples: {set(expected) - set(seen)}")


class CliTests(TempRunCase):
    def _run(self, *argv: str) -> tuple[int, str]:
        from reagent_workflow.cli import main
        import contextlib, io  # noqa: PLC0415

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["--runs-root", str(self.runs_root), *argv])
        return code, buffer.getvalue()

    @needs_benchflow
    def test_demo_command_runs_end_to_end(self):
        code, output = self._run("demo", "cli-demo", "--by", "tester")
        self.assertEqual(code, 0, msg=output)
        self.assertIn("hero checkpoint", output)
        self.assertIn("valid=True", output)
        self.assertIn("format=opentraces", output)

    def test_status_and_trace_commands_read_a_finished_run(self):
        self._run("demo", "cli-read", "--by", "tester")
        code, output = self._run("status", "cli-read")
        self.assertEqual(code, 0)
        status = json.loads(output)
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["hero_candidate_id"], "CAND-SELECTIVE")
        self.assertEqual(status["manifest_drift"], [])

        code, output = self._run("trace", "cli-read", "--summary")
        self.assertEqual(code, 0)
        self.assertGreater(json.loads(output)["events"], 20)

    def test_structure_run_exits_blocked_without_approval(self):
        self._run("init", "cli-block")
        self._run("run", "cli-block")
        code, _ = self._run("structure", "run", "cli-block")
        self.assertEqual(code, 4)

    @needs_benchflow
    def test_validate_benchflow_exit_code_reflects_validity(self):
        self._run("demo", "cli-valid", "--by", "tester")
        code, output = self._run("trace", "validate-benchflow", "cli-valid")
        self.assertEqual(code, 0, msg=output)
        self.assertTrue(json.loads(output)["ok"])

    def test_unknown_run_reports_cleanly(self):
        code, _ = self._run("status", "does-not-exist")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
