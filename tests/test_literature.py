"""Per-axis retrieval: parsing, triage, and the difference between a null result
and a broken tool.

That distinction is the point of this file. A search that legitimately finds
nothing and a search that failed because the CLI is missing or the key is wrong
must never look the same — that exact confusion has already shipped twice here,
once as a parser matching a changed output format and once as an API key that
silently returned empty for every query.

No network: `search` is exercised through its parser, and the live path is
covered by the end-to-end run.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from reagent_workflow.literature import (AXES, CandidateEvidence, Paper, _parse,
                                         gather, mentions, search)

CURRENT = (
    "Found 2 papers  [s_abc]\n\n"
    "  1. IRF4 as an Oncogenic Master Transcription Factor\n"
    "     Some Author, Another Author\n"
    "     PMC9941528 · Cancers · 2023-02-07\n"
    "     https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9941528/\n"
    '     "IRF4 knockdown reduces myeloma cell survival."\n\n'
    "  2. Structural determinants of the IRF4/DNA homodimeric complex\n"
    "     PMC1234567 · JMB · 2024-01-01\n"
    "     https://doi.org/10.1000/x\n"
    '     "A crystal structure of the complex."\n'
)


class ParsingTests(unittest.TestCase):
    def test_parses_accession_url_and_abstract(self):
        got = _parse(CURRENT, "driver", "q")
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0].accession, "PMC9941528")
        self.assertEqual(got[1].accession, "PMC1234567")
        self.assertTrue(got[0].url.startswith("http"))
        self.assertIn("knockdown", got[0].abstract)
        self.assertEqual(got[0].axis, "driver")

    def test_a_doi_url_does_not_become_the_accession(self):
        """The accession and the link are different fields; conflating them
        breaks every citation the interface renders."""
        got = _parse(CURRENT, "structure", "q")
        self.assertNotIn("doi.org", got[1].accession)

    def test_preprint_accessions_survive(self):
        out = ("  1. A preprint\n     bio_2024.01.15.575613 · bioRxiv · 2024-01-15\n")
        self.assertEqual(_parse(out, "a", "q")[0].accession, "bio_2024.01.15.575613")

    def test_genuinely_empty_output_parses_to_nothing(self):
        self.assertEqual(_parse("No papers found.\nTry broader terms\n", "a", "q"), [])


class SupportTriageTests(unittest.TestCase):
    def test_language_suggests_a_support_type(self):
        self.assertEqual(Paper(title="Crystal structure of X").suggested_support,
                         "direct_experimental")
        self.assertEqual(Paper(title="CRISPR knockout of X").suggested_support,
                         "genetic_functional")
        self.assertEqual(Paper(title="AlphaFold model of X").suggested_support,
                         "computational_prediction")

    def test_unrecognised_language_is_unclassified_not_guessed(self):
        """Guessing a support type from a title is how a review article becomes
        'direct experimental evidence'. Unknown must stay unknown."""
        self.assertEqual(Paper(title="Some review about a protein").suggested_support,
                         "unclassified")


class NullVersusBrokenTests(unittest.TestCase):
    def test_a_missing_cli_is_an_error_not_an_empty_result(self):
        papers, err = search("anything", exe="/nonexistent/paperclip-binary")
        self.assertEqual(papers, [])
        self.assertTrue(err, "a failed search must report why")

    def test_an_axis_that_failed_is_marked_not_searched(self):
        """A failed axis must not be indistinguishable from an axis that ran and
        found nothing — one is a broken tool, the other is a finding."""
        ev, papers, errors = gather("GENE", "Context", "MED23",
                                    axes={"structure": "{gene} structure"},
                                    per_axis=1)
        # Without a working CLI on PATH this errors; with one it may return hits.
        axis = ev.axes["structure"]
        if errors:
            self.assertFalse(axis.searched)
            self.assertTrue(axis.note)
        else:
            self.assertTrue(axis.searched)

    def test_evidence_carries_its_caveat(self):
        ev = CandidateEvidence(gene="X", context="Y", partner="MED23")
        self.assertIn("Retrieved, not read", ev.caveat)
        self.assertIn("verified it at source", ev.caveat)


class AxisCoverageTests(unittest.TestCase):
    def test_every_axis_from_the_brief_is_present(self):
        """Andrey's stage-1 brief names these as separate questions; one generic
        search cannot answer them and must not pretend to."""
        for axis in ("dependency", "driver", "normal_tissue", "coactivator",
                     "activation_domain", "structure"):
            self.assertIn(axis, AXES)

    def test_queries_are_templated_per_candidate(self):
        for name, tpl in AXES.items():
            q = tpl.format(gene="IRF4", context="Lymphoid", partner="MED23")
            self.assertNotIn("{", q, f"{name} left an unfilled placeholder")
            self.assertIn("IRF4", q)

    def test_the_driver_axis_asks_the_overexpression_question(self):
        """The distinction the whole project turns on has to be in the query,
        not just in the write-up."""
        q = AXES["driver"].format(gene="X", context="Y", partner="Z")
        self.assertIn("overexpressed", q)

    def test_empty_and_populated_axes_are_both_reported(self):
        ev = CandidateEvidence(gene="X", context="Y", partner="MED23")
        from reagent_workflow.literature import AxisResult
        # "hit" means on-target, not merely returned: semantic search always
        # returns something, so n_papers alone is not evidence of coverage.
        ev.axes["a"] = AxisResult(axis="a", query="q", n_papers=3, n_on_target=2)
        ev.axes["b"] = AxisResult(axis="b", query="q", n_papers=3, n_on_target=0)
        self.assertEqual(ev.axes_with_hits, ["a"])
        self.assertEqual(ev.axes_empty, ["b"])



class OnTargetRelevanceTests(unittest.TestCase):
    """Paperclip's search is semantic: it returns the nearest papers, never
    nothing. Counting everything it returns as a hit means a query for a gene
    that does not exist reports full coverage across every axis — the exact
    false confidence this project exists to prevent."""

    def test_a_paper_that_never_names_the_gene_is_not_on_target(self):
        from reagent_workflow.literature import Paper, mentions
        p = Paper(title="Structural basis of Mediator recruitment",
                  abstract="We solve a structure of MED23 with Elk-1.")
        self.assertTrue(mentions(p, "MED23"))
        self.assertFalse(mentions(p, "GENEDOESNOTEXIST"))

    def test_matching_is_word_bounded_not_substring(self):
        """Substring matching would let TP53 match TP53BP1 and quietly credit a
        paper about a different protein to the candidate under review."""
        from reagent_workflow.literature import Paper, mentions
        p = Paper(title="TP53BP1 recruits repair factors", abstract="")
        self.assertFalse(mentions(p, "TP53"))
        self.assertTrue(mentions(Paper(title="TP53 mutation", abstract=""), "TP53"))

    def test_an_empty_or_one_character_gene_matches_nothing(self):
        from reagent_workflow.literature import Paper, mentions
        p = Paper(title="A paper about a protein", abstract="with an abstract")
        for g in ("", " ", "a"):
            self.assertFalse(mentions(p, g), f"{g!r} must not match")

    def test_an_axis_with_returns_but_no_on_target_paper_is_empty(self):
        from reagent_workflow.literature import AxisResult
        a = AxisResult(axis="x", query="q", n_papers=4, n_on_target=0)
        self.assertTrue(a.empty, "4 irrelevant papers is not a hit")
        self.assertFalse(AxisResult(axis="x", query="q", n_papers=4,
                                    n_on_target=1).empty)

if __name__ == "__main__":
    unittest.main()


class GeneSymbolsAsPapersWriteThem(unittest.TestCase):
    """Retrieval is worthless if the filter throws away the right paper.

    The cryo-EM structure that maps the ELK1 region on MED23 is titled with
    "Elk-1". Matching the HGNC symbol exactly discarded it, so the pipeline
    retrieved the one paper it most needed and then dropped it before reading.
    """

    @staticmethod
    def paper(title, abstract=""):
        return SimpleNamespace(title=title, abstract=abstract)

    def test_a_hyphenated_symbol_still_names_the_gene(self):
        self.assertTrue(mentions(self.paper(
            "Structural basis of human Mediator recruitment by the "
            "phosphorylated transcription factor Elk-1"), "ELK1"))

    def test_a_spaced_symbol_still_names_the_gene(self):
        self.assertTrue(mentions(self.paper("Elk 1 phosphorylation at Ser383"),
                                 "ELK1"))

    def test_a_longer_symbol_is_still_a_different_gene(self):
        # The reason the match was strict in the first place, kept intact.
        self.assertFalse(mentions(self.paper("ELK1L is a different protein"), "ELK1"))
        self.assertFalse(mentions(self.paper("TP53BP1 binds chromatin"), "TP53"))

    def test_a_paper_about_the_partner_alone_is_not_on_target(self):
        self.assertFalse(mentions(self.paper("Crystal structure of MED23"), "ELK1"))


class TheModelReadsTheAbstractNotTheGist(unittest.TestCase):
    """`paperclip search` summarises; the residues do not survive summarising."""

    def test_a_record_abstract_replaces_the_search_summary(self):
        from unittest import mock

        from reagent_workflow import literature as L
        real = ("Elk-1 binds to MED23 via a hydrophobic sequence PSIHFWSTLS "
                "containing one phosphorylated residue (S383).")
        with mock.patch.object(L, "search",
                               return_value=([Paper(title="Elk-1 and MED23",
                                                    accession="PMC1",
                                                    abstract="A structure reveals binding.")], "")), \
             mock.patch.object(L, "full_abstract", return_value=real):
            _, papers, _ = L.gather("ELK1", "", "MED23",
                                    axes={"structure": "{gene} {partner}"})
        self.assertEqual(papers[0].abstract, real)

    def test_an_unavailable_record_leaves_the_summary_alone(self):
        from unittest import mock

        from reagent_workflow import literature as L
        with mock.patch.object(L, "search",
                               return_value=([Paper(title="Elk-1 and MED23",
                                                    accession="PMC1",
                                                    abstract="A structure reveals binding.")], "")), \
             mock.patch.object(L, "full_abstract", return_value=""):
            _, papers, _ = L.gather("ELK1", "", "MED23",
                                    axes={"structure": "{gene} {partner}"})
        self.assertEqual(papers[0].abstract, "A structure reveals binding.")
