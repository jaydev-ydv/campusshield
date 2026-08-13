# CampusShield — ML/NLP Research and Evaluation

**Phase 4A.** A reproducible research pipeline for the project's AI/NLP component.

**The research pipeline here is still offline.** What *is* wired into the live
application, as of Phase 4C, is a model exported from it: see
[`ML_INTEGRATION.md`](../ML_INTEGRATION.md).

`scripts/export_baseline_model.py` trains the baseline on the same synthetic
corpus, bakes normalisation into the estimator, and writes an artifact the Flask
application loads. Reports submitted to the live system now receive a category
*suggestion*, a rule-based triage band, and proposed links to similar reports —
none of which changes what the student chose, and all of which are labelled as
coming from a model trained on synthetic text.

The transformer is **not** served, and the reasoning is in the export script: its
1.0000 on synthetic data is evidence the task is too easy, not that it would do
better on real reports.

---

## Read this first

**No external dataset could be legitimately used.** The one genuinely
domain-matched corpus — SafeCity — carries no licence and requires prior
permission from its moderators that has not been obtained. The full investigation
is in [DATASET.md](DATASET.md).

The pipeline therefore runs on a **synthetic corpus**, and every number it
produces is stamped `data_provenance: synthetic`.

> **What the results in `results/` demonstrate:** that the pipeline is correct,
> leakage-free, and reproducible.
>
> **What they do not demonstrate:** how either model performs on real campus
> reports. The gap between baseline and transformer is not transferable either —
> templated text rewards exactly the surface lexical cues TF-IDF is built for.

Saying otherwise in a dissertation would be a false claim about an experiment
that was not run.

---

## Run it

```bash
python -m ml.src.evaluate
```

One command, one config, one seed. Writes `results/evaluation.json` and
`results/EVALUATION.md`.

```bash
python -m ml.src.evaluate --quick            # tiny model and corpus, fast smoke run
python -m ml.src.evaluate --config path.yaml # alternative configuration
python -m pytest ml/tests -q                 # 73 tests, no model download
```

Requires `scikit-learn`, `pyyaml`, and — for the transformer arm — `torch` and
`transformers`. Without the latter two the pipeline records the transformer as
`not_run` with a reason, rather than inventing numbers.

---

## Structure

```
ml/
├── README.md              this file
├── DATASET.md             the dataset investigation and decision
├── configs/default.yaml   every parameter; nothing is hard-coded
├── data/                  empty and git-ignored — see data/README.md
├── src/
│   ├── labels.py          the 16 controlled categories + external label mapping
│   ├── synthetic.py       synthetic corpus generation
│   ├── dataset.py         loading, with provenance attached at the source
│   ├── preprocess.py      deterministic text normalisation
│   ├── splitting.py       stratified, duplicate-aware splitting
│   ├── leakage.py         five checks; the pipeline aborts on failure
│   ├── metrics.py         accuracy, macro/weighted F1, per-class, confusion
│   ├── baseline.py        TF-IDF + Logistic Regression
│   ├── transformer.py     DistilBERT fine-tuning
│   ├── similarity.py      find_related()
│   ├── hotspots.py        detect_hotspots()
│   └── evaluate.py        the single entrypoint
├── tests/                 73 tests
├── artifacts/             git-ignored; regenerable
└── results/               committed — the deliverable
```

---

## The label space

The 16 categories in `core.report_category` — the same values a student can pick
on the report form, not a taxonomy invented for the models. A test asserts
`labels.py` agrees with `sql/seed_report_categories.sql`, so the two cannot drift.

| Kind | Count | Categories |
|---|---|---|
| incident | 9 | `HARASS_VERBAL` `HARASS_PHYSICAL` `HARASS_DIGITAL` `STALKING` `INTIMIDATION` `RAGGING` `VOYEURISM` `TRESPASS` `OTHER_INCIDENT` |
| concern | 7 | `LIGHTING_POOR` `ISOLATED_AREA` `CCTV_GAP` `BLOCKED_ROUTE` `ACCESS_CONTROL` `TRANSPORT_SAFETY` `OTHER_CONCERN` |

---

## Method

**Primary metric: macro F1**, stated in the config rather than chosen after
seeing results. The real category distribution is imbalanced, and accuracy would
let a model score well by predicting the common categories and ignoring the rare
ones — which for this project are often the ones that matter most.

**Baseline:** TF-IDF (1–2 grams, sublinear tf) → Logistic Regression, with
`class_weight='balanced'`. Logistic Regression rather than LinearSVC because it
gives calibrated probabilities, and `ml.report_classification` has a `confidence`
column and a `label_scores` distribution to fill honestly.

