"""Spec §36 for the site and chemistry arms.

Where a check can be run against experimental coordinates it is, rather than
against a fixture that agrees with the implementation by construction. The box
test uses the deposited free MED23 structure (PDB 9F76) and the published pocket
residues; it skips if the gitignored file is absent (see team/TASKS.md).
"""
from __future__ import annotations

import unittest
from pathlib import Path

from reagent_workflow.chemistry import (Compound, LibraryArm, amphiphilicity_proxy,
                                        assemble_library, describe,
                                        estimate_screen_size, standardize)
from reagent_workflow.discovery_config import (ChemistryConfig, DiscoveryConfig,
                                               StructureConfig, StructureMode)
from reagent_workflow.interface import Atom, parse_mmcif
from reagent_workflow.site import build_search_site

ROOT = Path(__file__).resolve().parents[1]
FREE_MED23 = ROOT / "downloads" / "9F76.cif"
PUBLISHED_POCKET = [339, 343, 379, 382, 383, 533, 537]


def prov(name: str, smiles: str, **kw) -> Compound:
    return Compound(name=name, smiles=smiles, parent_smiles=smiles,
                    source="DrugCentral", source_id=f"DC{abs(hash(name)) % 99999}",
                    source_version="2025.1", retrieved="2026-08-15", **kw)


class SiteDefinitionTests(unittest.TestCase):
    @unittest.skipUnless(FREE_MED23.exists(), "downloads/9F76.cif absent")
    def test_search_box_contains_consensus_med23_residues(self):
        """A box that does not enclose the residues it was built from would send
        every compound to the wrong place while looking correct."""
        free = parse_mmcif(FREE_MED23)
        site = build_search_site(free, PUBLISHED_POCKET, receptor_path=str(FREE_MED23))
        self.assertTrue(site.defensible, site.blockers)
        self.assertEqual(site.residues, sorted(PUBLISHED_POCKET))
        wanted = set(PUBLISHED_POCKET)
        for a in (x for x in free if x.resi in wanted and x.element != "H"):
            self.assertTrue(site.contains(a.x, a.y, a.z),
                            f"residue {a.resi} atom {a.name} lies outside the box")

    @unittest.skipUnless(FREE_MED23.exists(), "downloads/9F76.cif absent")
    def test_box_is_a_groove_not_the_whole_protein(self):
        free = parse_mmcif(FREE_MED23)
        site = build_search_site(free, PUBLISHED_POCKET)
        self.assertLess(max(site.size), 30.0)
        xs = [a.x for a in free]
        self.assertLess(site.size[0], (max(xs) - min(xs)) / 2)

    @unittest.skipUnless(FREE_MED23.exists(), "downloads/9F76.cif absent")
    def test_no_localized_site_blocks_docking(self):
        """Residues scattered across the protein are a surface, not a pocket.
        The pipeline must abstain rather than box the whole thing."""
        free = parse_mmcif(FREE_MED23)
        spread = sorted({a.resi for a in free})[::120][:8]
        site = build_search_site(free, spread)
        self.assertFalse(site.defensible)
        self.assertTrue(any("not plausibly small-molecule addressable" in b
                            for b in site.blockers), site.blockers)

    def test_no_consensus_residues_refuses_rather_than_defaulting(self):
        site = build_search_site([Atom("A", 1, "CA", "C", 0, 0, 0)], [])
        self.assertFalse(site.defensible)
        self.assertEqual(site.size, (0.0, 0.0, 0.0))

    def test_residues_absent_from_the_free_receptor_are_refused(self):
        """Silently boxing whichever residues happen to be resolved would move
        the site without saying so."""
        atoms = [Atom("A", 10, "CA", "C", 0, 0, 0), Atom("A", 11, "CA", "C", 1, 1, 1)]
        self.assertFalse(build_search_site(atoms, [900, 901]).defensible)

    def test_box_padding_is_configuration(self):
        atoms = [Atom("A", r, "CA", "C", float(r), 0.0, 0.0) for r in (10, 11, 12)]
        tight = build_search_site(atoms, [10, 11, 12], StructureConfig(box_padding_angstrom=3))
        wide = build_search_site(atoms, [10, 11, 12], StructureConfig(box_padding_angstrom=8))
        self.assertLess(tight.size[0], wide.size[0])


