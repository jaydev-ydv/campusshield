# CampusShield — ML Integration (Phase 4C)

**Status:** Implemented and verified end to end against real PostgreSQL with the
real trained model.

This document covers what happens when a report is submitted and a model reads
it. For the research pipeline and its evaluation see [`ml/README.md`](ml/README.md)
and [`ml/DATASET.md`](ml/DATASET.md); for the schema see `DATABASE.md`.

---

## 1. The rule everything else follows

**The machine suggests. The person decides.**

That is not a slogan; it is the same architectural pattern already used twice in
this codebase:

| Authoritative | Derived signal | Never overrides it |
|---|---|---|
| `core.report.location_id` — the campus location the student selected | A photograph's EXIF GPS | Phase 4B-2 |
| `core.report.declared_category_id` — the category the student chose | The model's prediction | **This phase** |

A person's deliberate choice is authoritative. A derived signal describes,
records what it thinks, and stops there.

---

## 2. What runs at submission

```
report submitted
      │
      ├─ classify        narrative → one of 16 categories + confidence
      │                  → ml.report_classification   (a suggestion)
      │
      ├─ embed           narrative → TF-IDF vector
      │                  → ml.report_embedding        (never leaves the server)
      │
      ├─ relate          cosine similarity vs open reports
      │                  → core.report_link           (unreviewed)
      │
      └─ score           rules over the incident
                         → core.risk_assessment       (triage ordering)
```

All four run inside the submission transaction and **all four are best-effort**.
`TriageService.run_for_report` catches everything. A model that will not load, a
malformed vector, a missing artifact — each ends the same way: a log line, and a
report that stands without a suggestion. That is the state every report was in
before this phase, and it is not a failure state.

*This is tested by injecting an estimator that raises on every call and asserting
the submission still returns 201.*

---

## 3. Which model, and why

**A TF-IDF + logistic regression baseline.** `ml/results/evaluation.json` records
DistilBERT at macro-F1 1.0000 and the baseline at 0.8667 — and the pipeline's own
warning says a near-perfect score means the task is too easy, not that the model
is excellent. **That gap is not evidence the transformer is better on real campus
reports**, so it is not a reason to serve it.

Against that non-reason: ~14 ms versus ~0.013 ms per inference, torch and
transformers in the Flask process, and probabilities a human cannot inspect. When
the evidence for a change is uninterpretable, the cheaper and more transparent
option wins. `ml/src/transformer.py` remains, and the export script would ship it
if a real evaluation ever justified it.

### The provenance rule

**An artifact cannot be loaded without `data_provenance`.** `load_bundle` raises
if it is missing. Every model this project can currently produce is trained on
synthetic text, and a suggestion from one must never reach a responder looking
like a finding from a system trained on real reports.

The provenance travels: artifact manifest → `ml.model_version.headline_metrics` →
API response → the words on the responder's screen.

### Train/serve skew

Text normalisation is **inside** the estimator (`app/ml/text.py` as the first
pipeline step), so `predict_proba([raw_narrative])` takes the raw text and there
is only one code path. A test asserts `app/ml/text.py` and `ml/src/preprocess.py`
produce byte-identical output across the whole synthetic corpus — if they drift,
nothing would fail at runtime, the model would just quietly get worse.

Producing an artifact:

```bash
python scripts/export_baseline_model.py --register
```

---

## 4. Risk scoring

**Rule-based, not learned.** A learned risk score needs outcome labels — reports
where someone recorded what actually happened. This project has none. A model
fitted on synthetic text would produce a number with no relationship to real
harm, and a number carries authority that prose does not: "risk 78" gets acted on.

| Factor | Weight | Why |
|---|---|---|
| `category_severity` | 40 | From `core.report_category.base_severity`, an institutional judgement |
| `emergency` | 25 | The student said so. Theirs, not inferred |
| `ongoing` | 20 | The difference between a response and a report |
| `recent` | 5 | Whether anyone can still act |
| `night_hours` | 5 | When help is thinnest |
| `location_repeat` | 5 | A property of a place, not a person |

It orders a queue. It is not a prediction of harm, not a measure of how serious
an incident was, and not an assessment of anybody. It decides nothing — no
dispatch, no assignment, no status change, no notification.

### What can never be a factor

Anything about the reporter: identity, history, `reporter_relationship`, whether
they were believed before.

`reporter_relationship` records **vantage point**, and treating "witness" as less
urgent than "affected" would be a credibility judgement wearing a metadata
field's clothes. Three things enforce this:

1. `score_report()` **is not given a reporter** — there is no argument to misuse.
   A test asserts the exact signature.
2. `ck_risk_factors_no_credibility_terms` rejects those keys at both the top
   level and inside `weights`. Exercised against the real database, both halves.
3. `BARRED_FACTOR_KEYS` mirrors the constraint, and a test reads the constraint
   out of `pg_constraint` to prove the two cannot drift.

