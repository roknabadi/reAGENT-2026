"""A wrong majority must not silence a defensible minority.

The ELK1-MED23 control produced an ensemble where the two samples that agreed
with each other were both wrong about the interface, and the one sample that
recovered the published pocket was a singleton. The old `build_consensus`
abstained — correctly — and then reduced that singleton to a sample name in
`alternative_clusters`, discarding the only checkable placement in the run.

The fix is not to promote minorities. Every threshold here is unchanged and a
minority still cannot dock. What changed is that every cluster is scored and
kept, and that "no cluster won but one is defensible" is now a state of its own
(`ambiguous`, next action `sample_more`) rather than being flattened into a
refusal.

The geometry in these tests is synthetic and labelled as a test fixture. It is
not evidence about any interaction.
"""
from __future__ import annotations

import unittest

from reagent_workflow.interface import (Atom, ConsensusConfig, Contact,
                                        InterfaceConsensus, SampleInterface,
                                        build_consensus, score_hypothesis)
from reagent_workflow.site import build_search_site, site_from_consensus


def span(name: str, target: range, partner: range, plddt: float = 0.0
         ) -> SampleInterface:
    """A sample whose every target residue touches every partner residue."""
    return SampleInterface(sample=name, contacts=[
        Contact(target_resi=t, partner_resi=p, min_distance=3.5, sample=name,
                target_plddt=plddt, partner_plddt=plddt)
        for t in target for p in partner])


# A compact run of atoms, so a site built from a handful of residues is a
# plausible box rather than a refusal on size.
def receptor(residues: range) -> list[Atom]:
    return [Atom("A", r, "CA", "C", float(i % 3), float(i % 2), float(i))
            for i, r in enumerate(residues)]


def wrong_majority() -> list[SampleInterface]:
    """s0+s1 agree with each other on one surface; s2 is alone on another.

    Two of five is 40%: the majority is a majority and still does not converge.
    This is the ELK1-MED23 shape, with the published-pocket sample as s2.
    """
    return [span("s0", range(10, 18), range(100, 108)),
            span("s1", range(10, 17), range(100, 108)),
            span("s2", range(30, 38), range(40, 47)),
            span("s3", range(60, 68), range(70, 78)),
            span("s4", range(200, 208), range(300, 308))]


class AmbiguousStatusTests(unittest.TestCase):
    """2 wrong samples agreeing + 1 localized alternative."""

    def setUp(self):
        # The shape the control produced: five samples, a majority of two that
        # agree with each other, and singletons elsewhere. 2/5 is 40%, under the
        # unchanged 60% floor, so nothing converges.
        #
        # The samples within a cluster differ slightly, because identical
        # contact sets are one prediction handed over twice and collapse to a
        # single distinct observation.
        self.samples = wrong_majority()
        self.consensus = build_consensus(self.samples)

    def test_status_is_ambiguous_not_refused(self):
        self.assertEqual(self.consensus.status, "ambiguous")
        self.assertIsNone(self.consensus.primary_hypothesis)
        self.assertFalse(self.consensus.converged)

    def test_next_action_is_to_sample_more(self):
        self.assertEqual(self.consensus.next_action, "sample_more")

    def test_the_minority_hypothesis_is_retained_with_its_residues(self):
        """Not a list of sample names: the residues that make it checkable."""
        minority = [h for h in self.consensus.alternative_hypotheses
                    if h.sample_ids == ["s2"]]
        self.assertEqual(len(minority), 1, self.consensus.alternative_hypotheses)
        h = minority[0]
        self.assertEqual(h.partner_contact_residues, list(range(40, 47)))
        self.assertIsNotNone(h.target_segment)
        # 7, not 8: the segment is the shortest span covering 80% of the
        # reproducible contact, so the last residue is trimmed.
        self.assertEqual(h.segment_length, 7)
        self.assertTrue(h.localized)
        self.assertAlmostEqual(h.support_fraction, 0.2, places=3)

    def test_every_cluster_is_scored_not_only_the_largest(self):
        self.assertEqual(len(self.consensus.alternative_hypotheses), 4)
        for h in self.consensus.alternative_hypotheses:
            self.assertTrue(h.partner_contact_residues, h)
            self.assertTrue(h.blockers, "a non-converged hypothesis states why")

    def test_the_majority_is_still_reported_as_dominant(self):
        self.assertEqual(self.consensus.dominant_cluster_samples, 2)
        self.assertAlmostEqual(self.consensus.ensemble_support, 0.4, places=3)
        self.assertIsNone(self.consensus.target_segment,
                          "an unconverged run ships no segment at top level")
        self.assertIsNotNone(self.consensus.rejected_segment)

    def test_the_blockers_name_the_retained_alternative(self):
        self.assertTrue(any("localized" in b for b in self.consensus.blockers),
                        self.consensus.blockers)


