"""
Database layer for the Load/Match Lambda: connects to RDS Postgres
using IAM auth tokens, and upserts parsed SDN entities.

Uses pg8000, a pure-Python Postgres driver, instead of psycopg2.
psycopg2 is a compiled C extension: a `pip install` on a Mac
produces a macOS binary that will not run on Lambda's Linux
environment, exactly the kind of platform mismatch that cost real
debugging time earlier in this project (see the parser's namespace
and memory issues). pg8000 has no compiled extension, so whatever
gets installed locally is exactly what runs in Lambda.

Honesty about what's actually verified here: the SQL and upsert
logic below is tested against a local Postgres running this
project's real schema (see tests/test_db.py), using a plain
password rather than IAM auth, since this environment has no
network path to the real RDS instance and no AWS credential
context to generate a real IAM token. get_iam_connection() itself,
the actual token generation and SSL handshake against the live
instance, is written against documented AWS and pg8000 behavior
but is genuinely untested until the first real deployment. See
DRD Data Security for why IAM auth is used here at all: it
removes the need for any stored database password.
"""
from __future__ import annotations

import os
import ssl

import boto3
import pg8000.native

from matcher import Match
from parser import ParsedEntity

DB_HOST = os.environ.get("DB_HOST", "")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "ofac")
DB_USER = os.environ.get("DB_USER", "load_match_app")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")


def get_connection(
    host: str, port: int, user: str, password: str, database: str, use_ssl: bool = True
) -> pg8000.native.Connection:
    """Low-level connection, deliberately separated from the
    IAM-specific token generation below so the actual SQL logic can
    be tested against any Postgres, real RDS or a local instance
    with a plain password, without needing AWS credentials just to
    run the tests."""
    ssl_context = ssl.create_default_context() if use_ssl else None
    return pg8000.native.Connection(
        user=user, password=password, host=host, port=port, database=database, ssl_context=ssl_context
    )


def get_iam_connection() -> pg8000.native.Connection:
    """Connects using a short-lived IAM auth token instead of a
    stored password. IAM auth requires SSL, not optional. Untested
    from this environment; see module docstring."""
    rds_client = boto3.client("rds", region_name=AWS_REGION)
    token = rds_client.generate_db_auth_token(
        DBHostname=DB_HOST, Port=DB_PORT, DBUsername=DB_USER, Region=AWS_REGION
    )
    return get_connection(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=token, database=DB_NAME, use_ssl=True
    )


def upsert_entity(conn: pg8000.native.Connection, entity: ParsedEntity) -> None:
    """Upserts one entity, then fully replaces its aliases and
    addresses. Aliases and addresses have no stable external key
    from OFAC to upsert against individually (only sdn_entities'
    ent_num is OFAC's own identifier), so each entity's children are
    deleted and reinserted fresh on every load rather than diffed
    row by row. This is correct, it always reflects the current
    pull, and simple. Per-entity performance at the full ~19,400
    scale is the first thing to actually measure once this runs for
    real, not something to guess at and prematurely optimize."""
    conn.run(
        """
        INSERT INTO sdn_entities
            (ent_num, identity_id, entity_type, primary_name, sanctions_programs,
             date_of_birth, gender, remarks, loaded_at)
        VALUES
            (:ent_num, :identity_id, :entity_type, :primary_name, :sanctions_programs,
             :date_of_birth, :gender, :remarks, now())
        ON CONFLICT (ent_num) DO UPDATE SET
            identity_id = EXCLUDED.identity_id,
            entity_type = EXCLUDED.entity_type,
            primary_name = EXCLUDED.primary_name,
            sanctions_programs = EXCLUDED.sanctions_programs,
            date_of_birth = EXCLUDED.date_of_birth,
            gender = EXCLUDED.gender,
            remarks = EXCLUDED.remarks,
            loaded_at = now()
        """,
        ent_num=entity.ent_num,
        identity_id=entity.identity_id,
        entity_type=entity.entity_type,
        primary_name=entity.primary_name,
        sanctions_programs=entity.sanctions_programs,
        date_of_birth=entity.date_of_birth,
        gender=entity.gender,
        remarks=entity.remarks,
    )

    conn.run("DELETE FROM sdn_aliases WHERE ent_num = :ent_num", ent_num=entity.ent_num)
    for alias in entity.aliases:
        conn.run(
            """
            INSERT INTO sdn_aliases (ent_num, alias_name, alias_type, is_low_quality)
            VALUES (:ent_num, :alias_name, :alias_type, :is_low_quality)
            """,
            ent_num=entity.ent_num,
            alias_name=alias.alias_name,
            alias_type=alias.alias_type,
            is_low_quality=alias.is_low_quality,
        )

    conn.run("DELETE FROM sdn_addresses WHERE ent_num = :ent_num", ent_num=entity.ent_num)
    for address in entity.addresses:
        conn.run(
            """
            INSERT INTO sdn_addresses
                (ent_num, country, address1, address2, address3, city, region, postal_code)
            VALUES
                (:ent_num, :country, :address1, :address2, :address3, :city, :region, :postal_code)
            """,
            ent_num=entity.ent_num,
            country=address.country,
            address1=address.address1,
            address2=address.address2,
            address3=address.address3,
            city=address.city,
            region=address.region,
            postal_code=address.postal_code,
        )


