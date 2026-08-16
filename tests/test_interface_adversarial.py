"""Adversarial probes against the interface consensus module.

Written by a second pair of eyes, against `src/reagent_workflow/interface.py`
and without touching it. The module gates a docking run: if it calls a spurious
interface "converged", compounds get screened against a site that does not
exist and the result is presented as a hypothesis worth an experiment. A
refusal is cheap; a false convergence is not. Every test below asks the same
question in a different way — *can this be made to say yes when the right
answer is no?*

Tests marked `# WAS A DEFECT, NOW FIXED:` + `@unittest.expectedFailure` assert the behaviour the
module SHOULD have. They fail today. When the author fixes the defect they turn
into unexpected successes and the suite goes red until the decorator is
removed — which is the point.

Unmarked tests pass today and are regression guards on behaviour that survived
the attack.
"""
from __future__ import annotations

import time
import unittest
from itertools import permutations
from pathlib import Path

from reagent_workflow.interface import (Atom, ConsensusConfig, Contact,
                                        SampleInterface, build_consensus,
                                        cluster_interfaces, contact_occupancy,
                                        contacts_between, parse_mmcif,
                                        smallest_confident_segment)

ROOT = Path(__file__).resolve().parents[1]
CIF = ROOT / "downloads" / "9F6Y.cif"

# Monté et al. 2025 (doi:10.1038/s41467-025-59014-8). ELK1 S383 is the ERK site;
# the MED23 interaction is phosphorylation-dependent, so pS383 is not an
# incidental residue, it is the reason the complex exists.
ELK1_PHOSPHOSERINE = 383


def sample(name: str, pairs: list[tuple[int, int]]) -> SampleInterface:
    return SampleInterface(sample=name, contacts=[
        Contact(target_resi=t, partner_resi=p, min_distance=3.8, sample=name)
        for t, p in pairs])


def span(name: str, tgt, par) -> SampleInterface:
    return sample(name, [(t, p) for t in tgt for p in par])


def jaccard(a: set[int], b: set[int]) -> float:
    return len(a & b) / len(a | b) if (a | b) else 1.0


def chain_of_atoms(chain: str, resi_range, x0: float = 0.0) -> list[Atom]:
    """A straight run of CA atoms, 3.8 A apart — a crude but valid backbone."""
    return [Atom(chain=chain, resi=r, name="CA", element="C",
                 x=x0 + 3.8 * i, y=0.0, z=0.0)
            for i, r in enumerate(resi_range)]


# ── 1. how many independent observations does "convergence" require? ────────

class AdversarialEnsembleSizeTests(unittest.TestCase):
    """Convergence is a statement about *independent* samples agreeing. The
    module never checks how many independent samples it actually has."""

    # WAS A DEFECT, NOW FIXED: N=1 reports 100% support and no blockers. The author's own test
    # `test_a_single_experimental_structure_is_not_treated_as_an_ensemble`
    # carries the docstring "One structure cannot demonstrate convergence,
    # whatever its quality" but only asserts total_samples == 1 and the
    # interpretation string — it never asserts the claim in its own name.
    def test_a_single_sample_cannot_demonstrate_convergence(self):
        """Prevents: one model out of a failed ensemble being screened as a
        converged hypothesis. With N=1 the dominant cluster is trivially 1/1,
        every residue has occupancy 1.0, and the module reports 100% support
        with an empty blocker list — a number that means nothing."""
        c = build_consensus([span("only_model", range(374, 385), range(339, 346))])
        self.assertTrue(c.blockers, "N=1 must be refused, not scored")
        self.assertFalse(c.converged)

    # WAS A DEFECT, NOW FIXED: no de-duplication of samples. The same prediction repeated is
    # indistinguishable from independent replicates.
    def test_one_prediction_cloned_is_not_an_ensemble(self):
        """Prevents: a retry loop, a re-read artifact, or a fan-out that lost
        its seed silently turning one model into 'converged in 8/8 samples'.
        Sample names are already carried on every Contact; duplicates are a
        one-line check and the difference between evidence and an echo."""
        one = span("only_model", range(374, 385), range(339, 346))
        c = build_consensus([one] * 8)
        self.assertTrue(c.blockers,
                        f"8 copies of one model reported support "
                        f"{c.ensemble_support} with blockers {c.blockers}")

    def test_duplicate_sample_names_survive_into_the_provenance_fields(self):
        """Regression guard on a traceability hole rather than a safety one:
        `alternative_clusters` is a list of sample names, so colliding names
        make the reported clusters impossible to trace back to models. This
        passes today and documents the current, lossy behaviour."""
        s = [span("model", range(10, 21), range(50, 57)) for _ in range(3)]
        s += [span("model", range(800, 811), range(900, 907)) for _ in range(2)]
        c = build_consensus(s)
        self.assertEqual(c.alternative_clusters, [["model", "model"]])


