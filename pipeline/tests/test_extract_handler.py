"""
Tests the Extract Lambda handler's control flow with urllib and boto3
mocked out, since this sandbox can't reach OFAC's API or a real AWS
account. What this proves: the manifest-always-gets-written guarantee
holds across the success path, a failed pre-flight, and an unexpected
exception. What this does NOT prove: that the real HTTP calls or S3
writes work, since those are mocked. That still needs verifying against
the live API and a real bucket.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "extract_lambda"))

FIXTURE_XML = (Path(__file__).parent / "fixtures" / "sample_entity.xml").read_bytes()


def _fresh_handler_module():
    # Re-import fresh each test so the module-level `s3 = boto3.client(...)`
    # picks up the mock rather than a real client from a prior import.
    sys.modules.pop("handler", None)
    with patch("boto3.client") as mock_boto_client:
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        import handler as handler_module
        return handler_module, mock_s3


def test_success_path_stages_data_and_writes_success_manifest():
    handler_module, mock_s3 = _fresh_handler_module()
    with patch.object(handler_module, "_get") as mock_get:
        mock_get.side_effect = [b"", FIXTURE_XML]  # /alive, then /entities
        result = handler_module.handler({}, None)

    assert result["status"] == "success"
    assert result["entities_pulled"] == 1
    assert result["staged_key"] is not None
    put_calls = mock_s3.put_object.call_args_list
    assert len(put_calls) == 2  # staged raw XML + manifest
    assert put_calls[0].kwargs["ContentType"] == "application/xml"
    manifest_body = json.loads(put_calls[1].kwargs["Body"])
    assert manifest_body["status"] == "success"


def test_count_entities_avoids_a_full_parse():
    # Regression test: the first real run against the live API OOM'd
    # because this used to fully parse every entity just to count them.
    # count_entities must give the right number without going through
    # parse_entity() for each one.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "extract_lambda"))
    from parser import count_entities

    assert count_entities(FIXTURE_XML) == 1


def test_preflight_failure_still_writes_a_manifest():
    handler_module, mock_s3 = _fresh_handler_module()
    with patch.object(handler_module, "_preflight_ok", return_value=(False, "HTTP 403 Forbidden on /alive: b''")):
        result = handler_module.handler({}, None)

    assert result["status"] == "failed"
    assert "403" in result["error_message"]
    # Only the manifest should be written, no staged data file
    assert mock_s3.put_object.call_count == 1


def test_malformed_response_is_caught_and_still_writes_a_manifest():
    handler_module, mock_s3 = _fresh_handler_module()
    with patch.object(handler_module, "_get") as mock_get:
        mock_get.side_effect = [b"", b"<not>valid xml"]
        result = handler_module.handler({}, None)

    assert result["status"] == "failed"
    assert result["error_message"] is not None
    assert mock_s3.put_object.call_count == 1  # manifest only, no bad data staged


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"FAIL: {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
