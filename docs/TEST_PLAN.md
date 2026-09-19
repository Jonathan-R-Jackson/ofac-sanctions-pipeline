# Test Plan: OFAC Sanctions Screening Pipeline

**Status:** Draft
**Last updated:** 2026-09-15

Turns the DRD's Success Criteria into concrete, executable test cases, and defines the test data before the matcher exists, so the threshold gets set against a standard chosen in advance rather than tuned to whatever the code happens to produce.

## Test data

| Set | Count (min) | Source |
|---|---|---|
| True positive, exact | 5 | Real primary names and aliases, pulled from the live SDN data once it's loaded |
| True positive, misspelled | 5 | The same names, with a one- or two-character edit |
| True negative | 10 | Generic unrelated names, not derived from SDN data |
| Near-miss | 10 | Common-surname overlaps found in the live pull, paired with unrelated first names |

True positives and near-misses are deliberately not hardcoded here. OFAC's own API documentation includes a worked example (entity id `30629`) that looks internally inconsistent, its primary name is a company (`KAVE COFFEE S.A.`) but its name parts read as an individual (`Alaa AL-SAMAHI`), which suggests it's illustrative rather than a clean live record. Rather than build test cases on data I can't verify, real true-positive and near-miss names get pulled from the actual live SDN data once extract is running, and hardcoded into the seeded `customers` rows at that point.

## 1. Extract and load

- **API health check.** `/alive` returns 200 before the run proceeds; the pipeline aborts and logs a failure if it doesn't.
- **Parser smoke test.** Call `/entities/30629` (the worked example from OFAC's own docs) and confirm the parser produces a row without error. This checks that the parser handles the documented response shape, not that the specific field values are correct, given the inconsistency noted above.
- **Multi-valued programs.** After a full pull, spot-check that at least one loaded entity has more than one value in `sanctions_programs`, confirming the array column actually captures multiplicity rather than only ever storing one value.
- **Alias parsing.** Spot-check that entities with non-primary name entries produce matching `sdn_aliases` rows with `alias_type` populated.
- **Idempotency.** Run extract and load twice in a row with no upstream change. Row counts in `sdn_entities`, `sdn_aliases`, and `sdn_addresses` must not change between the two runs.
- **Re-run overwrites edits.** Manually change one loaded field (e.g. `primary_name`), then re-run. The load must overwrite it back to the source value rather than leaving the stale edit in place.

## 2. Matching

- Every true-positive name (exact and misspelled) must appear in `screening_matches` above the threshold.
- Every true-negative name must not appear in `screening_matches` at all.
- The near-miss set is observed, not gated. Its results are what set the threshold (see below), not something expected to pass or fail against a number chosen ahead of time.

## 3. Observability

- Every extract-load-match run produces exactly one `pipeline_runs` row with `status = 'success'` and populated counts.
- A forced failure (bad URL, or a malformed response fed to the parser) produces a `pipeline_runs` row with `status = 'failed'` and a non-null `error_message`. The pipeline must not fail silently, without a logged row.

## Threshold calibration process

1. Run the matcher against the full combined test set (true positives, true negatives, near-misses).
2. Look at the score distribution: true positives should cluster high, true negatives low, and near-misses show where the two risk overlapping.
3. Set the threshold in the gap. If true positives and near-misses overlap with no clean gap, that's a real finding worth documenting as a limitation, not a reason to pick a number that just makes the demo look clean.
4. Record the chosen threshold and the distribution that justified it, and update the DRD's Success Criteria (currently marked TBD) with the result.

## CI/CD (GitHub Actions)

Not everything above is a good fit for running automatically on every commit. Split by what actually belongs in CI:

**Runs in CI, on every push and pull request:**
- Linting.
- Unit tests for name normalization and nested-entity parsing, against a fixed, checked-in sample API response rather than a live call.
- The idempotency and re-run tests from Extract and Load, run against a throwaway Postgres instance spun up as a GitHub Actions service container, not the real RDS instance.

**Stays manual, not run in CI:**
- The `/entities/30629` parser smoke test and anything else that calls the live OFAC API. Hitting a public government API on every commit isn't good citizenship, and it makes CI results depend on OFAC's uptime rather than the code.
- Deriving true-positive and near-miss names from the live SDN pull, and the threshold calibration process. These are one-time (or periodic) analysis steps, not something that should silently re-run and shift on every commit.

**CD, on merge to main:**
- GitHub Actions deploys both the Extract Lambda and the Load/Match Lambda, using a role assumed via OIDC rather than a stored AWS access key. This runs only after CI passes.
- Schema changes to RDS are still applied manually for now; automating that is a plausible later step, not part of this plan.

## Out of scope

- Real customer PII of any kind.
- Performance or load testing at real bank-scale volumes.
- The Consolidated (non-SDN) list or non-OFAC sanctions regimes.
