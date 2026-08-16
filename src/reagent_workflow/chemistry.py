"""Approved-drug library: provenance, cleanup, descriptors, enrichment (§17–23).

The enrichment hypothesis is stated so it can be refuted. A molecule that
competes for a hydrophobic groove may benefit from presenting a hydrophobic face
to the surface while keeping polar groups toward solvent — motivated by the ELK1
site, where three hydrophobic residues dominate and the phosphorylated serine
points outward. That is the **amphiphilic compatibility hypothesis**, not a
validated pharmacophore.

Which is why the library is not simply the top-scoring molecules. Taking only
enriched compounds would make any later "amphiphilic compounds did well" finding
circular. Three arms ship together — enriched, diverse controls, and deliberate
counter-hypothesis compounds — so the screen can answer whether the enrichment
actually helped, and can come back saying it did not.

Requires RDKit. Every function fails loudly if a compound lacks provenance;
a compound with no public source never reaches a screen.
"""
from __future__ import annotations

import json
import math
import re
import statistics
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .discovery_config import ChemistryConfig


class LibraryArm(StrEnum):
    ENRICHED = "enriched"                      # fits the hypothesis
    DIVERSE_CONTROL = "diverse_control"        # chemically spread, hypothesis-agnostic
    COUNTER_HYPOTHESIS = "counter_hypothesis"  # deliberately poor fit


class Compound(BaseModel):
    """One approved drug. Provenance is required, not optional (§17)."""
    model_config = ConfigDict(extra="forbid")
    name: str
    smiles: str                                  # as supplied by the source
    parent_smiles: str | None = None             # after salt/mixture stripping
    source: str                                  # DrugCentral | ChEMBL
    source_id: str
    source_version: str
    retrieved: str
    approval_status: str | None = None
    known_target: str | None = None
    indication: str | None = None

    # descriptors (§18) — features, never a hard Rule-of-Five gate
    mw: float | None = None
    clogp: float | None = None
    tpsa: float | None = None
    hbd: int | None = None
    hba: int | None = None
    rotatable_bonds: int | None = None
    heavy_atoms: int | None = None
    formal_charge: int | None = None

    amphiphilicity_proxy: float | None = None
    amphiphilicity_caveat: str = ("heuristic chemical enrichment metric; "
                                  "not an experimentally validated descriptor")
    arm: LibraryArm | None = None
    rejected_reason: str | None = None

    @property
    def has_provenance(self) -> bool:
        return bool(self.source and self.source_id and self.source_version
                    and self.retrieved)


# ── cleanup (§18) ───────────────────────────────────────────────────────────

_METALS = {"Fe", "Pt", "Gd", "Tc", "Ru", "Au", "Ag", "As", "Sb", "Bi", "Hg",
           "Al", "Zn", "Cu", "Mn", "Co", "Cr", "Ni", "Ti", "Zr", "La", "Sm"}


def standardize(smiles: str, *, max_heavy_atoms: int = 150) -> tuple[str | None, str | None]:
    """Return (parent_smiles, rejection_reason). Stereochemistry is preserved.

    Salts and counterions are stripped to the parent; genuine mixtures, biologics
    and unsupported metal complexes are rejected rather than silently mangled.
    """
    from rdkit import Chem, RDLogger
    RDLogger.DisableLog("rdApp.*")

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None, "unparseable SMILES"

    frags = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=False)
    if frags:
        organic = [f for f in frags
                   if sum(1 for a in f.GetAtoms() if a.GetSymbol() == "C") >= 3]
        if not organic:
            return None, "no organic parent fragment (salt or inorganic only)"
        organic.sort(key=lambda m: m.GetNumHeavyAtoms(), reverse=True)
        # A second comparably sized organic fragment is a real mixture, not a salt.
        if len(organic) > 1 and organic[1].GetNumHeavyAtoms() > 0.5 * organic[0].GetNumHeavyAtoms():
            return None, "disconnected mixture of comparable components"
        mol = organic[0]

    if any(a.GetSymbol() in _METALS for a in mol.GetAtoms()):
        return None, "metal-containing structure unsupported by the docking workflow"
    n_heavy = mol.GetNumHeavyAtoms()
    if n_heavy < 6:
        return None, "too small to dock meaningfully"
    if n_heavy > max_heavy_atoms:
        return None, f"biologic or oversized ({n_heavy} heavy atoms)"
    try:
        Chem.SanitizeMol(mol)
    except Exception as e:
        return None, f"failed sanitization: {type(e).__name__}"
    return Chem.MolToSmiles(mol), None


