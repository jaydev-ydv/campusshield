# CampusShield — Campus Location Controlled Vocabulary

**Campus:** Presidency University, Rajanukunte, Yelahanka, Bengaluru, Karnataka 560119, India
**Purpose:** Proposed seed data for `core.campus_location` and `core.campus_zone` (see [DATABASE.md](DATABASE.md) §7)
**Status:** Research complete — **no coordinates verified.** Field survey required before this can be seeded.

---

## 0. The headline finding, stated first

**Presidency University does not publish a campus map, a building directory, or any building-level coordinates in any source I could reach.** The official site describes facilities in prose; it does not name most buildings, does not number blocks (one exception, see `F Block`), and gives no geometry.

Consequently:

> ### Every location in this document carries `COORDINATE_REQUIRED`.
> **Zero coordinates are verified. Not one.**

This is the correct outcome given your instruction not to fabricate, and it is not a research failure — it is a fact about what the university publishes. A single campus-centroid coordinate exists in a non-official source (§2) and is recorded **only** for initial map centring. It must never be copied into a location row: doing so would place the library, the hostels, the gates, and the sports ground at the identical point, which would silently destroy hotspot detection, cluster centroids, and every before/after impact measurement that depends on locations being distinguishable.

**What this means for the project:** the location list needs one afternoon of walking the campus with a phone. That is the entire remaining task, and §7 tells you exactly how to do it.

---

## 1. Source hierarchy used

| Tier | Meaning | Used for |
|---|---|---|
| **OFFICIAL** | `presidencyuniversity.in` or `library.presidencyuniversity.in` | Name and existence treated as authoritative |
| **SECONDARY** | Wikipedia, Shiksha, CollegeDunia, GetMyUni and similar aggregators | Existence plausible; **names are not authoritative** and are frequently paraphrased by the aggregator rather than quoted |
| **UNVERIFIED** | No source found | Listed only as a survey slot with no name and no coordinate |

Names in the tables below that came from OFFICIAL sources are quoted as the university writes them, including its own inconsistent capitalisation (`'F' Block`, `Library Annexe` vs `Law Library Annex`).

### Conflicts found in the sources — resolve during the survey

| Item | Conflict | Note |
|---|---|---|
| Campus area | 64 acres / 65–67 acres / "nearly hundred acres" / 100 acres / 110 acres | The official campus-facilities page says "nearly hundred acres"; Wikipedia's infobox says 110 acres. Not load-bearing for this project. |
| PIN code | 560119 (official contact page) vs 560089 (Mappls listing) | **Use 560119** — it is on the university's own contact page and matches your brief. |
| Village name | "Itgalpura" / "Ittagalpura" / "Dibbur" | Transliteration variants of the same revenue villages. |
| Hostel location | Official hostel page says hostels are **on campus**; official transport page describes shuttles for hostels "within a radius of two kilometres" | Possibly on-campus hostels *plus* off-campus leased accommodation. **Must be resolved** — it determines whether hostel reports are in-jurisdiction. |
| Central library block | A secondary source places the central library in a "Management Block"; no official source names any such block | Do not seed "Management Block" until confirmed on site. |

---

## 2. Campus reference point — NOT a location

| Field | Value |
|---|---|
| Reference | `CAMPUS-REF-000` |
| Latitude | `13.1682` |
| Longitude | `77.5354` |
| coordinate_source | Wikipedia infobox — **SECONDARY, not official** |
| confidence | **LOW** for anything finer than "the campus is here" |
| Permitted use | Initial Leaflet map centre and default zoom only |
| **Prohibited use** | **Must not be written into any `campus_location` row** |

Recorded so that nobody later needs to guess a map centre, and flagged so that nobody is tempted to fill the coordinate gap with it.

---

## 3. Proposed zones

Zones are **my proposal**, not university nomenclature — the university publishes no zoning scheme. They exist to support `authority_profile.zone_id` jurisdiction and control-location selection for impact measurement (DATABASE.md §15). Rename freely.

