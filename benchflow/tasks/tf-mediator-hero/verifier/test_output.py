"""Structural verifier for the TF–Mediator hero-hypothesis task."""

import json
import os
from pathlib import Path
from urllib.parse import urlparse

WORKSPACE = Path(os.environ.get("BENCHFLOW_WORKSPACE", "/root"))
OUTPUT = WORKSPACE / "hero_hypothesis.json"


def load_output():
    assert OUTPUT.exists(), "hero_hypothesis.json was not created"
    return json.loads(OUTPUT.read_text(encoding="utf-8"))


def test_required_sections():
    data = load_output()
    required = {"schema_version", "hero", "selection", "evidence", "alternatives_rejected", "structure", "drug_discovery", "limitations"}
    assert required <= set(data), f"missing sections: {sorted(required - set(data))}"
    assert data["schema_version"] == "1.0"


def test_hero_is_complete():
    hero = load_output()["hero"]
    for key in ("disease_context", "transcription_factor", "mediator_subunit", "hypothesis"):
        assert isinstance(hero.get(key), str) and hero[key].strip(), f"hero.{key} is empty"
    assert hero.get("confidence") in {"low", "medium", "high"}


def test_selection_is_auditable():
    selection = load_output()["selection"]
    for key in ("dependency_strength", "disease_specificity"):
        metric = selection[key]
        assert isinstance(metric.get("value"), (int, float))
        assert metric.get("definition")
        assert str(metric.get("source_url", "")).startswith("https://")
    assert selection["normal_cell_proxy"]["status"] in {"supported", "mixed", "missing"}


def test_evidence_has_public_provenance():
    evidence = load_output()["evidence"]
    assert isinstance(evidence, list) and len(evidence) >= 3
    domains = set()
    for record in evidence:
        assert record.get("claim")
        url = record.get("source_url", "")
        parsed = urlparse(url)
        assert parsed.scheme == "https" and parsed.netloc, f"invalid public URL: {url}"
        domains.add(parsed.netloc.lower())
        assert record.get("source_type") in {"primary", "database", "review"}
        assert record.get("interpretation") in {"observed", "computed", "predicted", "inference"}
        assert isinstance(record.get("supports"), bool)
    assert len(domains) >= 2, "evidence should not come from only one domain"


def test_alternative_and_limitations_are_explicit():
    data = load_output()
    assert data["alternatives_rejected"] and all(x.get("candidate") and x.get("reason") for x in data["alternatives_rejected"])
    assert data["limitations"] and all(isinstance(x, str) and x.strip() for x in data["limitations"])


def test_structural_abstention_is_valid():
    data = load_output()
    structure = data["structure"]
    discovery = data["drug_discovery"]
    assert structure.get("status") in {"experimental", "predicted", "missing"}
    assert discovery.get("status") in {"screened", "ready", "blocked"}
    if discovery["status"] == "screened":
        assert structure["status"] != "missing"
        assert discovery.get("search_region_basis")
        assert isinstance(discovery.get("compounds"), list) and discovery["compounds"]
    if discovery["status"] == "blocked":
        assert discovery.get("blockers"), "blocked screens must explain why"


def test_no_proprietary_project_material_is_named():
    lowered = OUTPUT.read_text(encoding="utf-8").lower()
    assert "therna" not in lowered
    assert "bioreasonrna" not in lowered