**Transformer:** `distilbert-base-uncased`, fine-tuned. ~40% smaller and ~60%
faster than BERT-base at ~97% of its GLUE score — the right size for an
undergraduate prototype on CPU. Every hyperparameter, the hardware, and the
training time are recorded in the results file.

**Both arms are scored on the same `Split` object**, so "identical held-out test
set" is true by construction rather than by convention.

### Leakage prevention

Not a formality — it decides whether any number here means anything.

The unit that must not cross the split boundary is the **text**, not the row.
`train_test_split(stratify=y)` splits rows, so two copies of one narrative can
land either side; the model memorises the answer during training and is rewarded
for reciting it at test time. So texts are grouped by exact hash *and* by
5-token-shingle Jaccard similarity, and groups are assigned as units. Realised
split proportions differ slightly from the requested ones as a result — an
exactly-20% test set with leakage in it is worth less than a 19.4% one without.

Five checks run **before** training and **abort** on failure:

| Check | What it catches |
|---|---|
| `split_sizes` | Degenerate splits where a metric is meaningless |
| `exact_text_overlap` | Identical text in more than one split |
| `near_duplicate_overlap` | Paraphrase-level overlap the hash check misses |
| `example_id_overlap` | The same source example in two splits |
| `label_coverage` | A test class never seen in training |

A pipeline that warned and trained anyway would emit a results file
indistinguishable from a clean one.

---

## Related-report detection

`similarity.find_related(report, corpus)` — sentence-embedding-shaped interface,
TF-IDF vectors in the prototype so there is no extra model dependency. Swapping
in real sentence embeddings does not change the call site.

**Duplicate vs related.** A duplicate is the same event reported twice: high text
similarity **and** the same location **and** close in time. A related report is
the same pattern recurring — similar text, possibly weeks apart. Collapsing the
second into the first would hide exactly the recurrence the system exists to find.

**What it may not see.** `SimilarityCandidate` has no field for reporter
identity, `reporter_relationship`, or anything credibility-shaped. The
prohibition is structural, not a convention: there is nothing to reach for. Two
anonymous reports being linked does not imply the same reporter.

**No accuracy is claimed.** There is no labelled set of genuine duplicate pairs.
The evaluation strategy — stratified pair sampling, two annotators, Cohen's κ,
precision@k and recall, a threshold sweep — is recorded in the results file so
the absence of a number is explicit rather than an omission.

---

## Pattern surfacing

`hotspots.detect_hotspots(observations)` — locations with an elevated report rate
in a recent window, described by controlled location, time, category and
recurrence.

`HotspotObservation` likewise has no reporter field. Pattern surfacing describes
recurring signals; it does not decide whether any individual report is true.

Publication respects the same k-anonymity threshold as
`analytics.v_public_safety_map`: below k, an "aggregate" can identify both the
incident and the person who reported it.

**Not evaluated** — there are no real reports at real locations, because
`core.campus_location` is empty pending the coordinate survey. No Leaflet, no
map, no dashboard here.

---

## Database

The five ML tables already exist from the Phase 1 migration and **no new table
was created**:

`ml.model_version` · `ml.report_classification` · `ml.report_embedding` ·
`ml.annotation` · `ml.evaluation_run`

`ml.evaluation_run` is shaped exactly for what this pipeline produces —
`accuracy`, `macro_f1`, `weighted_f1`, `per_class_metrics`, `confusion_matrix`.
Persisting runs into it is a small step, deliberately left for the integration
phase: writing synthetic-data results into the production database would put
numbers there that look like real evaluation records.

No training data is stored in production report tables. The schema and the
privacy architecture are unchanged.

---

## Honest limitations

1. **No real-world evaluation exists.** Every number comes from synthetic text.
2. **The synthetic corpus cannot establish real accuracy**, and the
   baseline-vs-transformer comparison on it does not establish which is better
   on real reports.
3. **14 of 16 categories have no real-data path** even if SafeCity permission is
   granted — it would cover `HARASS_PHYSICAL` and `HARASS_VERBAL` only.
4. **Duplicate detection accuracy is not claimed.**
5. **Hotspot detection is unevaluated.**
6. **Only the baseline is integrated**, and only as a suggestion. Clustering,
   hotspots and the transformer remain offline. See `ML_INTEGRATION.md` §9.

### The highest-value next action

Email the SafeCity moderators at
[maps.safecity.in/contact](http://maps.safecity.in/contact) requesting academic
permission. If granted it yields ~9,900 real narratives for a genuine
two-category evaluation — a real result on a reduced label space, which is far
more defensible than a synthetic result across all sixteen. `dataset.py` has the
loader stub ready. It costs one email.
