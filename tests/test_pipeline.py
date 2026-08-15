import unittest
from pathlib import Path
from dependency_scout.depmap import analyze_gene_effects
from dependency_scout.models import (Claim, EnrichmentEvidence, InterfaceTractability, Involvement,
                                     MediatorLink, RankedCandidate,
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

    def test_runx2_passes_the_contact_gate_but_declares_its_tractability_problem(self):
        """Andrey's round-01 finding: RUNX2's region is genuinely mapped, so the
        contact gate passes, but the interface is folded domains rather than a
        short linear motif. The concern must be stated, not discovered at docking."""
        runx2 = MediatorLink.model_validate_json(
            Path("examples/mediator_link_runx2_med23.json").read_text(encoding="utf-8"))
        self.assertIs(runx2.involvement, Involvement.DIRECT)
        self.assertTrue(runx2.ready_for_structural_modeling)
        self.assertTrue(any("folded-domain" in c for c in runx2.screening_concerns))

        elk1 = MediatorLink.model_validate_json(
            Path("examples/mediator_link_elk1_med23.json").read_text(encoding="utf-8"))
        self.assertIs(elk1.tractability, InterfaceTractability.SHORT_LINEAR_MOTIF)
        self.assertEqual(elk1.screening_concerns, ["calibration control, never a result"])

    def test_interface_evidence_survives_without_dependency_numbers(self):
        """Round 01 produced verified interface evidence and no DepMap numbers.
        A candidate must be representable in that state instead of being unbuildable."""
        runx2 = MediatorLink.model_validate_json(
            Path("examples/mediator_link_runx2_med23.json").read_text(encoding="utf-8"))
        c = RankedCandidate(gene="RUNX2", mediator=runx2)
        self.assertTrue(c.awaiting_dependency_data)
        self.assertEqual(c.name, "RUNX2")
        ok, reason = c.shortlistable
        self.assertFalse(ok)
        self.assertIn("awaiting quantitative dependency data", reason)
        with self.assertRaises(ValueError):  # no dependency and no gene to name it
            RankedCandidate()

    def test_calibration_controls_never_enter_the_shortlist(self):
        elk1 = MediatorLink.model_validate_json(
            Path("examples/mediator_link_elk1_med23.json").read_text(encoding="utf-8"))
        records = analyze_gene_effects(FIXTURES / "gene_effect.csv", FIXTURES / "models.csv",
                                       context="Lung", synthetic=True)
        ranked = rank_all(records)
        for c in ranked:  # give the control the best possible contact evidence
            if c.dependency.gene == "SELECTIVE_TF":
                c.mediator = elk1
        sl = build_shortlist(ranked, disease_scope="Lung")
        picked = {sl.candidates[i].name for i in sl.shortlist_indices}
        self.assertNotIn("SELECTIVE_TF", picked)
        self.assertIn("calibration control", render_markdown(sl))

    def test_pou2f3_is_the_case_the_dependency_gate_cannot_see(self):
        """POU2F3 has a mapped short-linear-motif contact with a ligandable groove
        and is, per the literature, essential in SCLC-P and dispensable in every
        other SCLC subtype. Our median-based gate cannot detect that (see
        team/FINDINGS_DEPMAP_ROUND01.md section 4), so the candidate must come out
        'direct' on evidence while honestly reporting that it has no numbers."""
        link = MediatorLink.model_validate_json(
            Path("examples/coactivator_link_pou2f3_ocat1.json").read_text(encoding="utf-8"))
        self.assertEqual(link.partner_gene, "POU2AF2")
        self.assertIs(link.involvement, Involvement.DIRECT)
        self.assertIs(link.tractability, InterfaceTractability.SHORT_LINEAR_MOTIF)
        self.assertTrue(link.ready_for_structural_modeling)
        self.assertEqual(link.screening_concerns, [])  # a real target, not a control
        self.assertIn("188-192", link.tf_region)
        # The ligandability claim is a CASTpFold computation, never experimental.
        pocket = next(c for c in link.claims if "ligandable" in c.statement)
        self.assertIs(pocket.support, SupportType.COMPUTATIONAL_PREDICTION)
        ok, reason = RankedCandidate(gene="POU2F3", mediator=link).shortlistable
        self.assertFalse(ok)
        self.assertIn("awaiting quantitative dependency data", reason)

    def test_the_contract_is_not_mediator_specific(self):
        """The pitch claims the pipeline is general across target classes. Sasha's
        cross-complex validation list (team/VALIDATION_TARGETS.md) tests that.
        A CBP/KIX contact must type identically to a Mediator one, with no schema
        change - only the type's *name* is Mediator-specific, not its behaviour."""
        kix = MediatorLink.model_validate_json(
            Path("examples/coactivator_link_foxo4_kix.json").read_text(encoding="utf-8"))
        self.assertEqual(kix.partner_gene, "CREBBP")  # not a Mediator subunit
        self.assertIs(kix.involvement, Involvement.DIRECT)
        self.assertIs(kix.tractability, InterfaceTractability.SHORT_LINEAR_MOTIF)
        self.assertTrue(kix.ready_for_structural_modeling)
        # It is a generalisation control, so it must never reach a shortlist.
        ok, reason = RankedCandidate(gene="FOXO4", mediator=kix).shortlistable
        self.assertFalse(ok)
        self.assertIn("calibration control", reason)

    def test_elk1_med23_positive_control_classifies_as_direct(self):
        """The known-good case must come out 'direct'. If this breaks, the gate is
        miscalibrated and would reject a real structurally mapped contact."""
        link = MediatorLink.model_validate_json(
            Path("examples/mediator_link_elk1_med23.json").read_text(encoding="utf-8"))
        self.assertIs(link.involvement, Involvement.DIRECT)
        self.assertTrue(link.ready_for_structural_modeling)
        self.assertIn("374-384", link.tf_region)
        self.assertTrue(all(c.citations for c in link.claims))

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
