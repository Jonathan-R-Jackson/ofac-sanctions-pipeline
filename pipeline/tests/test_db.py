"""
Tests db.py against a real local Postgres with schema.sql applied,
using plain password auth as a stand-in for IAM auth (see db.py's
module docstring for why get_connection and get_iam_connection are
split, specifically so this is possible). This matches the Test
Plan's CI section: these are meant to run against a throwaway
Postgres service container in GitHub Actions, not the real RDS
instance.

Connection details are read from environment variables with
defaults matching local dev, so the same test file works in CI
without changes, only the environment differs.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "load_match_lambda"))

from db import get_connection, load_entities  # noqa: E402
from parser import ParsedEntity, parse_entities_xml  # noqa: E402

DB_HOST = os.environ.get("TEST_DB_HOST", "localhost")
DB_PORT = int(os.environ.get("TEST_DB_PORT", "5432"))
DB_NAME = os.environ.get("TEST_DB_NAME", "ofac_dbtest")
DB_USER = os.environ.get("TEST_DB_USER", "test_app")
DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD", "testpass123")

FIXTURE = Path(__file__).parent / "fixtures" / "sample_entity.xml"


@pytest.fixture
def conn():
    connection = get_connection(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME, use_ssl=False
    )
    # Start each test from a clean slate rather than depending on
    # test order or leftover state from a previous run.
    connection.run("DELETE FROM screening_matches")
    connection.run("DELETE FROM sdn_aliases")
    connection.run("DELETE FROM sdn_addresses")
    connection.run("DELETE FROM sdn_entities")
    yield connection
    connection.close()


def _fixture_entities():
    return parse_entities_xml(FIXTURE.read_bytes())


def test_loads_entity_alias_and_address(conn):
    load_entities(conn, _fixture_entities())

    entity = conn.run("SELECT primary_name, sanctions_programs FROM sdn_entities WHERE ent_num = 30629")
    assert entity == [["KAVE COFFEE S.A.", ["CUBA"]]]

    alias = conn.run("SELECT alias_name, alias_type FROM sdn_aliases WHERE ent_num = 30629")
    assert alias == [["AL-SAMAHY, Allaa", "A.K.A."]]

    address = conn.run("SELECT country, city, address1 FROM sdn_addresses WHERE ent_num = 30629")
    assert address == [["Turkey", "Istanbul", "123 Example Street"]]


def test_rerun_updates_in_place_not_duplicate(conn):
    load_entities(conn, _fixture_entities())
    load_entities(conn, _fixture_entities())  # identical re-run

    count = conn.run("SELECT count(*) FROM sdn_entities")[0][0]
    assert count == 1
    alias_count = conn.run("SELECT count(*) FROM sdn_aliases")[0][0]
    assert alias_count == 1


def test_rerun_with_changed_data_replaces_not_appends(conn):
    load_entities(conn, _fixture_entities())

    changed_xml = FIXTURE.read_text().replace("KAVE COFFEE S.A.", "KAVE COFFEE S.A. RENAMED")
    load_entities(conn, parse_entities_xml(changed_xml.encode()))

    name = conn.run("SELECT primary_name FROM sdn_entities WHERE ent_num = 30629")[0][0]
    assert name == "KAVE COFFEE S.A. RENAMED"
    alias_count = conn.run("SELECT count(*) FROM sdn_aliases WHERE ent_num = 30629")[0][0]
    assert alias_count == 1  # not 2, the old alias was replaced, not accumulated


def test_failed_batch_rolls_back_entirely(conn):
    good = ParsedEntity(
        ent_num=99001, identity_id=None, entity_type="Individual", primary_name="Good",
        sanctions_programs=["TEST"], date_of_birth=None, gender=None, remarks=None,
    )
    bad = ParsedEntity(
        ent_num=99002, identity_id=None, entity_type="NotARealType", primary_name="Bad",
        sanctions_programs=["TEST"], date_of_birth=None, gender=None, remarks=None,
    )

    with pytest.raises(Exception):
        load_entities(conn, [good, bad])

    count = conn.run("SELECT count(*) FROM sdn_entities")[0][0]
    assert count == 0  # the valid one must not have landed either
