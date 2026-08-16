"""A run answers about the receptor the question named, or says it did not.

`DiscoveryConfig.partner_gene` defaulted to MED23 and no question could reach
it. Asking "which transcription factors have a mapped interacting region on
MLL" therefore ran six literature axes as `<TF>–MED23`, abstained on MED23
evidence, offered MED23's ELK1 cavity to the screen and drew MED23 in the
structure tab. Every stage was internally consistent and the whole answer was
about a different protein.

These tests pin the two halves of the fix: the question can name the receptor,
and nothing keyed to one receptor may be served under another.

Network-free. `_reviewed_symbol` is the only part that calls UniProt and it is
patched out here; the live lookup is exercised by running the pipeline, not by
the suite.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))
sys.path.insert(0, str(ROOT / "src"))

import pipeline_api as P  # noqa: E402

# What UniProt returns for these tokens, from the live lookup on 2026-08-16.
KNOWN = {"mll": ("KMT2A", "Q03164"), "kmt2a": ("KMT2A", "Q03164"),
         "med23": ("MED23", "Q9ULK4"), "brd4": ("BRD4", "O60885")}


def fake_symbol(token: str):
    return KNOWN.get(token.lower())


class PartnerFromQuestionTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(P, "_reviewed_symbol", side_effect=fake_symbol)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_a_named_receptor_replaces_the_default(self):
        gene, acc, why = P._named_partner(
            "which transcription factors have a mapped interacting region on mll",
            "MED23")
        self.assertEqual(gene, "KMT2A")
        self.assertEqual(acc, "Q03164")
        self.assertIn("mll", why)

    def test_a_synonym_resolves_to_the_reviewed_symbol(self):
        """Papers say MLL; the reviewed symbol is KMT2A. A user types the former."""
        self.assertEqual(P._named_partner("screen against MLL", "MED23")[0], "KMT2A")

    def test_no_named_receptor_keeps_the_default_and_says_so(self):
        gene, acc, why = P._named_partner(
            "which transcription factors is lung cancer dependent on?", "MED23")
        self.assertEqual(gene, "MED23")
        self.assertIsNone(acc)
        self.assertIn("MED23", why)
        self.assertTrue(why, "a default must never be silent")

    def test_an_unresolvable_token_does_not_become_a_receptor(self):
        """The failure has to be visible. Falling back quietly is the bug."""
        gene, acc, why = P._named_partner("a mapped region on FLIBBERTIGIBBET",
                                          "MED23")
        self.assertEqual(gene, "MED23")
        self.assertIn("did not resolve", why)

    def test_the_default_named_explicitly_is_not_treated_as_a_change(self):
        gene, _, _ = P._named_partner("the binding site on MED23", "MED23")
        self.assertEqual(gene, "MED23")

    def test_prose_words_are_not_mistaken_for_genes(self):
        for q in ("screen small molecules against that site",
                  "the interacting region on the literature",
                  "contacts with any of these"):
            with self.subTest(q=q):
                self.assertEqual(P._named_partner(q, "MED23")[0], "MED23")


class NoCrossPartnerResultsTests(unittest.TestCase):
    """A result computed against one receptor is not about another."""

    def test_a_recorded_ensemble_is_keyed_on_the_partner(self):
        """`runs/interfaces/<GENE>_<PARTNER>/` — not `<GENE>_MED23` always.

        With the path hardcoded, a KMT2A run read the E2F1-MED23 ensemble and
        reported it as its own structural result: right gene, wrong protein.
        """
        with mock.patch.object(P, "ROOT", ROOT):
            self.assertIsNone(P._recorded_interface("E2F1", "KMT2A"),
                              "no E2F1_KMT2A ensemble exists, so none may be served")

    def test_the_predictor_refuses_a_receptor_it_cannot_fold_against(self):
        """It only co-folds against MED23, and files output under the gene name.

        Dispatching anyway would spend GPU on MED23 and then present the answer
        under a question about something else.
        """
        events = []
        got, note = P._predict_interface("E2F1", lambda k, p: events.append((k, p)),
                                         "KMT2A")
        self.assertIsNone(got)
        self.assertIn("MED23", note)
        self.assertIn("KMT2A", note)
        self.assertTrue(any(p.get("state") == "abstained" for _k, p in events))


if __name__ == "__main__":
    unittest.main()


class TheRecordSaysWhichReceptorTheRunWasAbout(unittest.TestCase):
    """`emit.partner` is what the next question in the session compares against.

    It was stamped from the config default before the question was read, so a
    run about KMT2A recorded itself as MED23. The follow-up classifier then
    compared "is this the same receptor?" against the wrong answer and reused a
    run about a different protein.
    """

    def test_a_run_about_another_receptor_records_that_receptor(self):
        seen = {}

        def emit(event, payload):
            if event == "partner":
                seen["event"] = payload["gene"]

        rec = P._Recorder(emit)
        rec.partner = "MED23"
        # What run_live does once it has read the question.
        partner, _, _ = P._named_partner("a mapped region on MLL", "MED23")
        rec.partner = partner
        rec("partner", {"gene": partner, "uniprot": None, "why": "", 
                        "curated_pocket": False})

        self.assertEqual(partner, "KMT2A")
        self.assertEqual(seen["event"], "KMT2A")
        self.assertEqual(rec.state()["partner"], "KMT2A",
                         "the record must name the receptor the run was about")