def describe(parent_smiles: str) -> dict:
    """Standard 2D descriptors (§18). Soft features, not a filter."""
    from rdkit import Chem
    from rdkit.Chem import Crippen, Descriptors, rdMolDescriptors
    m = Chem.MolFromSmiles(parent_smiles)
    if m is None:
        return {}
    return {
        "mw": round(Descriptors.MolWt(m), 2),
        "clogp": round(Crippen.MolLogP(m), 3),
        "tpsa": round(rdMolDescriptors.CalcTPSA(m), 2),
        "hbd": rdMolDescriptors.CalcNumHBD(m),
        "hba": rdMolDescriptors.CalcNumHBA(m),
        "rotatable_bonds": rdMolDescriptors.CalcNumRotatableBonds(m),
        "heavy_atoms": m.GetNumHeavyAtoms(),
        "formal_charge": Chem.GetFormalCharge(m),
    }


# ── amphiphilicity proxy (§20) ──────────────────────────────────────────────

_HYDROPHOBIC = {"Hydrophobe", "LumpedHydrophobe"}
_POLAR = {"Donor", "Acceptor", "PosIonizable", "NegIonizable"}


def amphiphilicity_proxy(parent_smiles: str, cfg: ChemistryConfig | None = None
                         ) -> float | None:
    """Median spatial separation of hydrophobic and polar centroids.

    Median across conformers, not the best one: picking the best conformer would
    reward any molecule flexible enough to eventually look amphiphilic. The
    balance term stops a near-entirely hydrophobic molecule scoring highly on the
    strength of one distant oxygen.

    Deterministic — ETKDG runs from a fixed seed, so the same SMILES always gives
    the same number.
    """
    cfg = cfg or ChemistryConfig()
    from rdkit import Chem, RDConfig
    from rdkit.Chem import AllChem, ChemicalFeatures
    import os

    m = Chem.MolFromSmiles(parent_smiles)
    if m is None:
        return None
    m = Chem.AddHs(m)
    params = AllChem.ETKDGv3()
    params.randomSeed = cfg.conformer_seed
    params.useRandomCoords = True
    if AllChem.EmbedMultipleConfs(m, numConfs=cfg.conformers_per_compound,
                                  params=params) == 0:
        return None

    factory = ChemicalFeatures.BuildFeatureFactory(
        os.path.join(RDConfig.RDDataDir, "BaseFeatures.fdef"))
    feats = factory.GetFeaturesForMol(m)
    hyd = [f for f in feats if f.GetFamily() in _HYDROPHOBIC]
    pol = [f for f in feats if f.GetFamily() in _POLAR]
    if not hyd or not pol:
        return 0.0            # not amphiphilic; a real answer, not a failure
    balance = 2 * min(len(hyd), len(pol)) / (len(hyd) + len(pol))

    scores: list[float] = []
    for conf in m.GetConformers():
        cid = conf.GetId()
        def centroid(fs):
            pts = [f.GetPos(cid) for f in fs]
            return (sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts),
                    sum(p.z for p in pts) / len(pts))
        h, p = centroid(hyd), centroid(pol)
        pos = conf.GetPositions()
        com = pos.mean(axis=0)
        rg = math.sqrt(((pos - com) ** 2).sum(axis=1).mean())
        if rg <= 0:
            continue
        scores.append(math.dist(h, p) / rg * balance)
    return round(statistics.median(scores), 4) if scores else None


# ── library assembly (§21) ──────────────────────────────────────────────────

def _fingerprints(smiles: list[str]):
    from rdkit import Chem
    from rdkit.Chem import rdFingerprintGenerator
    gen = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
    out = []
    for s in smiles:
        m = Chem.MolFromSmiles(s)
        out.append(gen.GetFingerprint(m) if m is not None else None)
    return out


