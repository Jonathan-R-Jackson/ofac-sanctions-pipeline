# Data Dictionary: OFAC Sanctions Screening Pipeline

**Status:** Draft
**Last updated:** 2026-09-17

Field-level schema reference, with source-to-target mapping for the three tables loaded from the OFAC SLS REST API. Written before `schema.sql` so normalization decisions get made here, not invented mid-code.

## Key decisions

A few schema questions came directly out of reading the actual `/entities` response structure, and are settled here rather than left implicit:

- **Sanctions programs are multi-valued per entity** (an entity's `sanctionsPrograms` is a list, e.g. an entity can carry both `CUBA` and `SDGT`). Stored as a native Postgres `TEXT[]` array column on `sdn_entities` rather than a separate junction table, since it's a simple lookup value, not an entity needing its own relations.
- **Only the primary (Latin-script) translation of each name is captured.** Entity names can carry multiple `translations` in different scripts; non-Latin transliterations are out of scope for v1, since matching is against Latin-script customer names.
- **`Birthdate` and `Gender` are promoted to fixed columns**, not modeled as a generic features table. The API's `features` list can in principle hold many feature types (nationality, passport, vessel tonnage, etc.); only the two called for in the DRD's data types are captured. Everything else in `features` is dropped on load for v1.
- **`sanctionsTypes` and `legalAuthorities` are not captured.** Present in the API response, not in the DRD's data requirements, so left out rather than stored unused.
- **`sdn_addresses` captures more than country**, added after checking a real production file rather than guessing from the documentation's single worked example. `city` and `address1` are populated on well over half of all address records across roughly 19,400 real entities (17,007 and 14,251 respectively), not edge cases, so `address1`, `address2`, `address3`, `city`, `region`, and `postal_code` are all real columns now, not a deferred v2.

## Resolved against a real production file

- **`remarks` is confirmed absent.** Checked directly against a real entity record: no `remarks`-equivalent element exists on the actual response. The column stays in `sdn_entities` (harmless, always NULL) rather than being removed, in case a future entity type or list surfaces one, but it should not be relied on.
- **`/entities` returns the complete list in one response**, no pagination. A real pull returned all ~19,400 entities in a single call.

## Open items still to verify

- Whether other feature types beyond `Birthdate` and `Gender` are ever the *only* identifying signal for an entity (e.g. `Place of Birth`, confirmed present in real data but out of scope per Key Decisions). Not blocking, just worth knowing if matching quality ever seems off for entities with sparse Birthdate/Gender data.

---

## sdn_entities

One row per SDN entity. Upsert key is `ent_num`, OFAC's own identifier, so a re-run updates in place rather than duplicating.

| Column | Type | Nullable | Source (API field) | Notes |
|---|---|---|---|---|
| `ent_num` | INTEGER | NOT NULL, PK | `entity/@id` | Upsert key |
| `identity_id` | INTEGER | NULL | `generalInfo/identityId` | |
| `entity_type` | TEXT | NOT NULL | `generalInfo/entityType` | Individual, Entity, Vessel, Aircraft |
| `primary_name` | TEXT | NOT NULL | `names[isPrimary=true]/translations[isPrimary=true]/formattedFullName` | |
| `sanctions_programs` | TEXT[] | NOT NULL | `sanctionsPrograms[]/sanctionsProgram` | e.g. `{CUBA,SDGT}`; see Key Decisions |
| `date_of_birth` | DATE | NULL | `features[type=Birthdate]/valueDate` | Some entities only have a year or approximate range; store what's given |
| `gender` | TEXT | NULL | `features[type=Gender]/value` | |
| `remarks` | TEXT | NULL | confirmed absent | Checked against a real entity: no such field exists. Column kept for forward compatibility, always NULL in practice |
| `loaded_at` | TIMESTAMP | NOT NULL, DEFAULT now() | pipeline-generated | |

## sdn_aliases

One row per non-primary name entry (A.K.A., F.K.A., N.K.A.) on an entity.

| Column | Type | Nullable | Source (API field) | Notes |
|---|---|---|---|---|
| `id` | SERIAL | PK | generated | |
| `ent_num` | INTEGER | NOT NULL, FK → `sdn_entities` | | |
| `alias_name` | TEXT | NOT NULL | `names[isPrimary=false]/translations[isPrimary=true]/formattedFullName` | |
| `alias_type` | TEXT | NULL | `names[isPrimary=false]/aliasType` | A.K.A., F.K.A., N.K.A. |
| `is_low_quality` | BOOLEAN | NOT NULL, DEFAULT false | `names[]/isLowQuality` | Not used to filter matches in v1; candidate for down-weighting in v2 |

## sdn_addresses

One row per address entry on an entity.

| Column | Type | Nullable | Source (API field) | Notes |
|---|---|---|---|---|
| `id` | SERIAL | PK | generated | |
| `ent_num` | INTEGER | NOT NULL, FK → `sdn_entities` | | |
| `country` | TEXT | NULL | `addresses[]/country` | |
| `address1` | TEXT | NULL | `addresses[]/translations[isPrimary=true]/addressParts[type=ADDRESS1]/value` | Present on 14,251 of ~19,400 real entities |
| `address2` | TEXT | NULL | same path, `type=ADDRESS2` | |
| `address3` | TEXT | NULL | same path, `type=ADDRESS3` | |
| `city` | TEXT | NULL | same path, `type=CITY` | Present on 17,007 of ~19,400 real entities |
| `region` | TEXT | NULL | same path, `type=REGION` | |
| `postal_code` | TEXT | NULL | same path, `type=POSTAL CODE` | |

## customers

Synthetic test population, generated with Faker and deliberately seeded with true-positive test cases.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | SERIAL | PK | |
| `full_name` | TEXT | NOT NULL | |
| `date_of_birth` | DATE | NULL | Generated but not used in v1 matching (name-only); available for a v2 DOB-assisted match |
| `country` | TEXT | NULL | |
| `is_seeded_positive` | BOOLEAN | NOT NULL, DEFAULT false | Flags rows deliberately inserted as true-positive test cases, so the test plan can query them directly |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT now() | |

## screening_matches

One row per customer-to-entity match above the score threshold.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | SERIAL | PK | |
| `customer_id` | INTEGER | NOT NULL, FK → `customers` | |
| `ent_num` | INTEGER | NOT NULL, FK → `sdn_entities` | |
| `matched_name` | TEXT | NOT NULL | The specific name/alias text that scored the match, since a customer can match more than one alias of the same entity |
| `matched_on` | TEXT | NOT NULL | `primary_name` or `alias` |
| `match_score` | NUMERIC(5,2) | NOT NULL | 0 to 100 |
| `status` | TEXT | NOT NULL, DEFAULT 'pending_review' | pending_review, cleared, flagged |
| `screened_at` | TIMESTAMP | NOT NULL, DEFAULT now() | |

## pipeline_runs

One row per pipeline execution.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | SERIAL | PK | |
| `started_at` | TIMESTAMP | NOT NULL | |
| `completed_at` | TIMESTAMP | NULL | |
| `status` | TEXT | NOT NULL | running, success, failed |
| `entities_pulled` | INTEGER | NULL | |
| `entities_upserted` | INTEGER | NULL | |
| `matches_found` | INTEGER | NULL | |
| `error_message` | TEXT | NULL | |
