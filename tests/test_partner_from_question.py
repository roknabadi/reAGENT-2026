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


class PredictInstructionTests(unittest.TestCase):
    """"Run boltz2" is an instruction, and it survives being phrased as a sequel.

    "Which TFs have a mapped interacting region on MLL, and for the ones that
    do run boltz2" placed no condition the parser knew, so nothing folded. Then
    the literature found no mapped region on KMT2A, the set of "ones that do"
    was empty, and the pipeline abstained from the one step that exists to
    establish what the literature had not — with the reason "no confirmed cases
    were found", which is true and is also the reason to run the prediction.
    """

    def _run(self, predict_asked: bool, genes=("E2F1",), predicted=None):
        """Whether the structure stage folds, given the request's flags."""
        events = []
        emit = lambda k, d: events.append((k, d))  # noqa: E731
        emit.runtime = {}
        emit.genes = list(genes)
        P._structure_site_and_screen(
            emit, DummyCfg(), list(genes), {}, None,
            None, False, None, predict_asked=predict_asked)
        return events

    def test_the_flag_reaches_the_gate(self):
        """The condition that decides whether a fold is dispatched."""
        import inspect
        src = inspect.getsource(P._structure_site_and_screen)
        self.assertIn("require_site or predict_asked", src,
                      "an explicit instruction to predict must reach the gate")

    def test_the_parser_returns_the_flag(self):
        """`read_request` has to offer it, or nothing downstream can obey it."""
        from reagent_workflow import agent as A
        pol, _ = A.read_request(A.AgentTrace(), "")
        self.assertIn("predict_interface", pol)
        self.assertFalse(pol["predict_interface"], "empty request asks for nothing")


class DummyCfg:
    partner_gene = "KMT2A"

    class structure:
        top_candidates = 3
        contact_cutoff_angstrom = 4.5
        min_dominant_cluster_fraction = 0.6
        min_contact_occupancy = 0.6


class CuratedPocketOwnPartnerTests(unittest.TestCase):
    """The pocket's own partner is supported by it. Nobody else is.

    `calibration_only` keeps ELK1 out of a shortlist, which is right — it must
    never be presented as a discovered target. It was also dropping ELK1 from
    the `mapped` set, so a question about the ELK1–MED23 interface was refused a
    screen against the cavity that binds ELK1, on the grounds that the cavity is
    "not support for these candidates". The candidate was ELK1, and the
    coordinates come from the ELK1 complex. The note said both things in one
    sentence: "the cavity that binds ELK1 — calibration, not a site established
    for ELK1".
    """

    def _entry(self):
        from reagent_workflow.site import CURATED_POCKETS
        return CURATED_POCKETS["MED23"]

    def test_the_pocket_records_the_partner_it_was_mapped_with(self):
        """The whole fix rests on this field being the thing it claims to be."""
        e = self._entry()
        self.assertEqual(e["partner"], "ELK1")
        self.assertIn("9F6Y", e["source"])

    def test_support_is_granted_to_the_partner_and_nobody_else(self):
        entry = self._entry()
        partner = entry["partner"].upper()
        for genes, expected in ((["ELK1"], True), (["elk1"], True),
                                (["ELK1", "E2F1"], True),
                                (["E2F1"], False), (["FOXA1", "NKX2-1"], False),
                                ([], False)):
            with self.subTest(genes=genes):
                own = [g for g in genes if g.upper() == partner]
                self.assertEqual(bool(own), expected,
                                 f"{genes} should {'' if expected else 'not '}"
                                 "be supported by ELK1's pocket")

    def test_the_source_note_no_longer_contradicts_itself(self):
        """It said the cavity binds ELK1 and was not a site established for ELK1."""
        import inspect
        src = inspect.getsource(P._structure_site_and_screen)
        self.assertIn("own site, mapped on the", src)
        self.assertIn("for any \"\n                           \"other transcription factor",
                      src.replace("'", '"'))

    def test_the_note_keeps_the_two_structures_apart(self):
        """Mapped on the complex, applied to the apo receptor.

        Written as "screened against the free receptor" alone, the answer read
        it as "never seen with the TF bound, so only calibration" — the opposite
        of what 9F6Y is.
        """
        import inspect
        src = inspect.getsource(P._structure_site_and_screen)
        self.assertIn("mapped on the complex and applied", src)
