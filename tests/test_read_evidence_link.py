"""The gate's evidence can now come from what was read, not only from a file.

`_link_from_reading` is the only path by which a paper retrieved during a run
becomes a mapped interacting region the screen will act on, so what it refuses
matters more than what it accepts. Every rejection here is a claim the project
says out loud it will not pass downstream: a contact with no region, a
functional result dressed as a contact, or a contact with something other than
the partner being modelled.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "ui"))

from pipeline_api import _link_from_reading  # noqa: E402


def paper(acc="PMC123", url="https://example.org/PMC123"):
    return SimpleNamespace(accession=acc, url=url, title="t", abstract="a")


MAPPED = {
    "contact_documented": True,
    "partner": "MED23",
    "support": "structure",
    "region": "transactivation domain, residues 374-384",
    "region_source": 1,
    "tractability": "short_linear_motif",
    "note": "the structure resolves the motif in the MED23 groove",
}


class ReadingBecomesEvidence(unittest.TestCase):
    def test_a_mapped_region_from_a_structure_paper_becomes_a_link(self):
        link = _link_from_reading("ELK1", MAPPED, [paper()], "MED23")
        self.assertIsNotNone(link)
        self.assertTrue(link.interacting_region_mapped)
        self.assertEqual(link.tf_region, MAPPED["region"])
        self.assertEqual(link.partner_gene, "MED23")
        # The claim carries the paper it came from; without a citation the
        # contract refuses to build it at all.
        self.assertEqual(link.claims[0].citations, ["https://example.org/PMC123"])
        # And it is the kind of link the screen's gate accepts.
        self.assertFalse(link.calibration_only)

    def test_region_source_selects_the_abstract_it_names(self):
        papers = [paper("A", "https://example.org/A"), paper("B", "https://example.org/B")]
        link = _link_from_reading("ELK1", {**MAPPED, "region_source": 2}, papers, "MED23")
        self.assertEqual(link.claims[0].citations, ["https://example.org/B"])

    def test_a_contact_with_no_region_is_not_a_mapped_interface(self):
        # The headline rule: a whole-protein pull-down is not a contact we can
        # model, and saying so is the point rather than a limitation.
        for absent in (None, "", "not stated", "unknown", "full length"):
            with self.subTest(region=absent):
                self.assertIsNone(_link_from_reading(
                    "ELK1", {**MAPPED, "region": absent}, [paper()], "MED23"))

    def test_a_functional_result_is_not_a_physical_contact(self):
        self.assertIsNone(_link_from_reading(
            "ELK1", {**MAPPED, "support": "genetic"}, [paper()], "MED23"))

    def test_an_unclear_reading_is_not_promoted_to_evidence(self):
        for verdict in (False, "unclear", None):
            with self.subTest(contact=verdict):
                self.assertIsNone(_link_from_reading(
                    "ELK1", {**MAPPED, "contact_documented": verdict},
                    [paper()], "MED23"))

    def test_a_contact_with_a_different_partner_does_not_open_this_site(self):
        # Real contact, real region, wrong protein: the box the screen would
        # build is on MED23, and this abstract is about somebody else's groove.
        self.assertIsNone(_link_from_reading(
            "POU2F3", {**MAPPED, "partner": "POU2AF2"}, [paper()], "MED23"))

    def test_a_paper_with_no_identifier_cannot_support_a_claim(self):
        self.assertIsNone(_link_from_reading(
            "ELK1", MAPPED, [paper(acc="", url="")], "MED23"))

    def test_tractability_travels_so_a_domain_interface_is_not_sold_as_a_motif(self):
        link = _link_from_reading(
            "RUNX2", {**MAPPED, "tractability": "folded_domain",
                      "region": "Runt and PST domains"}, [paper()], "MED23")
        self.assertEqual(link.tractability.value, "folded_domain")
        self.assertIn("folded-domain interface", " ".join(link.screening_concerns))



class WhichLinksAReadingMayReplace(unittest.TestCase):
    """A paper answers what is open, and leaves settled things alone."""

    def test_nothing_on_file_is_open(self):
        from pipeline_api import _open_to_reading
        self.assertTrue(_open_to_reading(None))

    def test_a_file_that_found_no_region_is_open(self):
        from pipeline_api import _open_to_reading
        from dependency_scout.models import MediatorLink
        self.assertTrue(_open_to_reading(MediatorLink(partner_gene="MED23")))

    def test_a_curated_mapped_region_wins_over_a_reading(self):
        from pipeline_api import _link_from_reading, _open_to_reading
        curated = _link_from_reading("ELK1", MAPPED, [paper()], "MED23")
        self.assertFalse(_open_to_reading(curated))

    def test_a_calibration_control_never_becomes_evidence(self):
        from pipeline_api import _open_to_reading
        from dependency_scout.models import MediatorLink
        self.assertFalse(_open_to_reading(
            MediatorLink(partner_gene="MED23", calibration_only=True)))


class PromotionReachesTheGate(unittest.TestCase):
    """The dict the screen's gate reads is the dict this updates."""

    def setUp(self):
        self.cfg = SimpleNamespace(partner_gene="MED23")
        self.events = []

    def emit(self, event, payload):
        self.events.append((event, payload))

    def test_a_mapped_reading_lands_in_the_evidence_and_is_announced(self):
        from pipeline_api import _promote_reading
        evidence = {}
        self.assertTrue(_promote_reading("ELK1", MAPPED, [paper()], self.cfg,
                                         evidence, self.emit))
        # This is exactly what `_structure_site_and_screen` filters on.
        link = evidence["ELK1"]
        self.assertTrue(link.interacting_region_mapped)
        self.assertFalse(link.calibration_only)
        # And the run says where it came from, rather than letting a reading
        # look like a curated file.
        note = self.events[0][1]["note"]
        self.assertIn("Read from an abstract in this run", note)

    def test_nothing_is_promoted_and_nothing_is_announced_without_a_region(self):
        from pipeline_api import _promote_reading
        evidence = {}
        self.assertFalse(_promote_reading("ELK1", {**MAPPED, "region": None},
                                          [paper()], self.cfg, evidence, self.emit))
        self.assertEqual(evidence, {})
        self.assertEqual(self.events, [])

    def test_a_curated_mapped_link_is_left_alone(self):
        from pipeline_api import _link_from_reading, _promote_reading
        curated = _link_from_reading("ELK1", MAPPED, [paper("CURATED", "https://curated")],
                                     "MED23")
        evidence = {"ELK1": curated}
        self.assertFalse(_promote_reading("ELK1", MAPPED, [paper()], self.cfg,
                                          evidence, self.emit))
        self.assertIs(evidence["ELK1"], curated)


if __name__ == "__main__":
    unittest.main()