| zone_id | code | name | Notes |
|---|---|---|---|
| 1 | `Z-ACAD` | Academic Zone | Teaching blocks, labs, libraries |
| 2 | `Z-RESI` | Residential Zone | Hostels and hostel amenities |
| 3 | `Z-SPRT` | Sports & Recreation Zone | Indoor and outdoor sports |
| 4 | `Z-DINE` | Dining & Amenities Zone | Cafeterias, kiosks, ATM, reprography |
| 5 | `Z-ADMN` | Administration & Support Zone | Offices, medical, counselling |
| 6 | `Z-TRAN` | Perimeter & Transit Zone | Gates, parking, bus bays |
| 7 | `Z-CIRC` | Circulation Zone | Bridges, walkways, connectors |

`Z-CIRC` deserves comment. The official campus-facilities page states the campus is *"interconnected with steel bridges"* between blocks. Elevated connectors are a genuinely distinct safety geometry — enclosed, often unstaffed, with limited exit options and poor sightlines — and they are exactly the kind of place a community reporting tool exists to surface. They are also invisible to any location list that only records buildings. This is the one structural insight the official sources gave up, and it is worth building the vocabulary around.

---

## 4. TIER 1 — Officially named locations

Existence and name confirmed on `presidencyuniversity.in` or the official library subdomain. **All require coordinate capture.**

| id | code | Official name | Type | Zone | Safety relevance | Lat | Lng | coordinate_source | Name confidence |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `LKRC-MAIN` | Library & Knowledge Resource Centre (LKRC) | library | Z-ACAD | Open to 18:30 (16:30 on 1st/3rd Sat); students leave after dark in winter | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL — library subdomain | HIGH |
| 2 | `LKRC-ANNEXE` | Library Annexe | library | Z-ACAD | Separate structure (est. 2018) implying a walk between buildings | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 3 | `LKRC-LAW` | Law Library | library | Z-ACAD | Est. 2021; long individual study sessions | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 4 | `LKRC-LAW-ANX` | Law Library Annex | library | Z-ACAD | Inaugurated 2025; newest structure, lighting/signage may lag | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 5 | `BLK-F` | 'F' Block | academic | Z-ACAD | **The only block the university names publicly.** Houses recreation centres with photocopying/printing — sustained non-class footfall | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 6 | `AUDI-650` | Auditorium (650-seat) | assembly | Z-ACAD | Large evening events; concentrated crowd dispersal after dark | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 7 | `AMPHI` | Amphitheatre | open_assembly | Z-SPRT | Open-air, used for evening cultural events; lighting outside event hours uncertain | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 8 | `MOOT-CT` | Moot Court | academic | Z-ACAD | Law school facility with out-of-hours practice sessions | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 9 | `LAB-SOD` | SOD Labs (School of Design) | laboratory | Z-ACAD | Studio-type work runs late; design labs commonly have extended access | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 10 | `LAB-MEDIA` | Media Labs | laboratory | Z-ACAD | Equipment-bearing rooms; students move alone carrying kit | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 11 | `CTR-EXP` | Experience Centers | academic | Z-ACAD | Officially named; function not described — **confirm what these are** | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | MEDIUM |
| 12 | `CAFE-MAIN` | Cafeteria | dining | Z-DINE | Highest-density mixed-gender space on campus; peak crowding at lunch | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 13 | `ATM-FED` | Federal Bank Limited ATM lobby | amenity | Z-DINE | Enclosed, single-occupancy, cash-handling — a recognised risk geometry | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 14 | `REPRO` | Reprography Center | amenity | Z-DINE | Queueing and waiting space | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 15 | `MED-CTR` | Medical Center (operated by Manipal Hospitals) | health | Z-ADMN | 10 inpatient beds; nurse on duty **only until 21:00** — the gap after that is operationally significant | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 16 | `COUNSEL` | Counselling psychologist facility | support | Z-ADMN | Students attend alone and may not want to be seen entering — **privacy-sensitive; consider whether it belongs on a public map at all** | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | MEDIUM |
| 17 | `HOSTEL-G1` | Girls' Hostel — building 1 | hostel | Z-RESI | Four wardens + resident officer; 24h security and CCTV at entry points (official) | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL (existence) | MEDIUM — **university publishes no hostel names** |
| 18 | `HOSTEL-G2` | Girls' Hostel — building 2 | hostel | Z-RESI | As above | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL (existence) | LOW — count per gender unconfirmed |
| 19 | `HOSTEL-B1` | Boys' Hostel — building 1 | hostel | Z-RESI | Two wardens per hostel (official) | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL (existence) | LOW — count per gender unconfirmed |
| 20 | `HOSTEL-NEW` | New boys' hostel (four wings, 1,700 students) | hostel | Z-RESI | Under construction "near campus" — **active construction site**, and construction perimeters are a recurring safety concern in their own right | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | MEDIUM |
| 21 | `HOSTEL-DINE` | Hostel dining areas | dining | Z-RESI | Four meal services daily; the late service creates after-dark movement within the residential zone | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | MEDIUM — per-hostel instances unenumerated |
| 22 | `HOSTEL-COMMON` | Hostel common rooms (TV / indoor games) | recreation | Z-RESI | Evening congregation space | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | MEDIUM |
| 23 | `HOSTEL-LAUNDRY` | Hostel laundry rooms | amenity | Z-RESI | Low-traffic service room, often basement or rear-of-building | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | MEDIUM |
| 24 | `SPORT-INDOOR` | Multi-sports indoor complex | sports | Z-SPRT | Officially described as "premium court & arenas"; evening use | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 25 | `SPORT-OUTDOOR` | Outdoor sports ground | sports | Z-SPRT | "Expansive ground"; large unlit perimeter after sunset | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH — but see §5, sub-facilities unenumerated |
| 26 | `GYM` | Gymnasium | sports | Z-SPRT | Early-morning and late-evening use, low staffing at both ends | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 27 | `SPORT-CHANGE` | Changing rooms / athlete lounge | sports_support | Z-SPRT | **Changing rooms are among the highest-sensitivity spaces on any campus.** Officially listed as a "support facility" | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH |
| 28 | `BRIDGE-STEEL` | Steel bridges (inter-block connectors) | circulation | Z-CIRC | **See §3.** Elevated, enclosed, typically unstaffed, limited exits, poor sightlines. Individual bridges must be enumerated on site | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | OFFICIAL | HIGH (category) / LOW (instances) |