class CompoundProvenanceTests(unittest.TestCase):
    def test_every_screened_compound_has_public_source(self):
        good = prov("aspirin", "CC(=O)Oc1ccccc1C(=O)O")
        good.amphiphilicity_proxy = 0.5
        orphan = Compound(name="mystery", smiles="CCO", parent_smiles="CCO",
                          source="", source_id="", source_version="", retrieved="")
        orphan.amphiphilicity_proxy = 0.9
        self.assertTrue(good.has_provenance)
        self.assertFalse(orphan.has_provenance)
        picked = assemble_library([good, orphan], n=2)
        self.assertNotIn("mystery", [c.name for c in picked])

    def test_salts_are_stripped_to_the_parent_and_stereochemistry_survives(self):
        parent, why = standardize(
            "CC(C)c1c(C(=O)Nc2ccccc2)c(-c2ccccc2)c(-c2ccc(F)cc2)n1CC[C@@H](O)C[C@@H](O)CC(=O)O")
        self.assertIsNone(why)
        self.assertIn("@", parent)

    def test_mixtures_metals_and_biologics_are_rejected_with_a_reason(self):
        for smiles, expect in [("N.N.Cl[Pt]Cl", "organic"),
                               ("[Na+].[Cl-]", "organic"),
                               ("CCC", "too small")]:
            parent, why = standardize(smiles)
            self.assertIsNone(parent, smiles)
            self.assertIn(expect, why)

    def test_descriptors_are_computed_not_asserted(self):
        d = describe("CC(=O)Oc1ccccc1C(=O)O")
        self.assertAlmostEqual(d["mw"], 180.16, places=1)
        self.assertEqual(d["heavy_atoms"], 13)


class AmphiphilicityTests(unittest.TestCase):
    def test_amphiphilicity_is_reproducible_with_fixed_seed(self):
        """A heuristic that changes between runs cannot be part of a
        reproducible pipeline, whatever its scientific merit."""
        s = "CC(=O)Oc1ccccc1C(=O)O"
        self.assertEqual(amphiphilicity_proxy(s), amphiphilicity_proxy(s))

    def test_a_purely_hydrophobic_molecule_does_not_score_as_amphiphilic(self):
        """The balance term exists so one distant oxygen cannot carry a
        near-entirely greasy molecule into the enriched arm."""
        alkane = amphiphilicity_proxy("CCCCCCCCCCCC")
        self.assertIsNotNone(alkane)
        self.assertLessEqual(alkane, 0.2)

    def test_the_metric_carries_its_caveat(self):
        self.assertIn("not an experimentally validated",
                      Compound(name="x", smiles="C", source="s", source_id="1",
                               source_version="v", retrieved="d").amphiphilicity_caveat)