# ── 2. order dependence ─────────────────────────────────────────────────────

def _bridged_ensemble() -> list[SampleInterface]:
    """Two real poses plus one intermediate that half-overlaps both.

    a1/a2 sit on one surface, b1 on another, m straddles them at Jaccard 0.385
    to each — just over the 0.35 merge threshold. Which cluster m lands in is
    decided by nothing but list order, and m is the swing vote.
    """
    a = lambda n: span(n, range(1, 9), range(201, 209))
    b = lambda n: span(n, range(101, 109), range(301, 309))
    m = sample("m", [(t, p) for t in list(range(1, 6)) + list(range(101, 106))
                     for p in list(range(201, 206)) + list(range(301, 306))])
    return [a("a1"), a("a2"), b("b1"), m]


class AdversarialOrderDependenceTests(unittest.TestCase):
    """`cluster_interfaces` is a single greedy pass: each sample joins the first
    existing cluster whose *running mean* similarity clears the threshold. The
    running mean depends on which samples arrived first."""

    # WAS A DEFECT, NOW FIXED: the same four samples converge or refuse depending on list order.
    def test_shuffling_the_ensemble_must_not_flip_refusal_into_convergence(self):
        """Prevents: a screen being authorised because an upstream fan-out
        happened to return its samples in a different order this run. Sample
        order is an artifact of scheduling — it carries no structural
        information and must not appear in the verdict."""
        s = _bridged_ensemble()
        a1, a2, b1, m = s
        first = build_consensus([a1, a2, b1, m])
        second = build_consensus([b1, a1, a2, m])
        self.assertEqual(bool(first.blockers), bool(second.blockers),
                         f"order [a1,a2,b1,m] -> support {first.ensemble_support}, "
                         f"blockers {first.blockers}; "
                         f"order [b1,a1,a2,m] -> support {second.ensemble_support}, "
                         f"blockers {second.blockers}")
        self.assertAlmostEqual(first.ensemble_support, second.ensemble_support)

    # WAS A DEFECT, NOW FIXED: the reported site itself moves to a different surface of the
    # target depending on input order.
    def test_the_reported_site_must_be_invariant_under_permutation(self):
        """Prevents: docking into target residues 1-6 or 101-104 depending on
        the order a list happened to be built in. These are different surfaces
        of the protein; a consumer reading `target_segment` has no way to know
        the answer was a coin flip."""
        outcomes = set()
        for perm in permutations(_bridged_ensemble()):
            c = build_consensus(list(perm))
            seg = c.target_segment
            outcomes.add((bool(c.blockers), c.ensemble_support,
                          (seg.start, seg.end) if seg else None))
        self.assertEqual(len(outcomes), 1,
                         f"permuting one ensemble produced {len(outcomes)} "
                         f"distinct verdicts: {sorted(outcomes)}")


# ── 3. cluster coherence ────────────────────────────────────────────────────