**Tier 1 count: 28 locations, 0 coordinates.**

### Tier 1 categories confirmed but not individually enumerable

Officially confirmed to exist in the plural, with no source distinguishing the instances. Each becomes several rows after the survey — do **not** seed them as single rows.

| Category | Official description | Survey action |
|---|---|---|
| Computer Labs | Named on the official infrastructure page | Enumerate per block and room |
| Laboratories (engineering) | "well-planned, with dedicated spaces for research-centric activities" | Enumerate the ones with out-of-hours access |
| Classrooms | "large, well-lit, and well-ventilated"; 250+ per secondary sources | Do **not** enumerate individually — too granular for reporting; use the parent block |
| Seminar Halls | Capacities 120–360 seats | Enumerate; evening events matter |
| Food courts / kiosks | "Multiple locations offering various cuisines" | Enumerate — these are dispersed and each is a distinct place |
| Academic blocks | Only `'F' Block` is publicly named | **Highest-value survey task**: get the real block letters |

---

## 5. TIER 2 — Reported by secondary sources only

Plausible and probably real, but **no official source names them**. Confirm existence during the survey before seeding; drop anything that turns out not to exist.

| id | code | Reported name | Type | Zone | Safety relevance | Lat | Lng | coordinate_source | Confidence |
|---|---|---|---|---|---|---|---|---|---|
| 29 | `GRND-CRICKET` | Cricket ground | sports | Z-SPRT | Large open area, distant perimeter | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | SECONDARY | MEDIUM |
| 30 | `GRND-FOOTBALL` | Football ground | sports | Z-SPRT | As above | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | SECONDARY | MEDIUM |
| 31 | `CT-BASKET` | Basketball court | sports | Z-SPRT | Evening play under variable lighting | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | SECONDARY | MEDIUM |
| 32 | `CT-VOLLEY` | Volleyball court | sports | Z-SPRT | As above | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | SECONDARY | MEDIUM |
| 33 | `CT-TENNIS` | Tennis court | sports | Z-SPRT | Reported by one aggregator only | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | SECONDARY | LOW |
| 34 | `TRACK-ATH` | Athletics track | sports | Z-SPRT | Early-morning solo use | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | SECONDARY | LOW |
| 35 | `POOL` | Swimming pool | sports | Z-SPRT | **Conflicting evidence** — the official sports page does not mention a pool, one aggregator does. High-sensitivity facility if real; verify before seeding | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | SECONDARY | LOW |
| 36 | `CT-BADMINTON` | Indoor badminton courts | sports | Z-SPRT | Likely inside `SPORT-INDOOR`; may not need its own row | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | SECONDARY | LOW |
| 37 | `TT-ROOM` | Table tennis room | sports | Z-SPRT | Officially "table tennis" is listed as a sport; the room is not | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | SECONDARY | LOW |
| 38 | `CAFE-2` | Second cafeteria | dining | Z-DINE | "Two sizable cafeterias" per aggregators; official page says "Cafeteria" singular | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | SECONDARY | MEDIUM |
| 39 | `CANTEEN-MAIN` | Main canteen / food court (≈550 seats) | dining | Z-DINE | May be the same place as `CAFE-MAIN` under a different aggregator's wording — **check for duplication** | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | SECONDARY | LOW |
| 40 | `BLK-MGMT` | "Management Block" | academic | Z-ACAD | One aggregator places the central library here. **Do not seed until confirmed** — a wrong block name propagates into every report | `COORDINATE_REQUIRED` | `COORDINATE_REQUIRED` | SECONDARY | LOW |