def _maxmin(cands: list[Compound], k: int) -> list[Compound]:
    """MaxMin diversity pick, so one scaffold family cannot fill an arm."""
    from rdkit import DataStructs
    if k >= len(cands):
        return list(cands)
    fps = _fingerprints([c.parent_smiles or c.smiles for c in cands])
    idx = [i for i, f in enumerate(fps) if f is not None]
    if not idx:
        return cands[:k]
    picked = [idx[0]]
    while len(picked) < k and len(picked) < len(idx):
        best, best_d = None, -1.0
        for i in idx:
            if i in picked:
                continue
            d = min(1.0 - DataStructs.TanimotoSimilarity(fps[i], fps[j]) for j in picked)
            if d > best_d:
                best, best_d = i, d
        if best is None:
            break
        picked.append(best)
    return [cands[i] for i in picked]


def assemble_library(compounds: list[Compound], n: int,
                     cfg: ChemistryConfig | None = None) -> list[Compound]:
    """Three arms, so the enrichment hypothesis stays falsifiable (§21).

    Every compound must carry provenance; anything without it is dropped here
    rather than appearing in a screen with no traceable source.
    """
    cfg = cfg or ChemistryConfig()
    pool = [c for c in compounds
            if c.has_provenance and c.amphiphilicity_proxy is not None]
    if not pool:
        return []
    arms = cfg.library_arms(min(n, len(pool)))

    ranked = sorted(pool, key=lambda c: c.amphiphilicity_proxy, reverse=True)
    # Enriched: diverse pick from the amphiphilic half, not simply the top slice.
    top_half = ranked[:max(arms["enriched"], len(ranked) // 2)]
    enriched = _maxmin(top_half, arms["enriched"])
    taken = {id(c) for c in enriched}

    bottom = [c for c in reversed(ranked) if id(c) not in taken]
    counter = bottom[:arms["counter_hypothesis"]]
    taken |= {id(c) for c in counter}

    rest = [c for c in ranked if id(c) not in taken]
    diverse = _maxmin(rest, arms["diverse_control"])

    for c in enriched:
        c.arm = LibraryArm.ENRICHED
    for c in diverse:
        c.arm = LibraryArm.DIVERSE_CONTROL
    for c in counter:
        c.arm = LibraryArm.COUNTER_HYPOTHESIS
    return enriched + diverse + counter


# ── runtime budget (§23) ────────────────────────────────────────────────────

class RuntimeEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    median_seconds_per_ligand: float
    p90_seconds_per_ligand: float
    effective_parallel_workers: int
    remaining_seconds: float
    estimated_n: int
    clamped: bool
    note: str = ""


def estimate_screen_size(timings: list[float], remaining_seconds: float,
                         workers: int, cfg: ChemistryConfig | None = None
                         ) -> RuntimeEstimate:
    """Size the screen from measured throughput, never from a guess.

    A finished 180-compound screen beats a 1000-compound screen still running
    during the demo, so the headroom factor is applied and the result is clamped.
    """
    cfg = cfg or ChemistryConfig()
    if not timings or workers < 1 or remaining_seconds <= 0:
        return RuntimeEstimate(median_seconds_per_ligand=0.0, p90_seconds_per_ligand=0.0,
                               effective_parallel_workers=max(workers, 0),
                               remaining_seconds=max(remaining_seconds, 0.0),
                               estimated_n=cfg.fast_vina_min_n, clamped=True,
                               note="no benchmark available; fell back to the configured minimum")
    ordered = sorted(timings)
    median = statistics.median(ordered)
    p90 = ordered[min(len(ordered) - 1, int(math.ceil(0.9 * len(ordered)) - 1))]
    raw = math.floor(remaining_seconds * workers / median * cfg.runtime_headroom)
    n = max(cfg.fast_vina_min_n, min(cfg.fast_vina_max_n, raw))
    return RuntimeEstimate(
        median_seconds_per_ligand=round(median, 3),
        p90_seconds_per_ligand=round(p90, 3),
        effective_parallel_workers=workers,
        remaining_seconds=round(remaining_seconds, 1),
        estimated_n=n, clamped=(n != raw),
        note=(f"benchmark supports {raw}; clamped to [{cfg.fast_vina_min_n}, "
              f"{cfg.fast_vina_max_n}]" if n != raw else "within configured bounds"))


# ── depiction ───────────────────────────────────────────────────────────────
#
# A docking score is a number about a molecule nobody can see. The interface
# shows the pose in the receptor, and these two functions give it the molecule
# itself: the 2D structure a chemist reads at a glance, and the 3D atoms and
# bonds of the pose that was actually scored.

def depict(smiles: str, width: int = 240, height: int = 150) -> str:
    """2D structure as inline SVG, coloured by the page rather than by RDKit.

    Drawn with the black-and-white palette and then re-pointed at
    `currentColor`, so one depiction is legible on both the light and the dark
    theme. RDKit's default palette hardcodes black bonds, which vanish on a
    dark background — and shipping two SVGs per compound to avoid that is a
    lot of bytes to solve a problem CSS already solves.
    """
    from rdkit import Chem, RDLogger
    from rdkit.Chem.Draw import rdMolDraw2D

    RDLogger.DisableLog("rdApp.*")
    mol = Chem.MolFromSmiles(smiles or "")
    if mol is None:
        return ""
    drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
    opts = drawer.drawOptions()
    opts.useBWAtomPalette()
    opts.clearBackground = False
    opts.bondLineWidth = 1
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
    drawer.FinishDrawing()
    svg = drawer.GetDrawingText()
    return (svg.replace("<?xml version='1.0' encoding='iso-8859-1'?>", "")
               .replace("#000000", "currentColor")
               .replace("stroke:#000", "stroke:currentColor")
               .strip())


_MOLBLOCK_COUNTS = 3          # line index of the counts line in a V2000 molblock


def parse_molblock(block: str) -> dict:
    """Atoms and bonds out of a V2000 molblock, without a chemistry toolkit.

    The docked pose arrives as a molblock in the receptor's coordinate frame,
    which is exactly what a viewer needs — parsing it here rather than in the
    browser keeps the interface free of a chemistry dependency, and keeps the
    numbers it draws the same numbers the screen recorded.

    Hydrogens are dropped: Vina's output carries them, they triple the atom
    count, and they hide the heavy-atom skeleton the pose is about.
    """
    lines = (block or "").splitlines()
    if len(lines) <= _MOLBLOCK_COUNTS:
        return {"atoms": [], "bonds": []}
    try:
        n_atoms = int(lines[_MOLBLOCK_COUNTS][0:3])
        n_bonds = int(lines[_MOLBLOCK_COUNTS][3:6])
    except (ValueError, IndexError):
        return {"atoms": [], "bonds": []}

    atoms, keep = [], {}
    for i in range(n_atoms):
        parts = lines[_MOLBLOCK_COUNTS + 1 + i].split()
        if len(parts) < 4:
            continue
        element = parts[3]
        if element == "H":
            continue
        keep[i + 1] = len(atoms)          # molblock indices are 1-based
        atoms.append([element, round(float(parts[0]), 2),
                      round(float(parts[1]), 2), round(float(parts[2]), 2)])

    bonds = []
    for j in range(n_bonds):
        line = lines[_MOLBLOCK_COUNTS + 1 + n_atoms + j]
        try:
            a, b = int(line[0:3]), int(line[3:6])
        except (ValueError, IndexError):
            continue
        if a in keep and b in keep:
            bonds.append([keep[a], keep[b]])
    return {"atoms": atoms, "bonds": bonds}


# ── identity ────────────────────────────────────────────────────────────────

def inchikey(smiles: str) -> str | None:
    """InChIKey for a structure, or None if it cannot be built.

    The key is what makes a structure checkable against the outside world: two
    people who draw the same molecule get the same key, and a name does not.
    """
    from rdkit import Chem, RDLogger
    RDLogger.DisableLog("rdApp.*")
    mol = Chem.MolFromSmiles(smiles or "")
    if mol is None:
        return None
    try:
        return Chem.MolToInchiKey(mol)
    except Exception:                                        # noqa: BLE001
        return None


def _normalize_name(name: str) -> str:
    """Case- and punctuation-insensitive form for comparing compound names.

    PubChem's Title is one chosen display name, not the only correct one --
    "Tolfenamic acid" and its Title "2-(4-Chloro-2-methylanilino)benzoic acid"
    are the same molecule spelled two different ways, so a bare `==` on the
    raw strings would flag every compound whose Title happens to be its IUPAC
    name rather than its common one.
    """
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _name_confirmed_by_synonym(key: str, name: str, timeout: float) -> bool:
    """Does PubChem file `name` as a synonym for the compound behind `key`?

    Only called after the Title comparison has already failed. A Title
    mismatch alone is not proof of a different molecule: PubChem shows one
    name and records the rest -- including the name a model or a paper
    actually used -- as synonyms. This is the check that lets "Tolfenamic
    acid" pass despite its IUPAC Title, and the check a real substitution
    like "Fisetin" resolving to "Kaempferol" still fails.
    """
    import urllib.error
    import urllib.request
    url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/"
           f"{key}/synonyms/JSON")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            info = (json.loads(r.read()).get("InformationList", {})
                   .get("Information") or [{}])[0]
    except Exception:                                        # noqa: BLE001
        return False
    target = _normalize_name(name)
    return any(_normalize_name(s) == target for s in (info.get("Synonym") or []))


def verify_identity(smiles: str, name: str | None = None, timeout: float = 15.0) -> dict:
    """Is this structure a compound the outside world already knows?

    A SMILES string that parses is a valid molecule, not a correct one. A model
    asked for "a compound like imatinib" can return something that parses
    cleanly, is not imatinib, and no amount of RDKit checking will notice —
    the check has to leave the process. So the structure is hashed to an
    InChIKey and looked up in PubChem: a hit returns the CID and the name
    PubChem itself uses, and a miss says plainly that this structure is not a
    known compound rather than implying it failed.

    An InChIKey hit is a match on the *structure*, not on the name the model
    or source claimed for it -- "Fisetin" hashing to the InChIKey PubChem
    files under "Kaempferol" passes the InChIKey check and is still a
    different molecule wearing the requested name. So when `name` is given,
    it is compared (case/punctuation-normalised) against PubChem's Title, and
    only if that fails, against PubChem's recorded synonyms for the same
    InChIKey — a compound named by its common name rather than its IUPAC
    Title (e.g. "Tolfenamic acid") must not be flagged. Only when neither
    matches does this return "name_mismatch".

    Never raises and never blocks a screen. A network problem is reported as a
    network problem, because "unverified because PubChem was unreachable" and
    "unverified because this molecule does not exist in PubChem" are different
    facts and a screen must not confuse them.
    """
    key = inchikey(smiles)
    if not key:
        return {"status": "unparseable", "inchikey": None, "cid": None,
                "name": None, "note": "RDKit could not build a structure"}

    import urllib.error
    import urllib.request
    url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/"
           f"{key}/property/Title,IUPACName/JSON")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            props = (json.loads(r.read()).get("PropertyTable", {})
                     .get("Properties") or [{}])[0]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"status": "not_in_pubchem", "inchikey": key, "cid": None,
                    "name": None,
                    "note": "no PubChem record for this exact structure"}
        return {"status": "lookup_failed", "inchikey": key, "cid": None,
                "name": None, "note": f"PubChem returned HTTP {e.code}"}
    except Exception as e:                                   # noqa: BLE001
        return {"status": "lookup_failed", "inchikey": key, "cid": None,
                "name": None, "note": f"PubChem unreachable: {type(e).__name__}"}

    pubchem_name = props.get("Title") or props.get("IUPACName")
    if (name and pubchem_name
            and _normalize_name(name) != _normalize_name(pubchem_name)
            and not _name_confirmed_by_synonym(key, name, timeout)):
        return {"status": "name_mismatch", "inchikey": key,
                "cid": props.get("CID"), "name": pubchem_name,
                "proposed_name": name,
                "note": (f"proposed name '{name}' matches neither PubChem's "
                        f"Title '{pubchem_name}' nor a recorded synonym")}

    return {"status": "verified", "inchikey": key,
            "cid": props.get("CID"),
            "name": pubchem_name,
            "note": "structure matches a PubChem record by InChIKey"}
