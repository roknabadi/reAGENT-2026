"""Where a docking box is allowed to come from, and what the screen does with it.

The defect this guards against was a category error rather than a bug: the CLI
built the receptor's search box out of integers regexed from `tf_region`, a
free-text description of where the binding motif sits on the *TF*. So a run
asked to box MED23 was handed ELK1's residues 374-384 and boxed whatever MED23
happens to have there.

Nothing downstream could catch it. Residue numbering is per-chain, 374 is a
perfectly valid MED23 index, and the resulting box was small, localised and
`defensible` by every check the module had. It produced a real box around the
wrong part of the wrong protein.

So the guard has to be at the source: `receptor_residues` admits exactly two
origins and refuses everything else, including the plausible-looking middle
cases — a consensus that was rejected, a curated pocket measured against a
different partner.

The screen half asserts the rule that follows: no defensible site, no score.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from reagent_workflow.site import (CURATED_POCKETS, build_search_site,
                                   receptor_residues)

ROOT = Path(__file__).resolve().parents[1]
RECEPTOR = ROOT / "downloads" / "9F76.cif"


class _FakeConsensus:
    def __init__(self, partner_contact_residues, blockers=()):
        self.partner_contact_residues = list(partner_contact_residues)
        self.blockers = list(blockers)


class SourceTests(unittest.TestCase):
    def test_a_consensus_supplies_partner_side_residues(self):
        r, basis, blockers = receptor_residues(
            "MED23", consensus=_FakeConsensus([339, 343, 533]))
        self.assertEqual(blockers, [])
        self.assertEqual(r, [339, 343, 533])
        self.assertIn("consensus", basis)

    def test_a_rejected_consensus_supplies_nothing(self):
        """`partner_contact_residues` can be populated on a consensus whose
        segment was rejected. Reading it anyway resurrects a result the
        consensus module already refused."""
        _, _, blockers = receptor_residues(
            "MED23", consensus=_FakeConsensus([339, 343],
                                              blockers=["register-shifted ladder"]))
        self.assertTrue(blockers)
        self.assertIn("rejected", blockers[0])

    def test_an_empty_consensus_is_a_blocker_not_an_empty_box(self):
        _, _, blockers = receptor_residues("MED23", consensus=_FakeConsensus([]))
        self.assertTrue(blockers)

    def test_no_consensus_and_no_curation_refuses(self):
        """The default. There is no third source, so the default is refusal
        rather than a fallback."""
        r, _, blockers = receptor_residues("MED23")
        self.assertEqual(r, [])
        self.assertTrue(blockers)

    def test_a_curated_pocket_is_allowed_only_for_its_own_partner(self):
        """The MED23 pocket is where ELK1 binds. Reusing it for another TF
        would assert a binding site no one has placed that TF in."""
        r, basis, blockers = receptor_residues("MED23", allow_curated=True,
                                               curated_for="ELK1")
        self.assertEqual(blockers, [])
        self.assertEqual(r, sorted(CURATED_POCKETS["MED23"]["residues"]))
        self.assertIn("Monté", basis)

        _, _, blockers = receptor_residues("MED23", allow_curated=True,
                                           curated_for="SOX10")
        self.assertTrue(blockers)
        self.assertIn("ELK1", blockers[0])

    def test_an_uncurated_receptor_refuses(self):
        _, _, blockers = receptor_residues("MED1", allow_curated=True)
        self.assertTrue(blockers)

    def test_every_curated_entry_carries_its_citation(self):
        for gene, entry in CURATED_POCKETS.items():
            self.assertTrue(entry.get("source"), f"{gene} has no citation")
            self.assertTrue(entry.get("partner"), f"{gene} names no partner")
            self.assertTrue(entry.get("residues"), f"{gene} has no residues")


@unittest.skipUnless(RECEPTOR.exists(), "9F76.cif not present")
class BoxTests(unittest.TestCase):
    def test_the_curated_pocket_builds_a_defensible_box(self):
        from reagent_workflow.interface import parse_mmcif
        residues, _, _ = receptor_residues("MED23", allow_curated=True,
                                           curated_for="ELK1")
        site = build_search_site(parse_mmcif(RECEPTOR), residues)
        self.assertTrue(site.defensible, site.blockers)
        self.assertEqual(len(site.residues), len(residues),
                         "every published pocket residue should be resolved")

    def test_residues_absent_from_the_receptor_are_refused_not_ignored(self):
        """Silently boxing whichever residues happen to be present would move
        the site without saying so."""
        from reagent_workflow.interface import parse_mmcif
        site = build_search_site(parse_mmcif(RECEPTOR), [90001, 90002])
        self.assertFalse(site.defensible)


class ScreenTests(unittest.TestCase):
    """The rule the smoke screen enforces, exercised through the script."""

    def test_the_dry_run_produces_a_record_without_docking(self):
        out = ROOT / "runs" / "_test_dry.json"
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "vina_smoke.py"),
                            "--json", str(out)],
                           capture_output=True, text=True, timeout=300)
        if not RECEPTOR.exists():
            self.skipTest("9F76.cif not present")
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        rec = json.loads(out.read_text())
        self.assertTrue(rec["site"]["defensible"])
        self.assertEqual(rec["results"], [], "a dry run must not dock anything")
        self.assertTrue(rec["limitations"], "a screen must ship its limitations")
        self.assertEqual(rec["interpretation"], "computational_prediction")
        out.unlink(missing_ok=True)

    def test_a_pose_outside_the_pocket_is_flagged(self):
        """A score with no contacts and a large offset is docking into the
        wrong place. That combination is what a bare score hides, so it has to
        surface as a flag rather than a good-looking number."""
        sys.path.insert(0, str(ROOT / "scripts"))
        from vina_smoke import pose_geometry

        class S:
            center = (0.0, 0.0, 0.0)
            size = (10.0, 10.0, 10.0)

            def contains(self, x, y, z):
                return all(abs(v) <= 5 for v in (x, y, z))

        class A:
            def __init__(self, resi, x):
                self.resi, self.x, self.y, self.z = resi, x, 0.0, 0.0
                self.element = "C"

        sdf = "\n".join(f"   40.000    0.0000    0.0000 C   0  0" for _ in range(3))
        geom = pose_geometry(sdf, S(), [A(1, 0.0), A(2, 1.0)], {1, 2})
        self.assertTrue(geom["parsed"])
        self.assertEqual(geom["n_pocket_contacts"], 0)
        self.assertFalse(geom["inside_box"])
        self.assertGreater(geom["offset_from_box_centre"], 10)

    def test_a_pose_in_the_pocket_records_its_contacts(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from vina_smoke import pose_geometry

        class S:
            center = (0.0, 0.0, 0.0)
            size = (10.0, 10.0, 10.0)

            def contains(self, x, y, z):
                return all(abs(v) <= 5 for v in (x, y, z))

        class A:
            def __init__(self, resi, x):
                self.resi, self.x, self.y, self.z = resi, x, 0.0, 0.0
                self.element = "C"

        sdf = "    0.0000    0.0000    0.0000 C   0  0"
        geom = pose_geometry(sdf, S(), [A(7, 3.0), A(8, 4.0), A(9, 20.0)], {7, 8, 9})
        self.assertEqual(geom["pocket_contacts"], [7, 8])
        self.assertTrue(geom["inside_box"])
        self.assertFalse(geom["clash"])


if __name__ == "__main__":
    unittest.main()