**Tier 2 count: 12 candidates, 0 coordinates, none confirmed.**

---

## 6. TIER 3 — Required categories with NO verifiable source

You asked specifically for gates, parking, and isolated pathways. **I could not verify a single one from any source, official or secondary.** The university publishes nothing about its entrances, its parking, or its internal path network.

These are therefore listed as **survey slots with no names and no coordinates**. The names below are placeholders describing what to look for — they are *not* claims that a place with that name exists.

| Slot | Category | Why it matters | Status |
|---|---|---|---|
| `GATE-?` × n | Campus entrance / gate | The primary boundary between campus and the Yelahanka–Doddaballapur road. Entry/exit points are where a safety tool sees its first and last signal of the day | **Count and names unknown.** Every campus has at least one; I will not assert how many or what they are called |
| `SECURITY-?` | Security post / gatehouse | Dispatch origin for `emergency_dispatch` | Unverified |
| `PARK-4W` | Four-wheeler parking | Large, low-surveillance, poorly lit after hours — a standard high-risk area | Unverified; existence near-certain, location unknown |
| `PARK-2W` | Two-wheeler parking | Dense, screened sightlines between rows | Unverified |
| `BUSBAY` | Bus bay / boarding point | 60+ bus fleet; transport runs **06:00–19:00**, so the last departure is around dusk in winter. Concentrated crowding at a fixed time daily | Unverified location; **service officially confirmed** |
| `SHUTTLE-RJK` | Rajanukunte shuttle pickup | Officially "the nearest point connecting the main road". **Off-campus** — decide whether it is in jurisdiction | Service officially confirmed; point location unknown |
| `ADMIN-?` | Administrative block / Registrar | Where formal complaints are physically filed | Unverified |
| `ICC-OFFICE` | Internal Complaints Committee office | The ICC exists and has an official channel (§9). Its **physical location is not published** | Unverified — ask the ICC directly |
| *(none)* | **Isolated pathways** | You asked for these "ONLY if actually identifiable from reliable campus information." **They are not.** Nothing published identifies a single internal path | **Deliberately empty** |

### On isolated pathways specifically

This is the category most worth having and the one I can least honestly fill. The motivation section of your project deck singles out *"poor lighting, isolated paths"* as the environmental hazards early detection is meant to surface — so leaving the category empty is a real gap, not a tidy one.

