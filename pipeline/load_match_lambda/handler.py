"""
Load/Match Lambda. Triggered by an S3 ObjectCreated event on the
staging bucket's manifest/ prefix, not raw/, since manifest is the
one file Extract Lambda always writes, on success or failure (see
extract_lambda/handler.py's module docstring). That's what makes
the guarantee below hold even when Extract itself fails.

Runs in a private VPC subnet with no route to the internet (see DRD
System Requirements): it never needs the public internet, only S3
(via a free Gateway Endpoint) and RDS, both reachable privately.

Guarantee this function is built around: exactly one pipeline_runs
row gets written per invocation, whether Extract succeeded, Extract
failed, or this function itself fails partway through load or
match. A run that goes unlogged is worse than a run that failed
and said so, this mirrors the same principle extract_lambda's
handler.py was built around.

Also adds a few new customers each run (see daily_seed.py), gated
by DAILY_SEED_ENABLED, so a live schedule produces fresh matches
day over day instead of screening the same static 30 seeded
customers forever.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import boto3

import daily_seed
import db
import matcher
from parser import parse_entities_xml

s3 = boto3.client("s3")

# Set to "false" to stop adding new customers each run without a
# redeploy, e.g. if this has run for a while and the demo data is
# ample. Additive only either way; see daily_seed.py for why this
# never deletes or regenerates existing customers.
DAILY_SEED_ENABLED = os.environ.get("DAILY_SEED_ENABLED", "true").lower() == "true"


def _read_json(bucket: str, key: str) -> dict:
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)


def _read_bytes(bucket: str, key: str) -> bytes:
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def handler(event, context):
    record = event["Records"][0]
    bucket = record["s3"]["bucket"]["name"]
    manifest_key = record["s3"]["object"]["key"]

    # Fallback start time, used only if the manifest itself can't be
    # read at all; overwritten below with Extract's actual start time
    # as soon as the manifest is available, so pipeline_runs reflects
    # when the run really began, not just when this function noticed.
    started_at = datetime.now(timezone.utc)
    conn = None

    try:
        manifest = _read_json(bucket, manifest_key)
        started_at = datetime.fromisoformat(manifest["started_at"])

        conn = db.get_iam_connection()

        if manifest["status"] == "failed":
            db.write_pipeline_run(
                conn,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                status="failed",
                error_message=f"Extract failed: {manifest.get('error_message')}",
            )
            return {"status": "failed", "reason": "extract_failed"}

        raw = _read_bytes(bucket, manifest["staged_key"])
        entities = parse_entities_xml(raw)

        entities_upserted = db.load_entities(conn, entities)

        if DAILY_SEED_ENABLED:
            new_customers = daily_seed.generate_daily_customers(entities)
            db.insert_customers(conn, new_customers)

        entity_pairs = [(e.ent_num, e.primary_name) for e in entities]
        alias_pairs = [(e.ent_num, a.alias_name) for e in entities for a in e.aliases]
        candidates = matcher.build_candidates(entity_pairs, alias_pairs)

        customer_rows = db.get_customers(conn)
        customers = [matcher.Customer(id=row[0], full_name=row[1]) for row in customer_rows]

        matches = matcher.find_matches(customers, candidates)
        db.write_matches(conn, matches)

        db.write_pipeline_run(
            conn,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            status="success",
            entities_pulled=manifest.get("entities_pulled"),
            entities_upserted=entities_upserted,
            matches_found=len(matches),
        )
        return {"status": "success", "entities_upserted": entities_upserted, "matches_found": len(matches)}

    except Exception as exc:  # noqa: BLE001 - deliberately broad: this
        # function must still log a pipeline_runs row even when the
        # failure is unexpected, not just for the cases anticipated above.
        error_message = f"{type(exc).__name__}: {exc}"
        try:
            if conn is None:
                conn = db.get_iam_connection()
            db.write_pipeline_run(
                conn,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                status="failed",
                error_message=error_message,
            )
        except Exception:
            # Genuinely nothing more this function can do to get a
            # record logged; surfaces as a Lambda invocation error in
            # CloudWatch instead, the same residual gap extract_lambda's
            # handler.py has for a truly catastrophic failure.
            pass
        raise

    finally:
        if conn is not None:
            conn.close()
