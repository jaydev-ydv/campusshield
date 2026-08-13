"""Synthetic corpus generation.

**This produces synthetic text. It is not real-world data, it was not collected
from students, and it must never be described as either.** Every example it emits
carries `provenance='synthetic'`, and the pipeline refuses to write a results
file that does not record that.

Two design choices matter for whether the resulting metrics mean anything.

**Shared vocabulary between categories.** Each category draws from its own phrase
components, but `cross_category_noise` injects context from a *different*
category into a proportion of examples — "at night near the hostel" appears
across many categories, as it would in real reports. Without that overlap the
task is separable by a handful of keywords, a bag-of-words model scores ~100%,
and the baseline-vs-transformer comparison measures nothing at all.

**It is still much easier than real text.** Real narratives have typos,
code-switching, ambiguity, emotional register, and reports that fit two
categories or none. This generator has a bounded vocabulary and consistent
grammar. Scores here are optimistic, and the *gap* between the two models is not
informative: templated text rewards exactly the surface lexical cues TF-IDF is
built for. See DATASET.md §4.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .labels import CATEGORY_CODES

# Context shared across every category. This is what stops the task being a
# keyword-matching exercise.
SHARED_TIMES = (
    "late at night",
    "after my evening class",
    "around 9pm",
    "early in the morning",
    "during the lunch break",
    "just after dark",
    "on my way back from the library",
)

SHARED_PLACES = (
    "near the hostel gate",
    "behind the academic block",
    "in the parking area",
    "on the path to the library",
    "by the bus stop",
    "near the sports ground",
    "in the corridor outside the lab",
)

SHARED_FEELINGS = (
    "I felt very uncomfortable",
    "it made me anxious",
    "I did not feel safe",
    "I was scared to walk back alone",
    "I have been avoiding that route since",
)

# Per-category phrasing. Written from the category definitions in
# sql/seed_report_categories.sql.
TEMPLATES: dict[str, tuple[str, ...]] = {
    "HARASS_VERBAL": (
        "a group of men shouted comments at me as I walked past",
        "someone made remarks about my appearance and would not stop",
        "two people kept passing loud comments every time I went by",
        "a man catcalled me and then laughed with his friends",
        "someone whistled and said things I do not want to repeat",
    ),
    "HARASS_PHYSICAL": (
        "someone deliberately brushed against me in the crowd",
        "a man grabbed my arm and would not let go at first",
        "somebody touched me without my consent while I was queuing",
        "a person pushed into me on purpose and did not apologise",
        "someone put their hand on my shoulder and refused to move it",
    ),
    "STALKING": (
        "a man followed me from the gate all the way to the hostel",
        "the same person has been waiting outside my class for a week",
        "someone followed me on a motorcycle at walking pace",
        "I noticed the same man behind me on three different days",
        "a person kept following me and turned whenever I turned",
    ),
    "HARASS_DIGITAL": (
        "someone has been sending me messages after I asked them to stop",
        "I keep getting calls from an unknown number late at night",
        "a person created a fake account and messaged my friends about me",
        "someone posted photographs of me online without asking",
        "I have received repeated unwanted messages on my phone",
    ),
    "INTIMIDATION": (
        "a man threatened me when I told him to leave me alone",
        "someone said they would find out where I live",
        "a group blocked my way and told me not to report anything",
        "a person threatened to make trouble for me if I complained",
        "someone shouted threats at me when I walked away",
    ),
    "RAGGING": (
        "seniors forced juniors to do things they clearly did not want to",
        "a group made first year students stand and answer questions for an hour",
        "some seniors were pressuring juniors into humiliating tasks",
        "juniors were made to run errands and were mocked when they refused",
        "a group of seniors was intimidating new students",
    ),
    "VOYEURISM": (
        "a man was pointing his phone camera at students without permission",
        "someone was filming people from behind a pillar",
        "I saw a person taking photographs of women without them knowing",
        "somebody held a phone under the table to record",
        "a man was recording video and hid the phone when noticed",
    ),
    "TRESPASS": (
        "an outsider without an identity card was walking through the block",
        "a man who is not a student was loitering in the corridor",
        "someone came in through the gate without being checked",
        "an unknown person was sitting near the classrooms all afternoon",
        "a stranger was inside the building and could not say why",
    ),
    "OTHER_INCIDENT": (
        "something happened that does not fit the other options",
        "there was an incident I am not sure how to categorise",
        "an event occurred that I want to report but cannot classify",
        "something took place that worried me but is not listed here",
        "I want to report an incident that does not match the categories",
    ),
    "LIGHTING_POOR": (
        "the lights along this path have not worked for two weeks",
        "this stretch is completely dark once the sun goes down",
        "several lamps are broken and nobody has repaired them",
        "the lighting here is so dim you cannot see who is approaching",
        "the bulbs have failed and the area is pitch black",
    ),
    "ISOLATED_AREA": (
        "this route is completely deserted after evening classes end",
        "nobody passes through here and there is no help nearby",
        "this area is cut off and there is never anyone around",
        "the path is empty and far from any occupied building",
        "there is no one within shouting distance along this stretch",
    ),
    "CCTV_GAP": (
        "there is no camera covering this entrance at all",
        "the camera here has been pointing at a wall for months",
        "this whole stretch has no surveillance coverage",
        "there are no cameras where you would expect them",
        "the camera at this corner does not appear to be working",
    ),
    "BLOCKED_ROUTE": (
        "construction material is blocking the whole walkway",
        "the footpath is obstructed and people have to walk on the road",
        "a parked vehicle blocks the pedestrian route completely",
        "the walkway is broken and unsafe to use",
        "debris has been left across the path for days",
    ),
    "ACCESS_CONTROL": (
        "this gate is regularly left open and unattended",
        "the door does not lock and anyone can walk in",
        "the side entrance has been propped open all week",
        "there is no one checking identity cards at this gate",
        "the lock on this door has been broken for a long time",
    ),
    "TRANSPORT_SAFETY": (
        "the bus stop has no lighting and students wait there alone",
        "the last bus leaves before evening classes finish",
        "there is no safe place to wait for transport here",
        "the shuttle pick up point is on an unlit stretch of road",
        "students have to wait on the roadside with no shelter",
    ),
    "OTHER_CONCERN": (
        "there is a safety issue here that is not covered by the options",
        "something about this place concerns me but does not fit a category",
        "I want to flag a condition that is not in the list",
        "there is a problem here I cannot categorise properly",
        "this is a safety concern that does not match the given types",
    ),
}


@dataclass(frozen=True, slots=True)
class Example:
    text: str
    label: str
    provenance: str = "synthetic"
    # Stable identifier, used by the deduplication and leakage checks.
    example_id: str = ""


def generate(
    *,
    examples_per_category: int,
    seed: int,
    cross_category_noise: float = 0.25,
) -> list[Example]:
    """Deterministically generate the corpus.

    The same seed produces byte-identical output, so a run is reproducible from
    the config alone.
    """
    if not 0.0 <= cross_category_noise <= 1.0:
        raise ValueError("cross_category_noise must be between 0 and 1")

    rng = random.Random(seed)
    examples: list[Example] = []

    for code in CATEGORY_CODES:
        cores = TEMPLATES[code]
        others = [c for c in CATEGORY_CODES if c != code]

        for i in range(examples_per_category):
            core = rng.choice(cores)
            parts = [core]

            if rng.random() < 0.7:
                parts.append(rng.choice(SHARED_PLACES))
            if rng.random() < 0.6:
                parts.append(rng.choice(SHARED_TIMES))
            if rng.random() < 0.4:
                parts.append(rng.choice(SHARED_FEELINGS))

            # Borrow a phrase from a different category. This is what makes the
            # classes overlap lexically, as they do in real reports where a
            # student describes context alongside the event itself.
            if rng.random() < cross_category_noise:
                borrowed = rng.choice(TEMPLATES[rng.choice(others)])
                parts.append(f"earlier I also noticed that {borrowed}")

            text = ", ".join(parts).strip()
            text = text[0].upper() + text[1:] + "."
            examples.append(
                Example(text=text, label=code, example_id=f"syn-{code}-{i:04d}")
            )

    rng.shuffle(examples)
    return examples