class AdversarialClusterCoherenceTests(unittest.TestCase):
    """A cluster is only meaningful if its members actually agree with each
    other. The greedy pass compares a new sample to the *mean* over an existing
    cluster, which lets a cluster drift arbitrarily far from where it started.
    """

    # WAS A DEFECT, NOW FIXED: transitive chaining. Membership is never re-checked pairwise.
    def test_every_pair_inside_a_cluster_should_meet_the_similarity_threshold(self):
        """Prevents: 'the dominant interface' naming a set of models that do
        not agree with one another. The module's own contract is a 0.35 Jaccard
        floor; chaining admits pairs an order of magnitude below it."""
        cfg = ConsensusConfig()
        ladder = [span(f"s{i}", range(100 + i, 112 + i), range(500 + i, 512 + i))
                  for i in range(12)]
        dominant = cluster_interfaces(ladder, cfg)[0]
        worst = min(min(jaccard(x.target_residues, y.target_residues),
                        jaccard(x.partner_residues, y.partner_residues))
                    for x in dominant for y in dominant)
        self.assertGreaterEqual(
            worst, cfg.cluster_jaccard_threshold,
            f"cluster of {len(dominant)} contains a pair at Jaccard {worst:.3f}, "
            f"below the {cfg.cluster_jaccard_threshold} floor it claims to enforce")

    # WAS A DEFECT, NOW FIXED: the headline. Plausible-looking garbage gets a clean bill.
    def test_a_register_shifted_ladder_must_not_be_called_a_converged_interface(self):
        """Prevents the expensive failure this module exists to prevent.

        A peptide sliding one residue at a time along a groove is the classic
        signature of a docking run that has found *nothing* — the sampler has
        no preferred register, so it returns every register. Twelve such
        samples chain into a single cluster and the module reports 12/12
        samples, 100% ensemble support, no blockers, and a tight 7-residue
        segment (107-113). That is the most confident-looking output the module
        can produce, and the first and last models of the 'converged' ensemble
        share one residue out of twenty-three.
        """
        ladder = [span(f"s{i}", range(100 + i, 112 + i), range(500 + i, 512 + i))
                  for i in range(12)]
        c = build_consensus(ladder)
        self.assertTrue(
            c.blockers,
            f"12 register-shifted poses reported support {c.ensemble_support} "
            f"and segment "
            f"{(c.target_segment.start, c.target_segment.end) if c.target_segment else None} "
            f"with no blockers")


# ── 4. is the returned segment a screenable site? ───────────────────────────

class AdversarialSegmentTests(unittest.TestCase):
    """`smallest_confident_segment` is only asked to be the *smallest* span
    covering 80% of reproducible contacts. Nothing asks whether that span is a
    binding site."""

    # WAS A DEFECT, NOW FIXED: no upper bound on segment length.
    def test_a_span_covering_most_of_the_protein_is_not_a_site(self):
        """Prevents: handing a docking run a 394-residue 'binding segment'.
        Five samples that all touch two patches 390 residues apart produce
        start=10, end=403, contact_coverage=0.83 and no blockers. A bimodal
        interface should be refused as unresolved, not averaged into a span
        that contains the entire protein between the two patches."""
        two_patch = [span(f"s{i}", list(range(10, 16)) + list(range(400, 406)),
                          range(50, 57)) for i in range(5)]
        c = build_consensus(two_patch)
        seg = c.target_segment
        self.assertTrue(
            c.blockers or (seg is not None and seg.length <= 40),
            f"returned a {seg.length if seg else 0}-residue segment with "
            f"blockers {c.blockers}")

    # WAS A DEFECT, NOW FIXED: a flat, whole-surface interface passes every check.
    def test_a_whole_protein_flat_interface_must_be_refused(self):
        """Prevents: a badly-scaled or collapsed set of models — every residue
        of the target touching every residue of the partner — being reported as
        perfect convergence. Five identical 400x300 contact maps give support
        1.0, a 320-residue segment, coverage 0.80 and zero blockers. Perfect
        agreement on a meaningless answer is still meaningless."""
        flat = [span(f"s{i}", range(1, 401), range(1, 301)) for i in range(5)]
        c = build_consensus(flat)
        self.assertTrue(c.blockers,
                        f"support {c.ensemble_support}, segment "
                        f"{c.target_segment}, blockers {c.blockers}")

    # WAS A DEFECT, NOW FIXED: no minimum evidence floor.
    def test_a_single_residue_pair_is_not_an_interface(self):
        """Prevents: an ensemble whose models each brush the partner at one
        atom being reported as a converged one-residue site with coverage 1.0.
        Five samples with one contact each return segment 42-42, length 1, no
        blockers — the strongest-looking output in the module, from six
        contacts total across the whole ensemble."""
        thin = [sample(f"s{i}", [(42, 99)]) for i in range(5)]
        c = build_consensus(thin)
        self.assertTrue(c.blockers,
                        f"one contact per sample yielded segment "
                        f"{c.target_segment} with blockers {c.blockers}")

    # OPEN (performance, not correctness): the span search is quadratic in the
    # residue *numbering* range,
    # not in the amount of data.
    @unittest.expectedFailure
    def test_segment_search_must_not_scale_with_the_residue_numbering_range(self):
        """Prevents: a hang on a construct with an offset or a two-domain
        target. The search scans every (i, j) pair between the lowest and
        highest kept residue number, so twelve contacts spread over a 4000-
        residue numbering range costs ~16M inner iterations. Doubling the gap
        with identical data quadruples the runtime."""
        cfg = ConsensusConfig()

        def timed(gap: int) -> float:
            cluster = [span(f"s{i}", list(range(10, 16)) + list(range(gap, gap + 6)),
                            range(50, 56)) for i in range(3)]
            t0 = time.perf_counter()
            smallest_confident_segment(cluster, cfg)
            return time.perf_counter() - t0

        small = timed(1000)
        large = timed(2000)
        self.assertLess(large, small * 2.5,
                        f"doubling the numbering gap took {large / max(small, 1e-9):.1f}x "
                        f"longer ({small:.3f}s -> {large:.3f}s) on identical data")

    def test_a_blocked_consensus_still_publishes_a_target_segment(self):
        """Regression guard on a footgun. `build_consensus` contains

            target_segment=segment if not blockers else segment

        which is a no-op — the two branches are identical, so a refused
        consensus still carries a fully-populated segment into the JSON
        artifact. `converged` is correct, but any consumer that reads
        `target_segment` without also reading `blockers` gets a refusal that
        looks exactly like an answer. This passes today; it exists so the
        FIXED: `target_segment` is now None whenever `blockers` is non-empty,
        and the diagnostic value moved to `rejected_segment`."""
        scattered = [span(f"s{i}", range(100 + i * 90, 110 + i * 90),
                          range(200 + i * 90, 208 + i * 90)) for i in range(5)]
        c = build_consensus(scattered)
        self.assertTrue(c.blockers)
        self.assertFalse(c.converged)
        self.assertIsNone(c.target_segment, "a refusal must not ship a segment")
        self.assertIsNotNone(c.rejected_segment, "diagnosis is kept, just not as a result")


