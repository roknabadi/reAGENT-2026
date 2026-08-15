import unittest
from pathlib import Path
from dependency_scout.depmap import analyze_gene_effects
from dependency_scout.models import ProtoScreenSpec
from dependency_scout.proto_bridge import validate_proto_spec
from dependency_scout.ranking import rank_all

FIXTURES = Path(__file__).parent / "fixtures"


class PipelineTests(unittest.TestCase):
    def test_selective_target_passes_and_pan_essential_fails(self):
        records = analyze_gene_effects(FIXTURES / "gene_effect.csv", FIXTURES / "models.csv", context="Lung", synthetic=True)
        ranked = {r.dependency.gene: r for r in rank_all(records)}
        self.assertTrue(ranked["SELECTIVE_TF"].gate.eligible)
        self.assertFalse(ranked["PAN_ESSENTIAL"].gate.eligible)
        self.assertIn("too broad", " ".join(ranked["PAN_ESSENTIAL"].gate.failures))
        self.assertEqual(ranked["SELECTIVE_TF"].dependency.source.tier.value, "synthetic")

    def test_vina_cannot_be_requested_without_auditable_inputs(self):
        with self.assertRaises(ValueError):
            ProtoScreenSpec(candidate_gene="TF", disease_context="Lung", partner_gene="MED1",
                structure_source="pdb", pdb_id="1ABC", tools=["vina-docking"],
                public_evidence_urls=["https://example.org/public"])

    def test_bundled_public_proto_smoke_spec_compiles_natively(self):
        spec_path = Path("examples/proto_screen_spec.smoke.json")
        spec = ProtoScreenSpec.model_validate_json(spec_path.read_text())
        compiled = validate_proto_spec(spec)
        self.assertTrue(compiled["ready"])
        self.assertEqual(compiled["proto_contract"],
            "proto_tools.tools.molecular_docking.vina.VinaDockingInput")
        self.assertEqual(len(compiled["vina_input"]["ligands"]), 1)


if __name__ == "__main__": unittest.main()
