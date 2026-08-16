"""The orthogonal control's ground truth, and the machinery that produced it.

9PFP carries two independent copies of the POU2F3-OCA-T1 complex in one
asymmetric unit. Running the contact machinery over both is a free check on the
machinery itself: if two copies of the same complex, processed identically,
disagree about which residues form the interface, then no disagreement between
a prediction and the experiment could be attributed to the prediction.

They agree exactly. That is asserted here so it stays true, and so the numbers
the eventual Boltz comparison is scored against cannot drift underneath it.

Nothing in this file runs a prediction or touches a GPU.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from reagent_workflow.interface import ConsensusConfig  # noqa: E402

STRUCTURE = ROOT / "downloads" / "9PFP.cif"


@unittest.skipUnless(STRUCTURE.exists(), "9PFP.cif not present")
class GroundTruthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pou2f3_control import ground_truth
        cls.gt = ground_truth(ConsensusConfig())

    def test_two_crystallographic_copies_give_the_same_interface(self):
        """The machinery check. Anything below 1.0 here would mean the contact
        extraction is sensitive to something other than the structure."""
        self.assertEqual(self.gt["reproducibility"]["pou2f3_jaccard"], 1.0)
        self.assertEqual(self.gt["reproducibility"]["pou2af2_jaccard"], 1.0)

    def test_the_interface_is_localised_not_the_whole_chain(self):
        """An 'interface' spanning most of a protein is a cutoff problem, not a
        finding, and would make any prediction look correct."""
        iface = self.gt["pou2f3_interface"]
        self.assertTrue(8 <= len(iface) <= 30, f"{len(iface)} residues")
        self.assertLess(len(iface), 40)

    def test_the_documented_pou2f3_motif_is_recovered(self):
        """examples/coactivator_link_pou2f3_ocat1.json records the LEELE motif
        at 188-192 from the literature. The structure-derived interface must
        contain it: two independent routes to the same residues."""
        iface = set(self.gt["pou2f3_interface"])
        self.assertTrue(iface & set(range(188, 193)),
                        f"literature motif 188-192 absent from {sorted(iface)}")

    def test_the_coactivator_side_is_a_short_linear_motif(self):
        """OCA-T1 contributes a short peptide, consistent with the
        short_linear_motif tractability the evidence file records."""
        p = self.gt["pou2af2_interface"]
        self.assertTrue(p)
        self.assertLessEqual(max(p) - min(p), 40)

    def test_the_dna_omission_is_recorded_not_hidden(self):
        """The crystal contains duplex DNA that a sequence-only prediction will
        not have. If that matters, it must already be written down before any
        result is seen, not offered afterwards as an excuse."""
        blob = " ".join(self.gt["limitations"]).lower()
        self.assertIn("dna", blob)

    def test_the_cutoff_comes_from_the_shared_config(self):
        """No control-specific threshold. Tuning one here after seeing a result
        is exactly the failure this control exists to avoid."""
        self.assertEqual(self.gt["cutoff_angstrom"],
                         ConsensusConfig().contact_cutoff_angstrom)


if __name__ == "__main__":
    unittest.main()