# ── 5. the partner side of the site ─────────────────────────────────────────

class AdversarialPartnerSiteTests(unittest.TestCase):
    """The target segment is occupancy-filtered. The partner residue list —
    which is what actually defines the pocket a compound is docked into — is a
    raw union over the dominant cluster."""

    # WAS A DEFECT, NOW FIXED: partner_contact_residues has no occupancy filter.
    def test_partner_pocket_residues_must_be_reproducible_too(self):
        """Prevents: a docking box built around a pocket that appeared in one
        model out of five. Four samples agree on partner 50-56; a fifth adds
        900-910 and still joins the dominant cluster, so the published pocket
        silently becomes 18 residues, 11 of them at 20% occupancy, with no
        blocker and no per-residue support reported."""
        cluster = [span(f"s{i}", range(10, 21), range(50, 57)) for i in range(4)]
        cluster.append(sample("s4", [(t, p) for t in range(10, 21)
                                     for p in list(range(50, 57)) + list(range(900, 911))]))
        c = build_consensus(cluster)
        self.assertEqual(c.dominant_cluster_samples, 5)
        self.assertEqual(
            [r for r in c.partner_contact_residues if r >= 900], [],
            f"published pocket {c.partner_contact_residues} includes residues "
            f"seen in 1/5 samples")


# ── 6. input validation at the geometry boundary ────────────────────────────

