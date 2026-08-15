import unittest
from pathlib import Path
from dependency_scout.depmap import analyze_gene_effects
from dependency_scout.models import (Claim, EnrichmentEvidence, Involvement, MediatorLink,
                                     ProtoScreenSpec, SupportType)
from dependency_scout.ranking import rank_all
from dependency_scout.report import build_shortlist, render_markdown

try:
    from dependency_scout.proto_bridge import validate_proto_spec
except ImportError:
    validate_proto_spec = None

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

    def test_mediator_involvement_is_derived_from_claim_support(self):
        pulldown = Claim(statement="Co-IP of full-length TF and MED23",
                         support=SupportType.DIRECT_EXPERIMENTAL,
                         citations=["https://doi.org/10.0000/example"])
        # Direct evidence alone is not enough: without a mapped region it is 'indirect',
        # which is exactly the correlation-vs-contact distinction the project turns on.
        self.assertIs(MediatorLink(claims=[pulldown]).involvement, Involvement.INDIRECT)
        mapped = MediatorLink(claims=[pulldown], interacting_region_mapped=True,
                              tf_region="activation domain, residues 1-89")
        self.assertIs(mapped.involvement, Involvement.DIRECT)
        self.assertTrue(mapped.ready_for_structural_modeling)

        predicted_only = MediatorLink(claims=[Claim(statement="AlphaFold-Multimer model",
            support=SupportType.COMPUTATIONAL_PREDICTION, citations=["https://example.org/x"])])
        self.assertIs(predicted_only.involvement, Involvement.PREDICTED)
        self.assertFalse(predicted_only.ready_for_structural_modeling)
        self.assertIs(MediatorLink().involvement, Involvement.UNKNOWN)

    def test_claims_are_mandatory_for_scores_and_mapped_regions(self):
        with self.assertRaises(ValueError):  # a number with no source
            EnrichmentEvidence(literature_support=0.9)
        with self.assertRaises(ValueError):  # mapped region asserted on prediction only
            MediatorLink(interacting_region_mapped=True, tf_region="AD1",
                claims=[Claim(statement="predicted", support=SupportType.COMPUTATIONAL_PREDICTION,
                              citations=["https://example.org/x"])])
        with self.assertRaises(ValueError):  # inference with no recorded reasoning
            Claim(statement="probably binds", support=SupportType.INFERENCE,
                  citations=["https://example.org/x"])

    def test_shortlist_ranks_mapped_contacts_first_and_excludes_gate_failures(self):
        records = analyze_gene_effects(FIXTURES / "gene_effect.csv", FIXTURES / "models.csv",
                                       context="Lung", synthetic=True)
        ranked = rank_all(records)
        for c in ranked:  # give the pan-essential failure the *better* contact evidence
            if c.dependency.gene == "PAN_ESSENTIAL":
                c.mediator = MediatorLink(interacting_region_mapped=True, tf_region="AD1",
                    claims=[Claim(statement="crystal structure of the complex",
                        support=SupportType.DIRECT_EXPERIMENTAL, citations=["https://example.org/s"])])
        sl = build_shortlist(ranked, disease_scope="Lung")
        self.assertEqual(sl.candidates[0].dependency.gene, "PAN_ESSENTIAL")  # sorted first
        picked = {sl.candidates[i].dependency.gene for i in sl.shortlist_indices}
        self.assertNotIn("PAN_ESSENTIAL", picked)  # but never shortlisted: gate failed
        self.assertIn("SELECTIVE_TF", picked)
        text = render_markdown(sl)
        self.assertIn("no mapped interacting region", text)  # blocker is stated, not hidden
        self.assertIn("stops before structural_modeling", text.lower())

    @unittest.skipUnless(validate_proto_spec, "optional Proto packages are not installed")
    def test_bundled_public_proto_smoke_spec_compiles_natively(self):
        spec_path = Path("examples/proto_screen_spec.smoke.json")
        spec = ProtoScreenSpec.model_validate_json(spec_path.read_text())
        compiled = validate_proto_spec(spec)
        self.assertTrue(compiled["ready"])
        self.assertEqual(compiled["proto_contract"],
            "proto_tools.tools.molecular_docking.vina.VinaDockingInput")
        self.assertEqual(len(compiled["vina_input"]["ligands"]), 1)


if __name__ == "__main__": unittest.main()
