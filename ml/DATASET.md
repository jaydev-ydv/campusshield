# CampusShield — Dataset Investigation and Decision

**Phase 4A.** What data can legitimately be used to train and evaluate the
classification component, and what cannot.

**Status: no external dataset is currently usable. The pipeline runs on a
clearly-labelled synthetic corpus, and the metrics it produces are a validation
that the pipeline works — not a comparative evaluation of the models.**

That distinction is the whole point of this document. Read §6 before quoting any
number from `ml/results/`.

---

## 1. What the project actually needs

The project deck requires a *comparative evaluation of classification approaches
— baseline vs. transformer-based — with accuracy/F1 metrics*, over reports
classified into CampusShield's controlled categories.

The target label space is `core.report_category`: **16 categories**, 9 incident
and 7 concern.

| Kind | Categories |
|---|---|
| incident (9) | `HARASS_VERBAL`, `HARASS_PHYSICAL`, `HARASS_DIGITAL`, `STALKING`, `INTIMIDATION`, `RAGGING`, `VOYEURISM`, `TRESPASS`, `OTHER_INCIDENT` |
| concern (7) | `LIGHTING_POOR`, `ISOLATED_AREA`, `CCTV_GAP`, `BLOCKED_ROUTE`, `ACCESS_CONTROL`, `TRANSPORT_SAFETY`, `OTHER_CONCERN` |

A usable dataset therefore needs free-text incident narratives whose labels map
onto a meaningful share of those 16 categories, under terms that permit academic
use.

---

## 2. Candidates investigated

### 2.1 SafeCity (Karlekar & Bansal, EMNLP 2018) — **REJECTED**

The only genuinely domain-matched dataset found. Crowd-sourced personal accounts
of sexual harassment, mostly from India, used in published NLP work.

