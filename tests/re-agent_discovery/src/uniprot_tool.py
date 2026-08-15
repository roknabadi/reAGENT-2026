"""
UniProt (+ PDB + ChEMBL) via Paperclip's protein SQL corpus
(`paperclip sql -s proteins`) -- Stage 3's expression/tractability half. Same
architecture as literature_search in agent_tools.py: Paperclip is an
agent-mediated CLI/MCP tool, not something this module can call over HTTP, so
this is a cache-only reader. The write path (record_uniprot_result) is called
by an agent session after actually running the paperclip sql query below.

Deliberately one wide query per gene (not five separate lookups) --
uniprot_v.functions/diseases/subcellular_locations/features, plus
pdb_v.structures_by_accession and chembl_v.{drugs,bioactivities}_by_accession,
joined through uniprot_v.genecentric to resolve the canonical human accession.
Costs ~7-20s per gene against the real corpus; batch, don't loop blindly.
"""

QUERY_TEMPLATE = """
WITH acc AS (
  SELECT g.accession FROM uniprot_v.genecentric g
  WHERE g.gene_name = '{gene}' AND g.organism = 'Homo sapiens' LIMIT 1
),
base AS (SELECT p.accession, p.protein_name, p.sequence_length, p.annotation_score FROM uniprot_v.proteins p JOIN acc ON acc.accession = p.accession),
fn AS (SELECT accession, LEFT(function_text, 300) AS function_text FROM uniprot_v.functions WHERE accession IN (SELECT accession FROM acc)),
dis AS (SELECT accession, STRING_AGG(DISTINCT disease_name, '; ') AS diseases FROM uniprot_v.diseases WHERE accession IN (SELECT accession FROM acc) GROUP BY accession),
loc AS (SELECT accession, STRING_AGG(DISTINCT location, '; ') AS locations FROM uniprot_v.subcellular_locations WHERE accession IN (SELECT accession FROM acc) GROUP BY accession),
dom AS (SELECT accession, STRING_AGG(DISTINCT feature_type || ' (' || start_pos || '-' || end_pos || ')', '; ') AS domains FROM uniprot_v.features WHERE accession IN (SELECT accession FROM acc) AND feature_type IN ('Domain','DNA binding','Region','Compositional bias','Zinc finger') GROUP BY accession),
pdbs AS (SELECT accession, COUNT(*) AS n_structures, MIN(resolution) AS best_resolution FROM pdb_v.structures_by_accession WHERE accession IN (SELECT accession FROM acc) GROUP BY accession),
drugs AS (SELECT accession, COUNT(*) AS n_drugs, STRING_AGG(DISTINCT drug_name, '; ') AS drug_names FROM chembl_v.drugs_by_accession WHERE accession IN (SELECT accession FROM acc) GROUP BY accession),
bioact AS (SELECT accession, COUNT(DISTINCT compound_chembl_id) AS n_compounds, MAX(pchembl_value) AS best_pchembl FROM chembl_v.bioactivities_by_accession WHERE accession IN (SELECT accession FROM acc) GROUP BY accession)
SELECT base.*, fn.function_text, dis.diseases, loc.locations, dom.domains, pdbs.n_structures, pdbs.best_resolution, drugs.n_drugs, drugs.drug_names, bioact.n_compounds, bioact.best_pchembl
FROM base
LEFT JOIN fn ON fn.accession = base.accession
LEFT JOIN dis ON dis.accession = base.accession
LEFT JOIN loc ON loc.accession = base.accession
LEFT JOIN dom ON dom.accession = base.accession
LEFT JOIN pdbs ON pdbs.accession = base.accession
LEFT JOIN drugs ON drugs.accession = base.accession
LEFT JOIN bioact ON bioact.accession = base.accession
"""

import json

import config

UNIPROT_CACHE_PATH = config.RESULTS_DIR / "uniprot_cache.json"


def query_for(gene: str) -> str:
    """Returns the exact `paperclip sql -s proteins "..."` query text to run
    for this gene -- the agent runs it, parses the one-row result, and calls
    record_uniprot_result()."""
    return QUERY_TEMPLATE.format(gene=gene)


def get_cached(gene: str) -> dict | None:
    if not UNIPROT_CACHE_PATH.exists():
        return None
    return json.loads(UNIPROT_CACHE_PATH.read_text()).get(gene)


def record_uniprot_result(gene: str, row: dict, found: bool = True) -> dict:
    """`row` is the parsed single-row SQL result (or {} if `found=False` --
    genecentric had no human entry for this gene, which does happen)."""
    cache = json.loads(UNIPROT_CACHE_PATH.read_text()) if UNIPROT_CACHE_PATH.exists() else {}
    cache[gene] = {"found": found, **row}
    UNIPROT_CACHE_PATH.write_text(json.dumps(cache, indent=2))
    return cache[gene]