class LibraryArmTests(unittest.TestCase):
    def _pool(self, n=60):
        base = ["CC(=O)Oc1ccccc1C(=O)O", "CCCCCCCCCCCC", "c1ccccc1O", "CCN(CC)CCOC(=O)c1ccccc1N",
                "OCC1OC(O)C(O)C(O)C1O", "Cc1ccccc1C(=O)NC", "CCCCc1ccc(O)cc1",
                "NCCc1ccc(O)c(O)c1", "COc1ccc2cc(ccc2c1)C(C)C(=O)O", "CC(C)Cc1ccc(cc1)C(C)C(=O)O"]
        out = []
        for i in range(n):
            c = prov(f"drug{i}", base[i % len(base)])
            c.amphiphilicity_proxy = round((i % 10) / 10, 3)
            out.append(c)
        return out

    def test_library_contains_enriched_diverse_and_counter_hypothesis_arms(self):
        """Taking only the top-scoring molecules would make any later 'amphiphilic
        compounds did well' result circular. The controls are what make the
        enrichment hypothesis refutable."""
        lib = assemble_library(self._pool(), n=50)
        arms = {a: sum(1 for c in lib if c.arm is a) for a in LibraryArm}
        for a in LibraryArm:
            self.assertGreater(arms[a], 0, f"{a} arm is empty: {arms}")
        self.assertGreater(arms[LibraryArm.ENRICHED], arms[LibraryArm.COUNTER_HYPOTHESIS])
        self.assertEqual(len({id(c) for c in lib}), len(lib))

    def test_arm_fractions_follow_configuration(self):
        cfg = ChemistryConfig()
        self.assertEqual(sum(cfg.library_arms(250).values()), 250)
        self.assertEqual(cfg.library_arms(250),
                         {"enriched": 175, "diverse_control": 50, "counter_hypothesis": 25})

    def test_counter_hypothesis_arm_is_actually_low_scoring(self):
        lib = assemble_library(self._pool(), n=50)
        enr = [c.amphiphilicity_proxy for c in lib if c.arm is LibraryArm.ENRICHED]
        cnt = [c.amphiphilicity_proxy for c in lib if c.arm is LibraryArm.COUNTER_HYPOTHESIS]
        self.assertLess(sum(cnt) / len(cnt), sum(enr) / len(enr))


class RuntimeBudgetTests(unittest.TestCase):
    def test_runtime_estimator_caps_screen_between_configured_min_and_max(self):
        cfg = ChemistryConfig()
        fast = estimate_screen_size([0.05] * 20, remaining_seconds=1500, workers=8, cfg=cfg)
        self.assertLessEqual(fast.estimated_n, cfg.fast_vina_max_n)
        slow = estimate_screen_size([600.0] * 20, remaining_seconds=60, workers=1, cfg=cfg)
        self.assertGreaterEqual(slow.estimated_n, cfg.fast_vina_min_n)
        self.assertTrue(slow.clamped)

    def test_estimate_applies_headroom_and_uses_measured_throughput(self):
        cfg = ChemistryConfig()
        e = estimate_screen_size([2.0] * 20, remaining_seconds=1000, workers=1, cfg=cfg)
        self.assertEqual(e.estimated_n, min(cfg.fast_vina_max_n, int(1000 / 2.0 * 0.70)))
        self.assertEqual(e.median_seconds_per_ligand, 2.0)

    def test_no_benchmark_falls_back_to_the_minimum_not_the_target(self):
        """Guessing a large screen with no measurement is how a demo ends up
        still running when it is meant to be finished."""
        e = estimate_screen_size([], remaining_seconds=1500, workers=8)
        self.assertEqual(e.estimated_n, ChemistryConfig().fast_vina_min_n)
        self.assertTrue(e.clamped)


class ConfigTests(unittest.TestCase):
    def test_hackathon_defaults_match_the_spec(self):
        c = DiscoveryConfig()
        self.assertEqual(c.structure.top_candidates, 3)
        self.assertEqual(c.structure.ad_window_length, 70)
        self.assertEqual(c.structure.discovery_diffusion_samples, 5)
        self.assertEqual(c.structure.contact_cutoff_angstrom, 4.5)
        self.assertEqual(c.chemistry.time_budget_minutes, 25)
        self.assertEqual(c.chemistry.redock_exhaustiveness, 32)
        self.assertTrue(c.fractions_sum_to_one)

    def test_top_candidates_cannot_exceed_the_hackathon_ceiling(self):
        with self.assertRaises(Exception):
            StructureConfig(top_candidates=9)

    def test_exploratory_mode_is_not_the_default(self):
        """Exploratory structural discovery has to be asked for explicitly."""
        self.assertIs(DiscoveryConfig().structure_mode, StructureMode.ESTABLISHED_INTERFACE)

    def test_config_is_frozen(self):
        with self.assertRaises(Exception):
            StructureConfig().top_candidates = 5


if __name__ == "__main__":
    unittest.main()