| | |
|---|---|
| Paper | [SafeCity: Understanding Diverse Forms of Sexual Harassment Personal Stories](https://aclanthology.org/D18-1303/), EMNLP 2018 |
| Repository | [github.com/swkarlekar/safecity](https://github.com/swkarlekar/safecity) |
| Size | 9,892 stories — 7,201 train / 990 dev / 1,701 test |
| Labels | `Commenting`, `Ogling/Facial Expressions/Staring`, `Touching/Groping` (multi-label) |
| Origin | [maps.safecity.in/reports](http://maps.safecity.in/reports) |

**Two independent reasons it is not used.**

**(a) The terms require permission nobody has obtained.** The repository has
**no LICENSE file**. The README states, verbatim:

> "This data is for research purposes only and is publicly available at
> http://maps.safecity.in/reports. Please contact SafeCity moderators at
> http://maps.safecity.in/contact for permission before the use of this data."

That is a prior-permission requirement, not an open licence. "Publicly
accessible" is not the same as "licensed for use", and a dataset of people's
personal accounts of being harassed is precisely the kind where that distinction
deserves respect rather than a technicality. **No permission has been requested
or granted, so the dataset is not downloaded or incorporated.**

**(b) Even with permission, the label space does not cover the task.** Mapping
SafeCity's three labels onto CampusShield's sixteen:

| SafeCity label | CampusShield category | Quality |
|---|---|---|
| `Touching/Groping` | `HARASS_PHYSICAL` | **Good** — semantically equivalent |
| `Commenting` | `HARASS_VERBAL` | **Good** — verbal harassment/catcalling |
| `Ogling/Facial Expressions/Staring` | *(no category)* | **Poor** — see below |

`Ogling/Staring` has no CampusShield equivalent. It is non-verbal, so
`HARASS_VERBAL` is wrong; it involves no recording, so `VOYEURISM` is wrong.
Forcing it into either would be inventing a mapping the semantics do not support,
which §3 of the phase brief explicitly forbids. It would have to become
`OTHER_INCIDENT`, which teaches the model nothing useful.

**Coverage: 2 of 16 categories (12.5%).** The remaining 14 —
including every environmental concern, which is the deck's stated preventive
focus — would have **zero** training examples. A "comparative evaluation" over a
label space where 87.5% of classes are absent is not the evaluation the project
requires.

### 2.2 ADL H.E.A.T. Map — **REJECTED**

Antisemitic and extremist incident reports (harassment, vandalism, assault).
Wrong domain: not campus safety, not women's safety, and the incident taxonomy
does not overlap CampusShield's. Access and licensing require a direct request to
ADL; no public terms of use were found.

### 2.3 Generic incident-response / cybersecurity datasets — **REJECTED**

Several Apache-2.0 and MIT datasets exist on Hugging Face for "incident"
classification, but they concern IT security incidents and response playbooks.
The word "incident" is the only thing they share with this project. Matching a
dataset on a keyword rather than on its actual content is the failure mode §2 of
the brief warns against.

### 2.4 Institutional data — **NOT AVAILABLE**

Real CampusShield reports would be the ideal training source. There are none:
the system has no verified campus locations yet (see
[CAMPUS_LOCATIONS.md](../CAMPUS_LOCATIONS.md)), so no reports have been filed.

This is also where the strongest long-term source lies. `ml.report_classification`
carries `overridden_by` / `overridden_category_id`: every time an authority
corrects a prediction, that is a free gold label from the real deployment. The
schema was built for that in Phase 1; it needs the system to be in use.

---

## 3. Decision

**No external dataset is used.** The pipeline is built and validated against a
**synthetic corpus** that is labelled as such at every layer — in the file, in
the config, in the results JSON, and in the report heading.

This is the honest outcome of the investigation, not a shortcut taken to skip it.

---

## 4. The synthetic corpus

`ml/src/synthetic.py`. Generated deterministically from a fixed seed.

**How it is built.** Each of the 16 categories has a set of hand-written phrase
components — openings, actions, qualifiers, locations, times — combined
combinatorially. Categories deliberately **share vocabulary** (e.g. "at night",
"near the hostel", "I felt unsafe") so the task is not trivially separable by a
handful of keyword features. Without that overlap a bag-of-words model scores
~100% and the comparison measures nothing at all.

**What it is.** Text written to *resemble* the shape of campus safety reports,
by someone who has read the category definitions.

**What it is not.** It is **not real-world data**, not collected from students,
not annotated from real incidents, and carries none of the messiness real
narratives have — typos, code-switching, ambiguity, emotional register, reports
that fit two categories or none. Its vocabulary is bounded by what was written
into the generator.

**What this means for the metrics.** A model evaluated on synthetic data is being
tested on the generator's regularities, not on the language students actually
use. Scores will be **optimistic** relative to real performance, and the *gap*
between baseline and transformer is not informative either: templated text
rewards exactly the surface lexical cues TF-IDF is built for, which can make a
strong baseline look better than a transformer would on real text.

**Therefore: the numbers in `ml/results/` demonstrate that the pipeline is
correct and reproducible. They are not evidence about how either model would
perform on real campus reports, and must not be presented as such.**

---

## 5. Getting to a real evaluation

Three routes, in order of value.

**(a) Request SafeCity permission.** Contact the moderators at
[maps.safecity.in/contact](http://maps.safecity.in/contact), explaining the
academic use. If granted, it gives ~9,900 real narratives for a genuine
2-category evaluation (`HARASS_PHYSICAL`, `HARASS_VERBAL`) — a real result on a
reduced label space, which is far more defensible than a synthetic result on all
sixteen. `ml/src/dataset.py` has a documented loader stub for exactly this.
**This is the single highest-value action available and it costs one email.**

**(b) Manual annotation.** Three annotators write and cross-label 300–500 short
narratives against the 16 categories, reporting inter-annotator agreement
(Cohen's κ). Smaller than any public dataset, but genuinely human-authored and
honestly reportable. `ml.annotation` already exists to hold these with
`source = 'manual_seed'`.

**(c) Authority overrides from live use.** The long-term source, and the only one
that yields campus-specific data. Requires the system in production.

Until one of these lands, the project's comparative evaluation is a
**pipeline that is ready to run**, not a result.

---

## 6. How to read `ml/results/`

Every artifact carries `data_provenance`. Its values are the only ones the
pipeline will emit:

| Value | Meaning |
|---|---|
| `synthetic` | Generated by `ml/src/synthetic.py`. **Not real-world data.** |
| `public_dataset` | An externally licensed corpus. Requires a recorded licence in this file. |
| `manual_annotation` | Human-authored and labelled for this project. |
| `production_override` | Gold labels from authority corrections in live use. |

At the time of writing, **every** artifact is `synthetic`. A results file without
a `data_provenance` field should be treated as invalid.

---

## 7. Limitations, stated plainly

1. **No real-world evaluation exists.** Every number currently produced comes
   from synthetic text.
2. **The synthetic corpus cannot establish real accuracy**, and comparing the two
   models on it does not establish which is better on real reports.
3. **14 of 16 categories have no real-data path** even if SafeCity permission is
   granted.
4. **Duplicate-detection accuracy is not claimed.** No labelled pairs of
   genuinely duplicate reports exist, so §10's related-report work is delivered
   as a working, tested implementation with a defined evaluation strategy — not
   as an accuracy figure.
5. **Hotspot detection is unevaluated.** Clustering needs real reports at real
   locations; there are none.
