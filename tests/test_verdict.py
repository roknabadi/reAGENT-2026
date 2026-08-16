"""The single-context scan must return the canonical batch scan's verdict.

This file exists because the project shipped two different dependency gates.
The interface applied a four-way AND; `stage1_depmap.py` passes on either a
median path or a specificity-first path. Both were defensible in isolation and
they disagreed, so a candidate's status depended on which code you happened to
run — the failure mode where a pipeline looks consistent because nobody ran
both halves on the same input.

The test is a differential one: run `verdict.scan_context` and Kevin's
`pass1_lineage_scan` / `pass2_subtype_scan` over the same matrix and require
the gate-relevant fields to be identical. Anything less — asserting that the
scan "looks reasonable", or that known genes appear — would pass while the two
gates diverged, which is exactly what happened before.

Needs the real DepMap files; skips without them rather than inventing a
matrix, since a synthetic matrix would test agreement between two functions on
data neither will ever see.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "re-agent_discovery" / "src"))

from reagent_workflow import verdict as V  # noqa: E402

DATA = ROOT / "downloads" / "24Q4"
GE, MODEL = DATA / "CRISPRGeneEffect.csv", DATA / "Model.csv"
TFS = ROOT / "downloads" / "lambert_tfs.csv"
HAVE = GE.exists() and MODEL.exists() and TFS.exists()

# A small TF panel: the differential test needs the two implementations to see
# the same columns, and loading all 1,600 through the batch path for every
# assertion is minutes of CPU for no extra coverage.
PANEL = ["ASCL1", "POU2F3", "NEUROD1", "INSM1", "SOX10", "MYB", "IRF4", "FLI1",
         "ELK1", "TP53", "MYC", "CTCF", "POLR2A", "RPL13A", "SOX2", "PAX8"]


@unittest.skipUnless(HAVE, f"DepMap {V.DEPMAP_RELEASE} files not present")
class CanonicalAgreementTests(unittest.TestCase):
    """The gate-relevant fields must match the batch scan exactly."""

    @classmethod
    def setUpClass(cls):
        import pandas as pd
        from stage1_depmap import pass1_lineage_scan, pass2_subtype_scan

        ge, model = V.load_matrix(str(GE), str(MODEL), str(TFS))
        cls.ge = ge[[c for c in PANEL if c in ge.columns]]
        cls.model = model
        cls.pass1 = pass1_lineage_scan(cls.ge, model, min_n=5)
        cls.pass2 = pass2_subtype_scan(cls.ge, model)
        cls.pd = pd

    def _compare(self, batch, context, level):
        mine = {v.gene: v for v in
                V.scan_context(self.ge, self.model, context, level=level)}
        theirs = batch[batch["context"] == context]
        self.assertTrue(len(theirs), f"batch scan produced no rows for {context}")
        self.assertEqual(set(mine), set(theirs["tf"]),
                         f"{context}: different TFs tested")
        for _, row in theirs.iterrows():
            v = mine[row["tf"]]
            where = f"{row['tf']} in {context}"
            self.assertEqual(v.dependency_flag, bool(row["dependency_flag"]),
                             f"{where}: gate verdict differs")
            self.assertEqual(v.n_target, row["n_in"], f"{where}: n differs")
            self.assertEqual(v.n_other, row["n_out"], f"{where}: n_out differs")
            for field, col in (("median_target", "in_median"),
                               ("median_other", "out_median"),
                               ("target_dependent_fraction", "target_dependent_fraction"),
                               ("other_dependent_fraction", "other_dependent_fraction"),
                               ("selectivity_delta", "selectivity_delta"),
                               ("pvalue", "pvalue")):
                self.assertAlmostEqual(getattr(v, field), row[col], places=9,
                                       msg=f"{where}: {field} differs")

    def test_lineage_scan_matches_pass1(self):
        for context in ("Lung", "Skin", "Bone", "Lymphoid"):
            with self.subTest(context=context):
                self._compare(self.pass1, context, "lineage")

    def test_subtype_scan_matches_pass2(self):
        for context in ("Small Cell Lung Cancer", "Ewing Sarcoma"):
            if context in set(self.pass2["context"]):
                with self.subTest(context=context):
                    self._compare(self.pass2, context, "subtype")

    def test_route_is_consistent_with_the_flag(self):
        """`route` is a label on the gate's decision, never a second opinion:
        if it says no path passed, the flag must be false, and vice versa."""
        for v in V.scan_context(self.ge, self.model, "Lung", level="lineage"):
            self.assertEqual(v.dependency_flag, v.route != "none",
                             f"{v.gene}: route {v.route!r} contradicts flag")


@unittest.skipUnless(HAVE, f"DepMap {V.DEPMAP_RELEASE} files not present")
class GateBehaviourTests(unittest.TestCase):
    """What the canonical gate is supposed to do, on real data."""

    @classmethod
    def setUpClass(cls):
        ge, model = V.load_matrix(str(GE), str(MODEL), str(TFS))
        cls.ge, cls.model = ge, model

    def test_sclc_surfaces_the_lineage_scan_dilutes_away(self):
        """The reason the subtype pass exists. ASCL1 and POU2F3 are dependencies
        of distinct SCLC subsets; pooled into Lung they are invisible, and the
        interface previously only ever asked at lineage level."""
        sclc = {v.gene: v for v in
                V.scan_context(self.ge, self.model, "Small Cell Lung Cancer")}
        found = [g for g in ("ASCL1", "POU2F3", "NEUROD1", "INSM1")
                 if g in sclc and sclc[g].dependency_flag]
        self.assertTrue(found, "no SCLC master regulator cleared the gate; the "
                               "subtype pass is not reaching the data")

    def test_a_pan_essential_gene_is_not_a_selective_dependency(self):
        """POLR2A kills every cell line. A gate that admits it is measuring
        essentiality, not selectivity, and would put a lethal target at the top
        of every context."""
        for context in ("Lung", "Skin"):
            hits = {v.gene: v for v in
                    V.scan_context(self.ge, self.model, context, level="lineage")}
            if "POLR2A" in hits:
                self.assertFalse(hits["POLR2A"].dependency_flag,
                                 f"POLR2A passed in {context}")

    def test_shortlist_returns_at_most_three_and_only_passing(self):
        vs = V.scan_context(self.ge, self.model, "Skin", level="lineage")
        top = V.shortlist(vs, n=3)
        self.assertLessEqual(len(top), 3)
        for v in top:
            self.assertTrue(v.significant, f"{v.gene} shortlisted without FDR")

    def test_an_unknown_context_returns_nothing_rather_than_guessing(self):
        self.assertEqual(V.scan_context(self.ge, self.model, "Atlantis"), [])


class ProvenanceTests(unittest.TestCase):
    """No network, no data: the release label must come from the canonical
    config, so it cannot say 24Q4 while the code reads something else."""

    def test_release_label_is_read_not_written(self):
        import config as stage1_config
        self.assertEqual(V.DEPMAP_RELEASE, stage1_config.DEPMAP_RELEASE)

    def test_the_gate_is_the_canonical_function_object(self):
        """Not a copy that happens to agree today."""
        import stage1_depmap
        from reagent_workflow.verdict import _dependency_flag
        self.assertIs(_dependency_flag, stage1_depmap._dependency_flag)


if __name__ == "__main__":
    unittest.main()