---

## 5. Related reports, and the anonymity rule

Cosine similarity over TF-IDF vectors proposes links at ≥0.72 (`related`) and
≥0.88 (`duplicate`). Beyond 14 days apart a high-similarity pair becomes
`same_pattern` rather than `duplicate` — the same hazard in March and October is
a recurrence, not one event reported twice.

Every proposal enters `core.report_link` as `unreviewed`. **Nothing acts on an
unreviewed link.**

### A link never widens access

This is the novel privacy question in this phase. A link between an anonymous
report and an identified one is legitimate — two people can report the same
incident — but it must not become an identity bridge.

**A responder sees a link only if they could already open the other side on their
own.** Not the reference, not the location, not the fact that it exists.
Reviewing a link requires the same. Verified end to end: with 6 links in the
database, a security officer looking at their own report saw 0 of them and the
ICC reference appeared nowhere in the response.

Beyond that, the link carries no identity — `MlRepository` never queries
`identity.report_attribution`, so there is nothing to leak.

### Anonymity costs nothing

The classifier is given a narrative and nothing else — not the submission mode,
not the reporter, not the relationship. An anonymous report cannot be classified,
scored, or ranked differently from an identified one. Tested with identical
narratives submitted both ways.

---

## 6. What the responder sees

Under a heading that says **Suggestions**, after the account and the evidence — a
responder should read what the person wrote before reading what a model made of
it.

- The suggested category, its confidence, **and the reporter's own category
  beside it**, so disagreement reads as "the model differs", not "the student
  was wrong".
- A stated caveat wherever a suggestion appears: this model was not trained on
  real reports and its real-world accuracy is unknown.
- The triage band, labelled as ordering, with **every factor shown** — a number
  that sorts a queue must be answerable for on screen, not in a database someone
  would have to query.
- Proposed links, in language that does not assert them, with confirm/reject.

**Not shown:** the full 16-way probability distribution. It stays in
`label_scores` for evaluation; sixteen numbers on screen invite reading tea
leaves.

A responder who disagrees records a different category. That writes
`overridden_by` / `overridden_category_id` / `overridden_at` — and **does not
change `core.report.declared_category_id`**. The student's choice stays theirs.
Changing the report's own category is case management, with its own history trail.

---

## 7. Verification

**End to end, scratch database, real HTTP, real model — 21/21 checks.**

The model predicted `STALKING` at 0.419 confidence for a narrative whose declared
category was `HARASS_VERBAL` — a genuine disagreement, surfaced rather than
resolved. Near-identical narratives linked at 1.0000 similarity as `duplicate`,
`unreviewed`. An unrelated narrative produced no link. An ongoing emergency
scored 80.0 against 34.0. A credibility factor was refused by the database. The
development database was untouched: 0 reports, 0 models.

**Suites:** backend **377** (from 341; 51 new across two files), frontend **240**
(from 221). `ruff`, `ruff format`, `mypy` (61 files), `eslint`, `prettier`,
`tsc`, `vite build` all clean.

---

## 8. Known limitations

1. **Every number is from synthetic data.** No real-world accuracy is known or
   claimed. `ml/DATASET.md` records why no external corpus could be legitimately
   used. **This is the dominant limitation** and it is stated in the artifact, in
   the database, in the API and on screen.

2. **Risk scoring is unvalidated.** Rule-based and never checked against
   outcomes, because no outcome data exists. The weights are considered
   judgements, not measurements.

3. **Duplicate detection accuracy is not claimed.** The thresholds come from
   `ml/configs/default.yaml` and were not tuned against real reports.

4. **TF-IDF similarity is lexical.** Two accounts of the same incident in
   different words may not link. Sentence embeddings would do better; that is a
   model swap behind the same interface, not an architecture change.

5. **Similarity is an exact scan.** Fine at campus scale — a few thousand reports
   — and `DATABASE.md` rules out pgvector. When it stops being fast enough, an
   index goes in `MlRepository`.

6. **English only.** `core.report.source_language` exists and defaults to `en`;
   nothing translates or detects.

7. **Re-scoring is not scheduled.** `trigger_reason` supports
   `new_related_report` and `cluster_growth`, but only `initial` is ever written.
   A report's risk is scored once, at submission.

8. **Classification runs inline.** ~13 ms measured. If a heavier model is ever
   served this should move to a queue.

---

## 9. Deliberately not built

Automatic category assignment from a prediction. Automatic dispatch, assignment,
status change, or notification from any score. Reporter credibility or trust
scores of any kind. Learned risk scoring. ML on image content. Automatic
identification of anyone. Clustering and hotspot generation — `ml/src/hotspots.py`
exists and remains unwired, because hotspots need verified campus coordinates and
there are none. Serving DistilBERT on the strength of a synthetic 1.0000.
