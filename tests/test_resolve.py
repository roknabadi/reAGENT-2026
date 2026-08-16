"""Free text to a context, including the questions that must get no answer.

The resolver replaced eighteen hardcoded regexes that could only emit
lineages. The failure it exists to prevent is subtle: asking about small cell
lung cancer and being answered about Lung produces a complete, plausible,
wrong result, because pooling SCLC with every other lung tumour dilutes its
master regulators below threshold. So granularity is tested as carefully as
matching.

The other half is abstention. Semantic-ish matching always has a nearest
neighbour, and a question naming no disease must come back empty rather than
being quietly answered about whichever lineage scored highest.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from reagent_workflow.resolve import (ABBREVIATIONS, NOT_A_DISEASE, resolve,
                                      vocabulary)

MODEL = Path(__file__).resolve().parents[1] / "downloads" / "24Q4" / "Model.csv"
HAVE = MODEL.exists()


@unittest.skipUnless(HAVE, "DepMap Model.csv not present")
class VocabularyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = pd.read_csv(MODEL, usecols=["ModelID", "OncotreeLineage",
                                                "OncotreeSubtype"])
        cls.vocab = vocabulary(cls.model)
        cls.names = {c.context for c in cls.vocab}

    def test_every_abbreviation_expands_to_a_real_context(self):
        """The table's whole safety property. An expansion that names nothing
        in the data does not fail loudly — it partial-matches something else
        and answers confidently about the wrong disease. That is how 'crc'
        reached Rectal Adenocarcinoma (n=15) instead of Colon (n=74)."""
        missing = {k: v for k, v in ABBREVIATIONS.items() if v not in self.names}
        self.assertFalse(missing, f"expansions absent from the vocabulary: {missing}")

    def test_the_vocabulary_comes_from_the_file_not_a_list(self):
        both = {c.level for c in self.vocab}
        self.assertEqual(both, {"lineage", "subtype"},
                         "both granularities must be resolvable")
        self.assertGreater(len(self.vocab), 100,
                           "a hand-written list is the bug this replaced")

    def test_nothing_untestable_is_offered(self):
        for c in self.vocab:
            self.assertGreaterEqual(c.n_models, 5, f"{c.context} cannot be tested")
            self.assertNotIn(c.context, NOT_A_DISEASE)

    def test_subtypes_carry_their_parent_lineage(self):
        subs = [c for c in self.vocab if c.level == "subtype"]
        self.assertTrue(all(c.parent_lineage for c in subs))


@unittest.skipUnless(HAVE, "DepMap Model.csv not present")
class MatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = pd.read_csv(MODEL, usecols=["ModelID", "OncotreeLineage",
                                                "OncotreeSubtype"])

    def r(self, q):
        return resolve(q, self.model)

    def test_an_initialism_resolves_to_its_subtype(self):
        for q, want in (("SCLC", "Small Cell Lung Cancer"),
                        ("AML", "Acute Myeloid Leukemia"),
                        ("GBM", "Glioblastoma"),
                        ("PDAC", "Pancreatic Adenocarcinoma")):
            with self.subTest(q=q):
                self.assertEqual(self.r(q).match.context, want)

    def test_a_long_question_resolves_the_same_as_the_bare_term(self):
        """Scoring is recall over the context name, not over the question, so
        detail must not dilute the match."""
        long = ("I am a computational biologist looking at small cell lung cancer "
                "and I would like three druggable transcription factor "
                "dependencies with some structural evidence behind them")
        self.assertEqual(self.r(long).match.context, self.r("SCLC").match.context)

    def test_subtype_beats_lineage_when_the_question_is_specific(self):
        """The dilution failure. 'Small cell lung cancer' must not answer 'Lung'."""
        m = self.r("small cell lung cancer").match
        self.assertEqual(m.context, "Small Cell Lung Cancer")
        self.assertEqual(m.level, "subtype")

    def test_lineage_wins_when_the_question_is_broad(self):
        """And the converse: a bare tissue must not be narrowed to a subtype
        the user never asked for."""
        m = self.r("lung").match
        self.assertEqual((m.context, m.level), ("Lung", "lineage"))

    def test_a_qualifier_the_user_typed_is_not_discarded(self):
        self.assertEqual(self.r("uveal melanoma").match.context, "Uveal Melanoma")
        self.assertEqual(self.r("melanoma").match.context, "Melanoma")

    def test_adjectival_forms_reach_the_noun_oncotree_uses(self):
        for q in ("pancreatic cancer drug targets please", "ovarian cancer",
                  "renal cell", "gastric tumour"):
            with self.subTest(q=q):
                self.assertTrue(self.r(q).ok, f"{q!r} should resolve")

    def test_punctuated_names_still_earn_the_phrase_bonus(self):
        """'Chronic Myeloid Leukemia, BCR-ABL1+' lost its bonus to a comma and
        CML resolved to the entire Myeloid lineage."""
        self.assertEqual(self.r("CML").match.context,
                         "Chronic Myeloid Leukemia, BCR-ABL1+")

    def test_case_and_spacing_do_not_matter(self):
        for q in ("ewing sarcoma", "EWING SARCOMA", "  Ewing   Sarcoma  "):
            with self.subTest(q=q):
                self.assertEqual(self.r(q).match.context, "Ewing Sarcoma")


@unittest.skipUnless(HAVE, "DepMap Model.csv not present")
class AbstentionTests(unittest.TestCase):
    """Questions that must NOT get an answer."""

    @classmethod
    def setUpClass(cls):
        cls.model = pd.read_csv(MODEL, usecols=["ModelID", "OncotreeLineage",
                                                "OncotreeSubtype"])

    def test_a_question_naming_no_disease_abstains(self):
        for q in ("what is the weather today", "find me a target",
                  "hello", "", "   ", "tell me about transcription factors"):
            with self.subTest(q=q):
                r = resolve(q, self.model)
                self.assertFalse(r.ok, f"{q!r} resolved to {r.match}")
                self.assertTrue(r.note, "an abstention must say why")

    def test_an_abstention_still_reports_what_it_considered(self):
        """So a near miss is debuggable rather than a dead end."""
        r = resolve("some kind of carcinoma maybe", self.model)
        if not r.ok:
            self.assertIsInstance(r.alternatives, list)

    def test_a_resolution_records_the_alternatives_it_beat(self):
        r = resolve("SCLC", self.model)
        self.assertTrue(r.ok)
        self.assertTrue(r.note, "a match must say why it matched")
        self.assertTrue(any(a.context == "Lung" for a in r.alternatives),
                        "the lineage it outranked should be visible")


if __name__ == "__main__":
    unittest.main()