class AmbiguousCannotDockTests(unittest.TestCase):
    def setUp(self):
        self.consensus = build_consensus(wrong_majority())
        self.free = receptor(range(38, 50)) + receptor(range(98, 110))

    def test_ambiguous_does_not_generate_a_site_automatically(self):
        site = site_from_consensus(self.consensus, self.free)
        self.assertFalse(site.defensible)
        self.assertTrue(any("ambiguous" in b for b in site.blockers), site.blockers)

    def test_refused_does_not_generate_a_site_either(self):
        refused = build_consensus([])
        self.assertEqual(refused.status, "refused")
        self.assertFalse(site_from_consensus(refused, self.free).defensible)

    def test_a_named_human_approval_can_dock_a_chosen_hypothesis(self):
        chosen = next(h for h in self.consensus.alternative_hypotheses
                      if h.sample_ids == ["s2"])
        site = site_from_consensus(self.consensus, self.free,
                                   hypothesis=chosen, approved_by="a.reviewer")
        self.assertTrue(site.defensible, site.blockers)
        self.assertIn("UNCONVERGED", site.basis)
        self.assertIn("a.reviewer", site.basis)

    def test_approval_without_a_named_hypothesis_is_refused_when_ambiguous(self):
        """Two localized alternatives here, so 'approved' alone is not a choice."""
        c = build_consensus([span("s0", range(10, 18), range(100, 108)),
                             span("s1", range(30, 38), range(40, 47))])
        localized = [h for h in c.alternative_hypotheses if h.localized]
        self.assertEqual(len(localized), 2)
        site = site_from_consensus(c, self.free, approved_by="a.reviewer")
        self.assertFalse(site.defensible)
        self.assertTrue(any("name" in b for b in site.blockers), site.blockers)


class ConvergedIsUnchangedTests(unittest.TestCase):
    """The passing path must behave exactly as it did before the change."""

    def setUp(self):
        # Distinct predictions that agree, as a real ensemble is.
        self.samples = [span(f"s{i}", range(374, 382 - i), range(339, 346))
                        for i in range(4)]
        self.consensus = build_consensus(self.samples)

    def test_converged_status_and_action(self):
        self.assertEqual(self.consensus.status, "converged")
        self.assertEqual(self.consensus.next_action, "build_search_site")
        self.assertTrue(self.consensus.converged)
        self.assertEqual(self.consensus.blockers, [])

    def test_top_level_fields_are_populated_as_before(self):
        self.assertIsNotNone(self.consensus.target_segment)
        self.assertIsNone(self.consensus.rejected_segment)
        self.assertEqual(self.consensus.partner_contact_residues,
                         list(range(339, 346)))
        self.assertEqual(self.consensus.ensemble_support, 1.0)

    def test_the_primary_hypothesis_matches_the_top_level_result(self):
        primary = self.consensus.primary_hypothesis
        self.assertIsNotNone(primary)
        self.assertEqual(primary.sample_ids, ["s0", "s1", "s2", "s3"])
        self.assertEqual(primary.partner_contact_residues,
                         self.consensus.partner_contact_residues)
        self.assertEqual(primary.target_segment, self.consensus.target_segment)
        self.assertEqual(self.consensus.alternative_hypotheses, [])

    def test_converged_docks_without_approval(self):
        site = site_from_consensus(self.consensus, receptor(range(335, 350)))
        self.assertTrue(site.defensible, site.blockers)
        self.assertNotIn("UNCONVERGED", site.basis)

    def test_it_boxes_the_same_residues_the_old_path_would_have(self):
        free = receptor(range(335, 350))
        direct = build_search_site(free, self.consensus.partner_contact_residues)
        viaconsensus = site_from_consensus(self.consensus, free)
        self.assertEqual(direct.center, viaconsensus.center)
        self.assertEqual(direct.size, viaconsensus.size)
        self.assertEqual(direct.residues, viaconsensus.residues)


