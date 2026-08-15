"""Determinism, pinning, and the parser that silently reported a null result.

These run without the 400 MB DepMap download: the heavy checks live in
scripts/verify_pipeline.py, which needs the real files. What is here is what can
be asserted from fixtures and pure functions on any machine.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from dependency_scout.depmap import analyze_gene_effects
from dependency_scout.provenance import (InputFile, Reproducibility, RunProvenance,
                                         digest_payload)
from dependency_scout.ranking import rank_all

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui"))


def rows(cands):
    return [{"gene": c.dependency.gene, "n": c.dependency.n_target_models,
             "sel": round(c.dependency.selectivity_delta, 9),
             "median": round(c.dependency.median_target_effect, 9),
             "pass": c.gate.eligible, "why": c.gate.failures} for c in cands]


class DeterminismTests(unittest.TestCase):
    def _run(self):
        return rows(rank_all(analyze_gene_effects(
            FIXTURES / "gene_effect.csv", FIXTURES / "models.csv",
            context="Lung", synthetic=True)))

    def test_the_same_inputs_give_a_byte_identical_result(self):
        """A pipeline that cannot repeat itself cannot be checked by anyone."""
        a, b = self._run(), self._run()
        self.assertEqual(digest_payload(a), digest_payload(b))
        self.assertEqual(a, b)

    def test_gate_verdicts_do_not_depend_on_row_order(self):
        """Ranking must be a function of the numbers, not of how they arrived."""
        a = {r["gene"]: (r["pass"], tuple(r["why"])) for r in self._run()}
        b = {r["gene"]: (r["pass"], tuple(r["why"])) for r in reversed(self._run())}
        self.assertEqual(a, b)


class ProvenanceTests(unittest.TestCase):
    def test_fingerprint_follows_the_inputs_not_their_order(self):
        f1 = InputFile.pin("models", FIXTURES / "models.csv")
        f2 = InputFile.pin("gene_effect", FIXTURES / "gene_effect.csv")
        self.assertEqual(RunProvenance(inputs=[f1, f2]).fingerprint,
                         RunProvenance(inputs=[f2, f1]).fingerprint)

    def test_a_changed_input_changes_the_fingerprint(self):
        """The silent failure this exists to catch: a file reissued under the
        same name, so every downstream number quietly describes other data."""
        real = InputFile.pin("gene_effect", FIXTURES / "gene_effect.csv")
        swapped = real.model_copy(update={"sha256": "0" * 64})
        self.assertFalse(RunProvenance(inputs=[real])
                         .matches(RunProvenance(inputs=[swapped])))

    def test_retrieved_results_are_not_labelled_reproducible(self):
        p = RunProvenance(inputs=[], reproducibility=Reproducibility.RETRIEVED)
        self.assertIsNot(p.reproducibility, Reproducibility.COMPUTED)


class PaperclipParserTests(unittest.TestCase):
    """The regression that shipped: Paperclip changed its metadata line and the
    parser dropped every paper, so a live search reported 'nothing found'. On
    screen that is indistinguishable from a real null result."""

    def setUp(self):
        try:
            import serve  # ui/serve.py
        except Exception as e:                       # pragma: no cover
            self.skipTest(f"ui/serve.py not importable: {e}")
        self.parse = serve._parse_search

    def test_current_format_with_the_journal_in_the_metadata_line(self):
        out = (
            "Found 2 papers  [s_d313fffe]\n\n"
            "  1. The Potential of Single-Transcription Factor Gene Expression\n"
            "     Albert Inanez, Raul del Rey-Vergara, Pedro Rocha, ...\n"
            "     PMC11818609 · International Journal of Molecular Sciences · 2025-02-03\n"
            "     https://doi.org/10.3390/ijms26031293\n"
            '     "This study evaluated using RT-qPCR to classify SCLC subtypes."\n\n'
            "  2. Tumor Heterogeneity and Plasticity in Small Cell Lung Cancer\n"
            "     PMC12519990 · Cancers · 2025-10-01\n"
            "     https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12519990/\n"
        )
        got = self.parse(out)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0]["id"], "PMC11818609")
        self.assertEqual(got[1]["id"], "PMC12519990")
        self.assertIn("RT-qPCR", got[0]["abstract"])
        self.assertTrue(got[0]["url"].startswith("http"))

    def test_the_older_format_still_parses(self):
        """Both shapes must work, or upgrading the CLI silently empties the rail."""
        out = ("  1. Structural basis of coactivator recruitment\n"
               "     PMC12755459 · PMC · 2025-12-16\n"
               "     https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12755459/\n"
               '     "A tripartite interface."\n')
        got = self.parse(out)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["id"], "PMC12755459")

    def test_preprint_accessions_are_recognised(self):
        out = ("  1. A preprint about something\n"
               "     bio_2024.01.15.575613 · bioRxiv · 2024-01-15\n")
        self.assertEqual(self.parse(out)[0]["id"], "bio_2024.01.15.575613")

    def test_genuinely_empty_output_returns_nothing(self):
        """The one case that must still report zero, so a real null result and a
        parser bug can never look the same again."""
        self.assertEqual(self.parse("No papers found.\nTry broader terms\n"), [])
        self.assertEqual(self.parse(""), [])


if __name__ == "__main__":
    unittest.main()