class AdversarialGeometryInputTests(unittest.TestCase):

    # OPEN (low severity): contacts_between now refuses identical chains, but a
    # SampleInterface built by hand from self-contacts cannot be detected here.
    @unittest.expectedFailure
    def test_a_chain_docked_against_itself_must_not_produce_an_interface(self):
        """Prevents: a config typo (target_chain == partner_chain) producing a
        beautiful converged 'interface' of a chain with itself. Every residue
        contacts itself at 0.0 A, so occupancy is 1.0 everywhere, the cluster
        is unanimous and blockers is empty. On the real 9F6Y chain B this
        yields a converged segment at 374-381 — the published motif — from
        pure self-contact."""
        atoms = chain_of_atoms("A", range(1, 11))
        iface = contacts_between(atoms, "A", "A", 4.5, sample="self")
        self.assertTrue(iface.is_empty,
                        f"self-chain produced {len(iface.contacts)} contacts, "
                        f"{sum(1 for c in iface.contacts if c.target_resi == c.partner_resi)}"
                        f" of them residue-with-itself at 0.0 A")

    def test_nan_coordinates_raise_rather_than_becoming_contacts(self):
        """Regression guard on a live hazard. A failed prediction can emit NaN
        coordinates. `d > cutoff` is False for NaN, so any NaN distance that
        reaches the comparison is silently *accepted* as a contact. Today the
        grid bucketing raises first, which is loud and safe — this test pins
        that ordering so a future refactor of the spatial index cannot quietly
        turn NaNs into a converged interface. The raise is still an unguarded
        ValueError, not a validated refusal."""
        for bad in (float("nan"), float("inf")):
            with self.subTest(coord=bad):
                atoms = [Atom("A", 1, "CA", "C", bad, 0.0, 0.0),
                         Atom("B", 2, "CA", "C", 0.0, 0.0, 0.0)]
                with self.assertRaises(ValueError):
                    contacts_between(atoms, "A", "B", 4.5)

    def test_a_typoed_chain_id_refuses_rather_than_converging(self):
        """Regression guard: asking for a chain that is not in the file must
        not silently succeed. It refuses — though the blocker message blames
        the prediction ('no sample placed the target in contact with the
        partner') rather than naming the missing chain, so an operator debugging
        a typo is pointed at the wrong thing."""
        atoms = chain_of_atoms("A", range(1, 11)) + chain_of_atoms("B", range(1, 11), x0=100.0)
        c = build_consensus([contacts_between(atoms, "A", "Z", 4.5, f"s{i}")
                             for i in range(5)])
        self.assertFalse(c.converged)
        self.assertTrue(c.blockers)


# ── 7. thresholds and behaviour that held up ────────────────────────────────

class AdversarialThresholdTests(unittest.TestCase):
    """These passed. They are regression guards, not defects."""

    def test_thresholds_are_inclusive_exactly_at_their_boundaries(self):
        """Guards against a future `>` / `>=` flip. Exactly 0.60 support,
        exactly 0.60 occupancy and exactly 4.5 A all count as passing; a
        hair over the cutoff does not."""
        # distinct predictions that agree, as a real ensemble would be
        s = [span(f"a{i}", range(10, 21 - i), range(50, 57)) for i in range(3)]
        s += [SampleInterface(sample=f"e{i}") for i in range(2)]
        c = build_consensus(s)                      # support == 3/5 == 0.60
        self.assertAlmostEqual(c.ensemble_support, 0.60)
        self.assertEqual(c.blockers, [])

        cluster = [span(f"a{i}", range(10, 21), range(50, 57)) for i in range(3)]
        cluster += [span(f"b{i}", range(10, 16), range(50, 57)) for i in range(2)]
        occ = contact_occupancy(cluster)            # residue 16 == 3/5 == 0.60
        self.assertAlmostEqual(occ[16], 0.60)
        self.assertIn(16, smallest_confident_segment(cluster, ConsensusConfig()).occupancy)

        at = 4.5
        pair = [Atom("A", 1, "CA", "C", 0.0, 0.0, 0.0),
                Atom("B", 2, "CA", "C", at, 0.0, 0.0)]
        self.assertEqual(len(contacts_between(pair, "A", "B", at).contacts), 1)
        pair[1] = Atom("B", 2, "CA", "C", at + 1e-6, 0.0, 0.0)
        self.assertEqual(len(contacts_between(pair, "A", "B", at).contacts), 0)

    def test_a_fifty_fifty_split_can_never_clear_the_support_floor(self):
        """Guards the one place where 'dominant' would be meaningless. Two
        equally-sized clusters can reach at most 0.50 support because the
        denominator is every sample, so the tie-break in `sort(key=len)` can
        never decide a screening verdict. Sound as written."""
        for k in (2, 3, 4, 5):
            with self.subTest(k=k):
                s = [span(f"a{i}", range(10, 21), range(50, 57)) for i in range(k)]
                s += [span(f"b{i}", range(300, 311), range(700, 707)) for i in range(k)]
                c = build_consensus(s)
                self.assertAlmostEqual(c.ensemble_support, 0.5)
                self.assertFalse(c.converged)

    def test_negative_and_gapped_residue_numbering_is_handled(self):
        """Expression tags are numbered from -20. Guards against an implicit
        assumption that residue numbers start at 1."""
        s = [span(f"s{i}", range(-20, -9 - (i % 3)), range(50, 57)) for i in range(5)]
        seg = build_consensus(s).target_segment
        self.assertIsNotNone(seg)
        self.assertEqual(seg.start, -20)

    def test_empty_cluster_inputs_do_not_raise(self):
        """Guards the degenerate paths a caller can reach from an empty run."""
        self.assertEqual(contact_occupancy([]), {})
        self.assertIsNone(smallest_confident_segment([], ConsensusConfig()))
        self.assertEqual(cluster_interfaces([], ConsensusConfig()), [])