class OrderInvarianceTests(unittest.TestCase):
    """Sample order is a scheduling artifact and must not reach the verdict."""

    def _samples(self):
        return wrong_majority()

    def test_shuffled_order_gives_identical_hypotheses_and_status(self):
        base = build_consensus(self._samples())
        for order in ([4, 3, 2, 1, 0], [2, 0, 4, 3, 1], [1, 3, 0, 4, 2]):
            shuffled = build_consensus([self._samples()[i] for i in order])
            with self.subTest(order=order):
                self.assertEqual(shuffled.status, base.status)
                self.assertEqual(shuffled.next_action, base.next_action)
                self.assertEqual(shuffled.model_dump(), base.model_dump())

    def test_it_holds_for_a_converged_ensemble_too(self):
        s = [span(f"s{i}", range(374, 382 - i), range(339, 346)) for i in range(4)]
        a = build_consensus(s)
        b = build_consensus(list(reversed(s)))
        self.assertEqual(a.model_dump(), b.model_dump())


class HypothesisConfidenceTests(unittest.TestCase):
    def test_interface_plddt_is_summarised_when_the_samples_carry_it(self):
        c = build_consensus([span("s0", range(10, 18), range(100, 108), plddt=80.0),
                             span("s1", range(10, 18), range(100, 108), plddt=90.0),
                             span("s2", range(30, 38), range(40, 47), plddt=50.0)])
        dominant = c.alternative_hypotheses[0]
        self.assertEqual(dominant.sample_ids, ["s0", "s1"])
        plddt = dominant.confidence["contact_plddt"]
        self.assertEqual((plddt.min, plddt.max, plddt.n), (80.0, 90.0, 2))
        self.assertAlmostEqual(plddt.mean, 85.0)

    def test_caller_supplied_metrics_are_summarised_per_hypothesis(self):
        """ipTM belongs to a sample, so it summarises to the cluster it is in.

        It is recorded, not applied: nothing selects on it.
        """
        c = build_consensus(
            wrong_majority(),
            sample_metrics={"s0": {"iptm": 0.24}, "s1": {"iptm": 0.46},
                            "s2": {"iptm": 0.48}, "s3": {"iptm": 0.44},
                            "s4": {"iptm": 0.46}})
        by_id = {tuple(h.sample_ids): h for h in c.alternative_hypotheses}
        self.assertAlmostEqual(by_id[("s0", "s1")].confidence["iptm"].mean, 0.35)
        self.assertAlmostEqual(by_id[("s2",)].confidence["iptm"].max, 0.48)

    def test_no_metrics_means_no_confidence_block_not_a_zero(self):
        c = build_consensus(wrong_majority())
        for h in c.alternative_hypotheses:
            self.assertEqual(h.confidence, {})


class ThresholdsUnchangedTests(unittest.TestCase):
    def test_the_defaults_are_the_ones_this_change_did_not_touch(self):
        cfg = ConsensusConfig()
        self.assertEqual(cfg.min_dominant_cluster_fraction, 0.60)
        self.assertEqual(cfg.min_contact_occupancy, 0.60)
        self.assertEqual(cfg.max_segment_length, 40)
        self.assertEqual(cfg.cluster_jaccard_threshold, 0.35)
        self.assertEqual(cfg.min_partner_occupancy, 0.50)

    def test_a_minority_is_still_held_to_the_same_criteria(self):
        """An extended minority is not localized, however lonely it is."""
        sprawl = span("s2", range(30, 120), range(40, 47))
        h = score_hypothesis([sprawl], 3, ConsensusConfig(), "H2")
        self.assertFalse(h.localized)
        self.assertIsNone(h.target_segment)
        self.assertIsNotNone(h.rejected_segment)
        self.assertTrue(any("beyond the 40-residue limit" in b for b in h.blockers),
                        h.blockers)

    def test_an_ensemble_of_only_sprawl_is_refused_not_ambiguous(self):
        c = build_consensus([span(f"s{i}", range(30 + 200 * i, 120 + 200 * i),
                                  range(40 + 200 * i, 47 + 200 * i))
                             for i in range(3)])
        self.assertEqual(c.status, "refused")
        self.assertEqual(c.next_action, "abstain")
        self.assertIsInstance(c, InterfaceConsensus)


if __name__ == "__main__":
    unittest.main()
