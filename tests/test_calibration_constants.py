"""The calibration pair must be the proteins it says it is.

`scripts/calibrate_structure.py` verified the ELK1 motif against the primary
paper line by line, and never checked that the partner accession was MED23. It
was not: O75448 is MED24, a different subunit of the same Mediator tail module,
989 aa against MED23's 1368. The ELK1-MED23 control therefore docked the ELK1
motif onto MED24 and scored the result with MED23 residue numbering, and the
numbers came back looking like an ordinary negative result rather than an error.

These tests are cheap and offline. They exist because the expensive check - a
GPU ensemble and a day of analysis - could not tell the difference.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from calibrate_structure import (  # noqa: E402
    ELK1, ELK1_GENE, ELK1_MOTIF, ELK1_MOTIF_SEQ, MED23, MED23_GENE,
    STRUCTURE_PDB, verify_accession, verify_motif,
)

# What UniProt returns for each accession, from the live fetch on 2026-08-15.
# Gene symbols come back lower-cased.
#
# Note how close the two partners are: MED24 shares 10 of MED23's 13 structures,
# because both subunits sit in the same whole-Mediator depositions. Only 9F6Y
# and 9F76 are MED23-specific, so "is this entry a chain of the structure the
# ground truth comes from" separates them and "does it appear in Mediator
# structures" would not.
MED23_ENTRY = {"genes": ["med23"],
               "pdbs": ["6H02", "7EMF", "7ENA", "7ENC", "7ENJ", "7LBM", "8GXQ",
                        "8GXS", "8T9D", "8TQW", "8TRH", "9F6Y", "9F76"]}
MED24_ENTRY = {"genes": ["med24"],
               "pdbs": ["7EMF", "7ENA", "7ENC", "7ENJ", "7LBM", "8GXQ", "8GXS",
                        "8T9D", "8TQW", "8TRH"]}
ELK1_ENTRY = {"genes": ["elk1"], "pdbs": ["1DUX", "5VVT", "9F6Y"]}


class AccessionIdentityTests(unittest.TestCase):
    def test_the_partner_accession_is_med23_not_med24(self):
        """The constant itself, pinned. O75448 sat here and was not noticed."""
        self.assertEqual(MED23, "Q9ULK4")
        self.assertEqual(ELK1, "P19419")

    def test_the_real_entries_pass(self):
        for acc, gene, entry in ((ELK1, ELK1_GENE, ELK1_ENTRY),
                                 (MED23, MED23_GENE, MED23_ENTRY)):
            with self.subTest(accession=acc):
                self.assertEqual(
                    verify_accession(acc, gene, entry["genes"], entry["pdbs"]), [])

    def test_med24_is_rejected_on_both_counts(self):
        """The actual bug, replayed: MED24's entry offered as the partner.

        Both checks have to fail independently, because either one alone can
        pass by coincidence - a paralogue can appear in the same structure, and
        a gene symbol can be right while the isoform is not.
        """
        problems = verify_accession("O75448", MED23_GENE,
                                    MED24_ENTRY["genes"], MED24_ENTRY["pdbs"])
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("MED23", problems[0])
        self.assertIn("med24", problems[0])
        self.assertIn(STRUCTURE_PDB, problems[1])

    def test_a_right_symbol_with_the_wrong_structure_still_fails(self):
        """An MED23 entry that is not in 9F6Y - a fragment or a renumbered
        isoform - is still not the chain the ground truth is numbered against."""
        problems = verify_accession(MED23, MED23_GENE, ["med23"], ["7ENA"])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(STRUCTURE_PDB, problems[0])

    def test_an_entry_with_no_gene_names_is_not_waved_through(self):
        self.assertTrue(verify_accession(MED23, MED23_GENE, [], ["9F6Y"]))


class MotifTests(unittest.TestCase):
    def _seq(self, motif: str, start: int = ELK1_MOTIF[0]) -> str:
        return "A" * (start - 1) + motif + "A" * 40

    def test_the_published_motif_passes(self):
        self.assertEqual(verify_motif(self._seq(ELK1_MOTIF_SEQ)), [])

    def test_a_register_shift_is_caught(self):
        """One residue out is the failure mode that still looks plausible."""
        shifted = self._seq(ELK1_MOTIF_SEQ, start=ELK1_MOTIF[0] + 1)
        self.assertTrue(verify_motif(shifted))

    def test_a_sequence_too_short_for_the_motif_is_caught(self):
        self.assertTrue(verify_motif("MEAP"))


if __name__ == "__main__":
    unittest.main()
