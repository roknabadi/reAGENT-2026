"""Interface consensus: calibrated against an experimental structure, then
required to refuse when an ensemble does not agree.

The calibration test is the important one. Before any predicted interface is
trusted, the geometry that will judge it has to recover a published one from the
deposited coordinates of PDB 9F6Y — the MED23 complex with the phosphorylated
ELK1 motif. If it cannot reproduce a known answer it cannot be believed on an
unknown one.

The structure file is gitignored (see team/TASKS.md); that test skips without it.
Everything else runs anywhere.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from reagent_workflow.interface import (Contact, ConsensusConfig, SampleInterface,
                                        build_consensus, cluster_interfaces,
                                        contact_occupancy, contacts_between,
                                        parse_mmcif, smallest_confident_segment)

ROOT = Path(__file__).resolve().parents[1]
CIF = ROOT / "downloads" / "9F6Y.cif"

# Monté et al. 2025 (doi:10.1038/s41467-025-59014-8): the ELK1 MED23-binding
# motif is residues 374-384, and F378 inserts into a cavity lined by these.
PUBLISHED_MED23_POCKET = {339, 343, 379, 382, 383, 533, 537}
PUBLISHED_ELK1_MOTIF = set(range(374, 385))


def sample(name: str, pairs: list[tuple[int, int]]) -> SampleInterface:
    return SampleInterface(sample=name, contacts=[
        Contact(target_resi=t, partner_resi=p, min_distance=3.8, sample=name)
        for t, p in pairs])


def span(name: str, tgt: range, par: range) -> SampleInterface:
    return sample(name, [(t, p) for t in tgt for p in par])


@unittest.skipUnless(CIF.exists(), "downloads/9F6Y.cif absent — see team/TASKS.md")
class ExperimentalCalibrationTests(unittest.TestCase):
    """§36 test_elk1_control_recovers_known_med23_neighborhood, run against the
    deposited complex rather than a fixture."""

    @classmethod
    def setUpClass(cls):
        atoms = parse_mmcif(CIF)
        cls.iface = contacts_between(atoms, target_chain="B", partner_chain="A",
                                     cutoff=4.5, sample="9F6Y")

    def test_recovers_every_published_med23_pocket_residue(self):
        missing = PUBLISHED_MED23_POCKET - self.iface.partner_residues
        self.assertEqual(missing, set(),
                         f"failed to recover published pocket residues {sorted(missing)}")

    def test_target_contacts_fall_inside_the_published_motif(self):
        stray = self.iface.target_residues - PUBLISHED_ELK1_MOTIF
        self.assertEqual(stray, set(),
                         f"contacts outside the published 374-384 motif: {sorted(stray)}")
        self.assertGreaterEqual(len(self.iface.target_residues), 6)

    def test_a_single_experimental_structure_is_not_treated_as_an_ensemble(self):
        """One structure cannot demonstrate convergence, whatever its quality."""
        c = build_consensus([self.iface])
        self.assertEqual(c.total_samples, 1)
        self.assertEqual(c.interpretation, "computational_prediction")

    def test_tightening_the_cutoff_shrinks_the_interface(self):
        atoms = parse_mmcif(CIF)
        tight = contacts_between(atoms, "B", "A", cutoff=3.5, sample="t")
        self.assertLess(len(tight.contacts), len(self.iface.contacts))
        self.assertTrue(tight.partner_residues <= self.iface.partner_residues)


class ConvergenceTests(unittest.TestCase):
    def test_consensus_interface_requires_repeated_samples(self):
        agree = [span(f"s{i}", range(216, 230), range(339, 346)) for i in range(4)]
        agree.append(span("s4", range(700, 714), range(900, 907)))   # one outlier
        c = build_consensus(agree)
        self.assertEqual(c.total_samples, 5)
        self.assertEqual(c.dominant_cluster_samples, 4)
        self.assertAlmostEqual(c.ensemble_support, 0.8)
        self.assertEqual(c.blockers, [])
        self.assertEqual(len(c.alternative_clusters), 1)

    def test_unrelated_poses_block_screening(self):
        """Five samples on five surfaces is not a hypothesis. It must refuse."""
        scattered = [span(f"s{i}", range(100 + i * 90, 110 + i * 90),
                          range(200 + i * 90, 208 + i * 90)) for i in range(5)]
        c = build_consensus(scattered)
        self.assertFalse(c.converged)
        self.assertTrue(any("did not converge" in b for b in c.blockers), c.blockers)
        self.assertLess(c.ensemble_support, 0.6)

    def test_an_empty_ensemble_refuses_rather_than_returning_a_default(self):
        c = build_consensus([])
        self.assertFalse(c.converged)
        self.assertTrue(c.blockers)

    def test_samples_with_no_contacts_do_not_count_as_agreement(self):
        c = build_consensus([SampleInterface(sample=f"s{i}") for i in range(5)])
        self.assertFalse(c.converged)
        self.assertEqual(c.dominant_cluster_samples, 0)

    def test_support_is_measured_against_every_sample_not_just_contacting_ones(self):
        """Three agreeing plus two that never touched must read 3/5, not 3/3 —
        otherwise failed samples silently inflate confidence."""
        s = [span(f"a{i}", range(10, 20), range(50, 56)) for i in range(3)]
        s += [SampleInterface(sample=f"empty{i}") for i in range(2)]
        c = build_consensus(s)
        self.assertEqual(c.total_samples, 5)
        self.assertAlmostEqual(c.ensemble_support, 0.6)


class SegmentTests(unittest.TestCase):
    def test_contact_occupancy_returns_a_tight_contiguous_segment(self):
        cluster = [span(f"s{i}", range(216, 229), range(339, 346)) for i in range(4)]
        cluster.append(sample("s4", [(216, 339), (300, 400)]))       # noisy tail
        seg = smallest_confident_segment(cluster, ConsensusConfig())
        self.assertIsNotNone(seg)
        # 216-226 already covers >=80% of reproducible contacts; the point of the
        # rule is the SMALLEST such span, so a shorter answer is the right one.
        self.assertEqual(seg.start, 216)
        self.assertLessEqual(seg.end, 228)
        self.assertGreaterEqual(seg.end, 224)
        self.assertLessEqual(seg.length, 15)
        self.assertNotIn(300, seg.occupancy)

    def test_occupancy_is_a_fraction_of_the_cluster(self):
        cluster = [span("a", range(10, 15), range(50, 53)),
                   span("b", range(10, 15), range(50, 53)),
                   span("c", range(12, 15), range(50, 53))]
        occ = contact_occupancy(cluster)
        self.assertAlmostEqual(occ[10], 2 / 3)
        self.assertAlmostEqual(occ[12], 1.0)

    def test_no_reproducible_residue_yields_no_segment(self):
        cluster = [sample(f"s{i}", [(100 + i, 200)]) for i in range(5)]
        self.assertIsNone(smallest_confident_segment(cluster, ConsensusConfig()))

    def test_thresholds_are_configuration_not_constants(self):
        cluster = [span("a", range(10, 20), range(50, 56)),
                   span("b", range(10, 20), range(50, 56)),
                   span("c", range(80, 90), range(50, 56))]
        strict = ConsensusConfig(min_dominant_cluster_fraction=0.95)
        self.assertTrue(build_consensus(cluster, strict).blockers)
        lax = ConsensusConfig(min_dominant_cluster_fraction=0.60)
        self.assertEqual(build_consensus(cluster, lax).blockers, [])


class ClusteringTests(unittest.TestCase):
    def test_agreeing_on_the_partner_alone_is_not_agreement(self):
        """Two samples on the same partner surface but different target regions
        describe different hypotheses and must not be merged."""
        a = span("a", range(10, 20), range(50, 60))
        b = span("b", range(400, 410), range(50, 60))
        self.assertEqual(len(cluster_interfaces([a, b], ConsensusConfig())), 2)

    def test_clusters_are_returned_largest_first(self):
        s = [span(f"big{i}", range(10, 20), range(50, 56)) for i in range(3)]
        s += [span(f"small{i}", range(300, 310), range(400, 406)) for i in range(2)]
        cl = cluster_interfaces(s, ConsensusConfig())
        self.assertEqual([len(c) for c in cl], [3, 2])


if __name__ == "__main__":
    unittest.main()