But inventing path names would be worse than leaving it empty, because a fabricated location that students cannot find teaches them the tool does not know their campus, and they stop using it. The right way to fill this category is not research — it is the survey in §7 plus the system's own reporting: `location_hint` on `core.report` exists precisely so a student can write *"the path behind D block"* against a nearby controlled location, and repeated hints at one location are the signal that a new location row is needed. **The vocabulary is designed to grow from use.** Seed it with what is real, and let the first month of reports tell you what you missed.

---

## 7. Field survey protocol

This is the whole remaining task. It needs one person, one phone, roughly three hours.

**Capture method.** Open Google Maps at each location, long-press your exact position, and read the decimal coordinates from the card. Record **6 decimal places** (~0.1 m precision — far finer than needed, but truncation later is free and re-surveying is not). Do not use the map's search result for the building name; stand at the place. GPS is least accurate indoors and beside tall structures, so capture at the **main entrance, outdoors**, which is also the right anchor for a reporting tool: people report where they were, not which room.

**What to record per location:** code, the university's own name for it (read the signage, don't paraphrase), latitude, longitude, whether it is indoors, whether the approach is lit after dark, whether CCTV is visible, and rough footfall band. Those last three feed `has_lighting`, `has_cctv`, and `footfall_band` in the schema, which are risk-scoring inputs — capture them while you are standing there, not from memory.

**Survey order, highest value first:**
1. **Academic block letters.** `'F' Block` implies at least A–F. Getting the real letters converts a vague list into a vocabulary students recognise. Highest single-item value in this document.
2. **Gates.** How many, what they are called, which are open when.
3. **Parking areas.** Both types.
4. **The steel bridges.** Which blocks each connects — these are named nowhere and matter disproportionately.
5. **Hostels.** Real names, which are girls' and which boys', and **whether any are off-campus** (§1 conflict).
6. Everything else in Tiers 1 and 2.

**Two cautions.** Photograph signage rather than trusting recall — official names and colloquial names diverge, and the schema needs the official one with the colloquial one as a fallback the UI can search. And confirm with campus security before mapping hostel interiors or perimeter security posts; a student walking a girls' hostel perimeter recording GPS points is exactly the behaviour the security staff are there to stop.

---

## 8. Schema mapping

Row shape for `core.campus_location` (DATABASE.md §7):

| This document | Schema column |
|---|---|
| `id` | `location_id` |
| `code` | `code` |
| Official name | `name` |
| Type | `location_type` |
| Zone | `zone_id` → `core.campus_zone` |
| Lat / Lng | `latitude` / `longitude` — **`NOT NULL`, so no row can be seeded until surveyed** |
| — | `is_indoor`, `has_lighting`, `has_cctv`, `footfall_band` — capture during survey |
| — | `dispatch_note` — access guidance for responders; capture for anything non-obvious |
| — | `is_active` — `true` for all seeds |

The `NOT NULL` on coordinates is doing real work here: it makes it impossible to seed a placeholder location and forget to fix it later. Nothing enters the vocabulary un-surveyed.

**Safety-relevance note for `location_type`:** the `dispatch_note` column matters most for `BRIDGE-STEEL`, `PARK-*`, and the hostels — places where "go to the library" is adequate direction and "go to the third bridge" is not.

---

## 9. Non-location finding worth keeping

Presidency University's **Internal Complaints Committee** is real, is contactable, and has named officers: complaints go to **`puicc@presidencyuniversity.in`**, with a Chairperson (Professor & Associate Dean – Student Affairs) and a Member Secretary (Assistant Professor, School of Law). The university also lists a Grievance and Redressal Committee, a Gender Sensitization Cell, a Caste Based Discrimination Cell, and an Equal Opportunity Cell.

This matters for two reasons. It confirms the `icc` role in the schema maps to a body that actually exists here rather than a generic placeholder — which strengthens the framing in your deck that CampusShield *supports existing institutional mechanisms* rather than replacing them. And if you ever want the prototype reviewed by the people it is modelled on, that address is where to ask.

---

# A. Verified locations

**Verified to exist and be officially named: 28 (Tier 1).**
**Verified coordinates: 0.**

