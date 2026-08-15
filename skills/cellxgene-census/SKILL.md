---
name: CELLxGENE Census
description: >
  Queries the CZ CELLxGENE Discover Census (~218M single cells across 1,845
  datasets) with the cellxgene-census Python API, no API key. Use when the user
  asks about single-cell RNA-seq, cell types, marker-gene expression across
  tissues, diseases, or species, cell-type composition, or wants to build a
  single-cell dataset or meta-analysis.
---

# CELLxGENE Census

A standardized, versioned corpus of single-cell RNA-seq data (Chan Zuckerberg
Biohub / CELLxGENE), queried from Python. No account and no API key.

## Setup

Python 3.10 to 3.12. No credentials of any kind.

1. Install if missing (idempotent):
  `python3 -c "import cellxgene_census" 2>/dev/null || pip install -U cellxgene-census`
2. The first query downloads a small metadata index; cell data streams from S3
  on demand, so the sandbox needs outbound network but no local dataset.

## Before you start

Pin the release so results are reproducible:

```python
import cellxgene_census
# a dated release, not "stable"/"latest", which drift between builds
census = cellxgene_census.open_soma(census_version="2025-11-08")
```

`"stable"` and `"latest"` move between builds and print a warning naming the
current date. Pin that date instead and record it beside any number you report.

## Known gotchas (verified 2026-08-14)

These bite on the first query, so read them before writing any:

- **Narrow `obs_value_filter` BEFORE pulling expression.** An unscoped pull
  streams the whole matrix from S3 and hangs, "human blood B cells" alone is
  920,197 cells. Always add `is_primary_data == True` plus a specific
  `tissue_general` / `cell_type` / `disease`, and request only the genes you need
  via `var_value_filter`. Size a query with a cheap count (below) before
  materializing a matrix.
- **`value_filter` is a predicate string, NOT SQL.** It supports `==`, `!=`,
  `in`, `and`, `or`, and comparisons over obs/var columns, no `SELECT`, `JOIN`,
  or aggregation. Filter rows here; aggregate in pandas afterward. A column name
  that does not exist errors, so use the exact columns listed below.
- **`experimental` is not auto-imported.** `cellxgene_census.experimental`
  raises `AttributeError` until you `import cellxgene_census.experimental`
  explicitly. Needed for the precomputed cell embeddings.
- **Coarse vs fine tissue.** Use `tissue_general` for broad tissue (`'blood'`,
  `'brain'`, `'lung'`); `tissue` is fine-grained. Filtering on the wrong one
  silently returns far more or fewer cells than intended.
- **`is_primary_data == True` avoids double-counting.** The same cell can appear
  in several datasets; without this filter a count over-reports.
- **Prefer `obs_column_names` / `var_column_names`.** The older
  `column_names={"obs": [...]}` argument to `get_anndata` is deprecated and warns.

## Working style in a workspace

- Start from metadata: browse `census_info/datasets` and the obs schema to see
  what exists, decide the exact filter, then pull the smallest matrix that
  answers the question.
- Write the synthesis into the workspace files (e.g. `findings.tex`) as normal,
  reviewable edits, not left in tool output.
- Every number is reproducible from the pinned `census_version` plus the
  `value_filter`, record both next to the result, and cite the datasets by
  `dataset_title` / `collection_name` / `citation` from the datasets table.

---

_Reference below adapted from the official cellxgene-census docs
(chanzuckerberg.github.io/cellxgene-census). See the docsite for the
authoritative API._

## Corpus shape (verified 2026-08-14, release 2025-11-08)

- ~218M total cells, ~125M unique; 1,845 datasets; 5 organisms.
- Human: ~159M cells, 61,497 genes.
- Organisms (keys under `census["census_data"]`): `homo_sapiens`,
  `mus_musculus`, `macaca_mulatta`, `callithrix_jacchus`, `pan_troglodytes`. In
  `get_anndata`, name them `"Homo sapiens"`, `"Mus musculus"`, and so on.

## Cell metadata columns (`obs`), the `value_filter` fields

```
soma_joinid, dataset_id, assay, assay_ontology_term_id, cell_type,
cell_type_ontology_term_id, development_stage, development_stage_ontology_term_id,
disease, disease_ontology_term_id, donor_id, is_primary_data, observation_joinid,
self_reported_ethnicity, self_reported_ethnicity_ontology_term_id, sex,
sex_ontology_term_id, suspension_type, tissue, tissue_ontology_term_id,
tissue_type, tissue_general, tissue_general_ontology_term_id, raw_sum, nnz,
raw_mean_nnz, raw_variance_nnz, n_measured_vars
```

Gene metadata columns (`var`): `soma_joinid, feature_id, feature_name,
feature_type, feature_length, nnz, n_measured_obs`. Filter genes by
`feature_name` (symbol, e.g. `'CD19'`) or `feature_id` (Ensembl).

Inspect columns live without downloading data:

```python
[f.name for f in census["census_data"]["homo_sapiens"].obs.schema]
```

## Query recipes

**Cheap count (size a query before pulling a matrix):**

```python
human = census["census_data"]["homo_sapiens"]
n = len(human.obs.read(
    value_filter="tissue_general == 'blood' and cell_type == 'B cell' and is_primary_data == True",
    column_names=["soma_joinid"],
).concat())
```

**Cell metadata as a DataFrame:**

```python
obs = human.obs.read(
    value_filter="tissue_general == 'tongue' and is_primary_data == True",
    column_names=["cell_type", "assay", "disease"],
).concat().to_pandas()
obs["cell_type"].value_counts()
```

**Expression matrix into AnnData (scope tightly, name the genes):**

```python
adata = cellxgene_census.get_anndata(
    census,
    organism="Homo sapiens",
    obs_value_filter="tissue_general == 'tongue' and is_primary_data == True",
    var_value_filter="feature_name in ['EPCAM', 'PTPRC']",
    obs_column_names=["cell_type", "disease"],
    var_column_names=["feature_name"],
)
# adata.X is raw counts; adata.obs / adata.var carry the metadata columns above.
```

**Datasets table (meta-analysis entry point):**

```python
ds = census["census_info"]["datasets"].read().concat().to_pandas()
# columns: soma_joinid, citation, collection_id, collection_name, collection_doi,
# collection_doi_label, dataset_id, dataset_version_id, dataset_title,
# dataset_h5ad_path, dataset_total_cell_count
```

**Precomputed cell embeddings (experimental, explicit import):**

```python
import cellxgene_census.experimental as ex
ex.get_all_available_embeddings("2025-11-08")  # scvi, tf-sapiens, tf-exemplar-human/mouse
```

Always `census.close()`, or use `with cellxgene_census.open_soma(...) as census:`,
when done.
