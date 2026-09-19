"""
End-to-end tests for load_match_lambda/handler.py. S3 is mocked
(no real bucket involved), but the database connection is real,
pointed at a local Postgres with the actual schema applied, using
plain password auth as a stand-in for IAM auth (see db.py's module
docstring for why this split is possible). This exercises the real
orchestration logic: reading a manifest, branching on its status,
loading and matching, and writing pipeline_runs, against real data
paths, not a fully mocked pipeline.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "load_match_lambda"))

import db  # noqa: E402

DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "ofac_dbtest"
DB_USER = "test_app"
DB_PASSWORD = "testpass123"

FIXTURE_XML = (Path(__file__).parent / "fixtures" / "sample_entity.xml").read_bytes()

S3_EVENT = {
    "Records": [
        {"s3": {"bucket": {"name": "test-bucket"}, "object": {"key": "manifest/2026/09/17/run_1.json"}}}
    ]
}


def _real_test_connection():
    return db.get_connection(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME, use_ssl=False
    )


@pytest.fixture
def clean_db():
    conn = _real_test_connection()
    conn.run("DELETE FROM screening_matches")
    conn.run("DELETE FROM pipeline_runs")
    conn.run("DELETE FROM sdn_aliases")
    conn.run("DELETE FROM sdn_addresses")
    conn.run("DELETE FROM sdn_entities")
    conn.run("DELETE FROM customers")
    conn.close()
    yield


def _fresh_handler_module():
    for mod in ("handler", "db", "matcher", "parser", "daily_seed"):
        sys.modules.pop(mod, None)
    with patch("boto3.client") as mock_boto_client:
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        import handler as handler_module
        return handler_module, mock_s3


def _mock_s3_get_object(bucket_to_key_bytes):
    """Returns a side_effect function for mock_s3.get_object matching
    handler.py's actual call shape: get_object(Bucket=..., Key=...)."""

    def _get_object(Bucket, Key):  # noqa: N803 - matches boto3's actual param casing
        body = bucket_to_key_bytes[(Bucket, Key)]
        mock_body = MagicMock()
        mock_body.read.return_value = body
        return {"Body": mock_body}

    return _get_object


def test_success_path_loads_matches_and_logs(clean_db):
    handler_module, mock_s3 = _fresh_handler_module()

    manifest = {
        "started_at": "2026-09-17T00:00:00+00:00",
        "status": "success",
        "entities_pulled": 1,
        "staged_key": "raw/2026/09/17/entities.xml",
        "error_message": None,
    }
    mock_s3.get_object.side_effect = _mock_s3_get_object(
        {
            ("test-bucket", "manifest/2026/09/17/run_1.json"): json.dumps(manifest).encode(),
            ("test-bucket", "raw/2026/09/17/entities.xml"): FIXTURE_XML,
        }
    )

    with patch.object(handler_module.db, "get_iam_connection", side_effect=_real_test_connection):
        # Seed a customer that should match the fixture's primary name exactly.
        seed_conn = _real_test_connection()
        seed_conn.run("INSERT INTO customers (full_name) VALUES ('KAVE COFFEE S.A.')")
        seed_conn.close()

        result = handler_module.handler(S3_EVENT, None)

    assert result["status"] == "success"
    assert result["entities_upserted"] == 1
    assert result["matches_found"] >= 1

    verify_conn = _real_test_connection()
    entity_count = verify_conn.run("SELECT count(*) FROM sdn_entities")[0][0]
    assert entity_count == 1
    match_count = verify_conn.run("SELECT count(*) FROM screening_matches")[0][0]
    assert match_count >= 1
    run = verify_conn.run("SELECT status, entities_upserted, matches_found FROM pipeline_runs")
    assert len(run) == 1
    assert run[0][0] == "success"
    assert run[0][1] == 1
    verify_conn.close()


def test_extract_failure_logs_without_loading_anything(clean_db):
    handler_module, mock_s3 = _fresh_handler_module()

    manifest = {
        "started_at": "2026-09-17T00:00:00+00:00",
        "status": "failed",
        "entities_pulled": None,
        "staged_key": None,
        "error_message": "Pre-flight /alive check failed: HTTP 404 Not Found",
    }
    mock_s3.get_object.side_effect = _mock_s3_get_object(
        {("test-bucket", "manifest/2026/09/17/run_1.json"): json.dumps(manifest).encode()}
    )

    with patch.object(handler_module.db, "get_iam_connection", side_effect=_real_test_connection):
        result = handler_module.handler(S3_EVENT, None)

    assert result["status"] == "failed"
    assert result["reason"] == "extract_failed"

    verify_conn = _real_test_connection()
    entity_count = verify_conn.run("SELECT count(*) FROM sdn_entities")[0][0]
    assert entity_count == 0  # nothing should have been loaded
    run = verify_conn.run("SELECT status, error_message FROM pipeline_runs")
    assert len(run) == 1
    assert run[0][0] == "failed"
    assert "404" in run[0][1]
    verify_conn.close()


def test_malformed_staged_file_still_logs_a_failed_run(clean_db):
    handler_module, mock_s3 = _fresh_handler_module()

    manifest = {
        "started_at": "2026-09-17T00:00:00+00:00",
        "status": "success",
        "entities_pulled": 1,
        "staged_key": "raw/2026/09/17/entities.xml",
        "error_message": None,
    }
    mock_s3.get_object.side_effect = _mock_s3_get_object(
        {
            ("test-bucket", "manifest/2026/09/17/run_1.json"): json.dumps(manifest).encode(),
            ("test-bucket", "raw/2026/09/17/entities.xml"): b"<not>valid xml",
        }
    )

    with patch.object(handler_module.db, "get_iam_connection", side_effect=_real_test_connection):
        with pytest.raises(Exception):
            handler_module.handler(S3_EVENT, None)

    verify_conn = _real_test_connection()
    run = verify_conn.run("SELECT status FROM pipeline_runs")
    assert len(run) == 1
    assert run[0][0] == "failed"
    verify_conn.close()