The two are independent, and only the first can be established from a desk. The 28 in §4 are safe to treat as real places with real names; none can be seeded until surveyed, because `latitude`/`longitude` are `NOT NULL`.

Officially named and highest-confidence: `LKRC-MAIN`, `LKRC-ANNEXE`, `LKRC-LAW`, `LKRC-LAW-ANX`, `BLK-F`, `AUDI-650`, `AMPHI`, `MOOT-CT`, `LAB-SOD`, `LAB-MEDIA`, `CAFE-MAIN`, `ATM-FED`, `REPRO`, `MED-CTR`, `SPORT-INDOOR`, `SPORT-OUTDOOR`, `GYM`, `SPORT-CHANGE`, `BRIDGE-STEEL`.

# B. Locations requiring coordinate verification

**All of them — 40 rows (28 Tier 1 + 12 Tier 2) — plus 9 Tier 3 survey slots that need a name before they need a coordinate.**

Priority for the survey, by safety value against effort:

| Priority | Items | Why first |
|---|---|---|
| 1 | Gates, parking, security post | Entirely unknown, universally present, high safety relevance |
| 2 | Academic block letters | Converts the vocabulary into something students recognise |
| 3 | Hostels + the on/off-campus question | Determines jurisdiction; conflicting sources |
| 4 | Steel bridges | Distinctive to this campus, named nowhere, high relevance |
| 5 | Tier 1 remainder | Names already known; only coordinates missing |
| 6 | Tier 2 | Confirm existence first; drop what is not real |

# C. Sources used

