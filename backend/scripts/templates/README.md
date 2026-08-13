# Campus data import templates

For `scripts/import_campus_locations.py`. Two files, both CSV, both plain
text so they can be edited in a spreadsheet or by hand.

## `zones.csv`

The seven zones CAMPUS_LOCATIONS.md §3 already proposes for this project —
not fabricated, not a placeholder. These carry no coordinates, so there is
nothing to fabricate: a zone is an organisational grouping
(`authority_profile.zone_id` jurisdiction, control-location selection), not
a place. Use this file as-is, or rename/restructure the zones before import
— CAMPUS_LOCATIONS.md itself says "rename freely."

## `locations.csv` — READ THIS BEFORE EDITING

**Both rows in the shipped file are synthetic examples.** `EXAMPLE-STAGED`
and `EXAMPLE-VERIFIED` are not real places, and `EXAMPLE-VERIFIED`'s
coordinate (`0.000000, 0.000000` — Null Island, the Gulf of Guinea) is
deliberately, obviously not on any campus. They exist only to show the two
row shapes the importer accepts:

- **A staged row** (`EXAMPLE-STAGED`): code, name, and whatever descriptive
  fields are already known. No coordinate. `coordinate_status` left blank
  defaults to `required`. This is the correct shape for **every real
  location today** — CAMPUS_LOCATIONS.md records 40 candidate locations and
  zero verified coordinates. Loading the real candidate list as staged rows
  now, ahead of the field survey, is a legitimate and useful use of this
  importer: it gets the names and types into the database where the survey
  can find and promote them one at a time (DATABASE_SETUP.md §8), instead of
  leaving the whole list sitting in a document.
- **A verified row** (`EXAMPLE-VERIFIED`): every column filled in, including
  `coordinate_source` and `coordinate_captured_at`. This is what a row looks
  like *after* someone has stood at the location with a phone and recorded
  its position — see CAMPUS_LOCATIONS.md §7's field survey protocol.

**Delete both example rows before importing anything real.** They are not
meant to be loaded into a working database; if you run the importer against
this file unmodified, you will get two obviously-fake rows and nothing else.

### Exactly what real data this project is missing

Per CAMPUS_LOCATIONS.md, every field below needs a person on the actual
campus with a phone. No amount of desk research can fill it in:

1. **The coordinate survey itself** — all ~40 candidate locations, latitude
   and longitude to 6 decimal places, captured standing at the main
   entrance of each place.
2. **Academic block letters** — only `'F' Block` is publicly named.
3. **Gates** — how many, their names, their hours.
4. **Parking areas** — both two-wheeler and four-wheeler, locations.
5. **Hostel names and the on/off-campus question** — sources conflict on
   whether hostels are entirely on-campus.
6. `has_lighting`, `has_cctv`, and `footfall_band` for each location —
   observed while standing there, not guessed afterward.
7. `dispatch_note` for anything non-obvious — which door, which floor, which
   bridge connects which blocks.

None of this can be filled in from a desk, and none of it should be
invented to make the template look more complete. A staged row with just a
code and a name is more honest, and more useful, than a row with an invented
coordinate that looks precise and is wrong.

### Column reference

| Column | Required | Notes |
|---|---|---|
| `code` | always | Unique. Short, stable, uppercase-with-hyphens by convention (`LKRC-MAIN`). |
| `name` | always | The place's real name, as signage reads it. |
| `location_type` | optional | One of the fixed set in `import_campus_locations.py` (mirrors the database's own CHECK constraint). |
| `zone_code` | optional | Must exist in `zones.csv` or already be in the database. |
| `latitude` / `longitude` | only with `coordinate_status=verified` | Decimal degrees, both or neither. |
| `coordinate_status` | optional, defaults to `required` | `required` (no coordinate yet), `provisional` (a rough guess, not yet a survey point — treated as unsurveyed everywhere in this system), or `verified` (someone stood there). |
| `coordinate_source` | only with `verified` | e.g. `"field survey, GPS, main entrance"`. |
| `coordinate_captured_at` | only with `verified` | ISO 8601 **with a UTC offset** — `2026-08-13T14:30:00+05:30`, not a bare date. |
| `is_indoor` / `has_lighting` / `has_cctv` | optional | `true`/`false`, blank for unknown. |
| `footfall_band` | optional | `high` / `medium` / `low`. |
| `dispatch_note` | optional | The last hundred metres — which door, which floor. |
| `is_active` | optional, defaults to `false` | Only ever `true` alongside `coordinate_status=verified` — the importer refuses anything else. |

### Running it

```bash
# Preview only — the default, and the safe way to check a file
python backend/scripts/import_campus_locations.py \
    --zones backend/scripts/templates/zones.csv \
    --locations path/to/your/real_locations.csv

# Actually write, once the preview looks right
python backend/scripts/import_campus_locations.py \
    --zones backend/scripts/templates/zones.csv \
    --locations path/to/your/real_locations.csv \
    --commit
```