# ── 8. the experimental calibration ─────────────────────────────────────────

@unittest.skipUnless(CIF.exists(), "downloads/9F6Y.cif absent — see team/TASKS.md")
class AdversarialCalibrationTests(unittest.TestCase):
    """The author's calibration tests are the module's licence to operate. They
    are weaker than they look."""

    @classmethod
    def setUpClass(cls):
        cls.atoms = parse_mmcif(CIF)
        cls.iface = contacts_between(cls.atoms, "B", "A", 4.5, sample="9F6Y")

    # WAS A DEFECT, NOW FIXED: parse_mmcif keeps ATOM records only, and modified residues are
    # deposited as HETATM.
    def test_the_phosphoserine_is_not_silently_deleted(self):
        """Prevents: calibrating the geometry on a structure with the
        biologically decisive residue removed.

        9F6Y is the complex with the *phosphorylated* ELK1 motif. pSer383 is
        deposited as `HETATM ... SEP B ... 383` — ten atoms including the
        phosphate. `parse_mmcif` filters on `group_PDB != "ATOM"`, so all ten
        are dropped and the recovered ELK1 interface is 375-382: the motif
        minus the phospho-residue that makes the interaction phospho-dependent.

        The author's `test_target_contacts_fall_inside_the_published_motif`
        cannot catch this — it only checks that recovered residues are a subset
        of 374-384 and that there are at least six of them, so deleting a
        residue makes that assertion *easier* to pass. The same filter will
        drop every SEP/TPO/PTR/MSE in any predicted or experimental complex
        this pipeline touches."""
        self.assertIn(ELK1_PHOSPHOSERINE, self.iface.target_residues,
                      f"recovered {sorted(self.iface.target_residues)}; "
                      f"pSer383 dropped as HETATM SEP")

    # WAS A DEFECT, NOW FIXED: same N=1 hole, on the structure the module is calibrated against.
    def test_the_deposited_complex_alone_reports_full_convergence(self):
        """Prevents: the single highest-quality input in the project — an
        experimental cryo-EM complex — being scored as a converged ensemble.
        It returns support 1.0, segment 375-381 and no blockers. The right
        answer for one structure is a refusal with a note that it is
        experimental, not a 100% self-consistency score."""
        c = build_consensus([self.iface])
        self.assertTrue(c.blockers, f"support {c.ensemble_support}, "
                                    f"blockers {c.blockers}")

    def test_the_grid_search_agrees_with_a_brute_force_scan(self):
        """The spatial index is the one piece of geometry a silent bug would
        make undetectable. Cell size equals the cutoff and the 27-cell
        neighbourhood is searched, so no contact can be missed — verified here
        against an all-pairs scan on the real structure. Sound as written."""
        import math
        tgt = [a for a in self.atoms if a.chain == "B" and a.element != "H"]
        par = [a for a in self.atoms if a.chain == "A" and a.element != "H"]
        brute = {(a.resi, b.resi) for a in tgt for b in par
                 if math.dist((a.x, a.y, a.z), (b.x, b.y, b.z)) <= 4.5}
        self.assertEqual({(c.target_resi, c.partner_resi)
                          for c in self.iface.contacts}, brute)

    def test_self_chain_contacts_on_the_real_structure_converge_on_nothing(self):
        """Companion to the synthetic self-chain test: shows the concrete
        consequence on real coordinates. Chain B against itself yields a
        unanimous 'interface' at 374-381 — inside the published motif, so it
        would have survived a sanity check by eye.
        FIXED: `contacts_between` now refuses identical chains outright."""
        with self.assertRaises(ValueError):
            contacts_between(self.atoms, "B", "B", 4.5, sample="selfB")
        return
        self.assertTrue(any(c.target_resi == c.partner_resi and c.min_distance == 0.0
                            for c in self_iface.contacts))
        c = build_consensus([SampleInterface(sample=f"s{i}",
                                             contacts=self_iface.contacts)
                             for i in range(5)])
        self.assertTrue(c.converged, "self-chain no longer converges — good, "
                                     "delete this test")


if __name__ == "__main__":
    unittest.main()