**Official — `presidencyuniversity.in`**
- [Infrastructure](https://presidencyuniversity.in/infrastructure/) — computer labs, library, media labs, experience centers, moot court, SOD labs
- [Campus Facilities](https://presidencyuniversity.in/student-life/campus-facilities) — `'F' Block`, 650-seat auditorium, amphitheatre, cafeteria, food courts, Federal Bank ATM lobby, reprography centre, seminar halls 120–360, **steel bridges**, "nearly hundred acres"
- [Student Hostel](https://presidencyuniversity.in/student-life/student-hostel-facilities) — three hostels, wardens, dining, laundry, common rooms, 24h security + CCTV, new 1,700-student boys' hostel
- [Student Wellness](https://presidencyuniversity.in/student-life/health-facilities) — Medical Center (Manipal Hospitals), 10 beds, nurse to 21:00, 24h ambulance, counselling psychologist
- [Sports & Athletics](https://presidencyuniversity.in/student-life/sports-athletics) — indoor complex, outdoor ground, gymnasium, changing rooms/athlete lounge
- [Transportation](https://presidencyuniversity.in/student-life/transport-facilities) — AC fleet, 06:00–19:00 Mon–Sat, Rajanukunte shuttle, 2 km hostel shuttle radius
- [Contact](https://presidencyuniversity.in/contact) — official address and PIN 560119
- [Departments](https://presidencyuniversity.in/departments) — 11 schools, 10 departments
- [Library & Knowledge Resource Centre](https://library.presidencyuniversity.in/) — LKRC official name, Law Library (2021), Library Annexe (2018), Law Library Annex (2025), hours
- [Internal Complaints Committee](https://presidencyuniversity.in/about-us/committees/internal-complaints-committee) and [Grievance and Redressal Committee](https://presidencyuniversity.in/about-us/committees/grievance-and-redressal-committee)

**Secondary — existence only, names not authoritative**
- [Wikipedia — Presidency University, Bengaluru](https://en.wikipedia.org/wiki/Presidency_University,_Bengaluru) — campus reference coordinate, 110 acres, Itgalpura, 500-seater amphitheatre, 60+ buses
- [Shiksha](https://www.shiksha.com/university/presidency-university-bangalore-47151/infrastructure), [GetMyUni](https://www.getmyuni.com/college/presidency-university-bangalore-facilities), [CollegeBatch](https://www.collegebatch.com/12074-presidency-university-campus-tour-bangalore), [CollegeAndFees](https://collegeandfees.com/colleges/presidency-university-bangalore/infrastructure), [UniversityKart](https://universitykart.com/university/universitydetails/presidency-university-bangalore/facilities) — sports sub-facilities, "Management Block", cafeteria counts, canteen capacity

**Sources searched that yielded nothing usable:** no campus map PDF, no building directory, no NAAC/IQAC infrastructure annexure, and no geo-referenced campus data were reachable. Mappls carries a listing with a conflicting PIN (560089) and no building detail.

# D. What you need to provide

**Blocking — nothing can be seeded without this:**

1. **A coordinate survey** (§7). All 40 rows. One person, one phone, ~3 hours. This is the only hard blocker.
2. **Academic block letters.** `'F' Block` is the only one published; A–E and beyond are unknown. Highest-value single item.
3. **Gates** — how many, official names, hours.
4. **Parking areas** — both types, locations.
5. **Hostel names, gender assignment, and the on/off-campus question** (§1 conflict). Determines jurisdiction for a large share of reports.

**Non-blocking but worth resolving:**

6. Which bridges connect which blocks.
7. Whether the swimming pool and tennis court exist (Tier 2, official sources silent).
8. Whether `CAFE-MAIN` and `CANTEEN-MAIN` are the same place.
9. Whether "Management Block" is a real name.
10. Where the ICC office physically is.
11. **A judgement call for you:** should the counselling facility (`COUNSEL`) appear on the public safety map at all? Being seen entering it is precisely what deters students from using it, and a map pin is a small permanent arrow pointing at the door. My recommendation is to keep it in the vocabulary — reports *about* that area still matter — but exclude it from the public map layer, the same way `requires_confidentiality` categories are handled in DATABASE.md.

**Fastest route to items 2–5 and 10:** the university's admissions or student-affairs office likely has a campus map that simply is not published online. One email to `info@presidencyuniversity.in`, or a walk to the front desk, could replace the entire survey — worth ten minutes before spending three hours.

---

# E. Loading the survey once it exists (Phase 5)

`backend/scripts/import_campus_locations.py` is the mechanism this document's
§7 asked for: a validated, repeatable loader for whatever the field survey
produces. Point it at a CSV shaped like `backend/scripts/templates/README.md`
describes and it will insert new zone and location rows, refusing loudly on
anything that violates the coordinate/verification invariants this document
and `DATABASE.md` §7 already establish. It does not change the finding
above — **zero coordinates are still verified** — and running it against an
empty or unmodified template changes nothing real. It exists so that the day
the survey happens, getting it into the database is a validated command, not
forty individual `INSERT` statements typed by hand.

---

# F. Making the map usable before the survey exists (Phase 5B)

The map, the location picker, and the responder incident view are all real
and all tested — inspection confirmed this repeatedly across Phase 4B-2,
Phase 5, and Phase 5B. What was missing was never the *code*; it was
something to show on it. `backend/scripts/seed_demo_campus_locations.py`
loads five clearly-fake locations — `DEMO — Sample Library (NOT A REAL
LOCATION)` and similarly for a hostel, cafeteria, gate, and sports ground —
at coordinates near `(0, 0)`, nowhere close to Bengaluru. Every row it
writes is flagged `is_synthetic = true` at the database level
(`DATABASE.md` §30), a flag the frontend renders as a visible "DEMO" badge
everywhere that location appears — the map marker, its tooltip, the
responder's incident detail, the navigation button.

This does not change §A/§B above: **verified coordinates for the real
campus remain zero.** The demo fixtures are not a step toward seeding real
data — they are a parallel, permanently-separate track that exists only so
a development or demonstration environment has a working map to show. The
schema itself refuses to let the two be confused
(`ck_campus_location_synthetic_is_labelled`): a synthetic row must carry a
`coordinate_source` starting with `'DEMO FIXTURE:'`, and the real-data
importer in §E has no code path capable of setting the flag at all.

---

*End of document. No SQL, schema changes, or application code produced beyond the Phase 5 importer noted in §E and the Phase 5B demo seeder noted in §F — neither of which reads or invents a real coordinate. `DATABASE.md` unmodified in its schema meaning; see its §29 and §30 for the Phase 5 and 5B revision notes.*