def load_entities(conn: pg8000.native.Connection, entities: list[ParsedEntity]) -> int:
    """Loads every entity in a single transaction: either all of it
    lands or none of it does, no partial load left behind by a
    mid-run failure. Returns the count upserted."""
    conn.run("BEGIN")
    try:
        for entity in entities:
            upsert_entity(conn, entity)
        conn.run("COMMIT")
    except Exception:
        conn.run("ROLLBACK")
        raise
    return len(entities)


def get_customers(conn: pg8000.native.Connection) -> list[tuple[int, str]]:
    rows = conn.run("SELECT id, full_name FROM customers")
    return [(r[0], r[1]) for r in rows]


def insert_customers(conn: pg8000.native.Connection, customers: list[tuple[str, bool]]) -> int:
    """Adds new customer rows; never deletes or modifies existing
    ones. customers: (full_name, is_seeded_positive) pairs, matching
    daily_seed.generate_daily_customers's return shape. Returns the
    count inserted."""
    conn.run("BEGIN")
    try:
        for full_name, is_seeded_positive in customers:
            conn.run(
                "INSERT INTO customers (full_name, is_seeded_positive) VALUES (:full_name, :is_seeded_positive)",
                full_name=full_name,
                is_seeded_positive=is_seeded_positive,
            )
        conn.run("COMMIT")
    except Exception:
        conn.run("ROLLBACK")
        raise
    return len(customers)


def write_matches(conn: pg8000.native.Connection, matches: list[Match]) -> None:
    """Inserts one row per match, as a new screening event. Unlike
    sdn_aliases/sdn_addresses, match rows are never deleted or
    replaced on a re-run: each pipeline run is its own screening
    event, and a real compliance system needs every one on record,
    not just the latest. This is exactly why screening_matches has
    ON DELETE RESTRICT (not CASCADE) back to sdn_entities in
    schema.sql, match history has to survive independently of
    whatever the current SDN list looks like."""
    conn.run("BEGIN")
    try:
        for m in matches:
            conn.run(
                """
                INSERT INTO screening_matches (customer_id, ent_num, matched_name, matched_on, match_score)
                VALUES (:customer_id, :ent_num, :matched_name, :matched_on, :match_score)
                """,
                customer_id=m.customer_id,
                ent_num=m.ent_num,
                matched_name=m.matched_name,
                matched_on=m.matched_on,
                match_score=m.match_score,
            )
        conn.run("COMMIT")
    except Exception:
        conn.run("ROLLBACK")
        raise


def write_pipeline_run(
    conn: pg8000.native.Connection,
    started_at,
    completed_at,
    status: str,
    entities_pulled: int | None = None,
    entities_upserted: int | None = None,
    matches_found: int | None = None,
    error_message: str | None = None,
) -> None:
    conn.run(
        """
        INSERT INTO pipeline_runs
            (started_at, completed_at, status, entities_pulled, entities_upserted, matches_found, error_message)
        VALUES
            (:started_at, :completed_at, :status, :entities_pulled, :entities_upserted, :matches_found, :error_message)
        """,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        entities_pulled=entities_pulled,
        entities_upserted=entities_upserted,
        matches_found=matches_found,
        error_message=error_message,
    )
