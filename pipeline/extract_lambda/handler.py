"""
Extract Lambda. Not attached to any VPC (see DRD System Requirements),
so it has default internet access to call OFAC's public API.

Always writes exactly one object to the staging bucket per invocation,
whether the pull succeeded or failed. This matters: the Load/Match
Lambda is triggered by an S3 ObjectCreated event on this bucket (not
by its own independent schedule), specifically so that a failure here
still produces a pipeline_runs row instead of the run going unlogged.
See the note in the project chat about why two independent EventBridge
schedules would have been a race condition against S3 write timing.

USER_AGENT is set defensively. It isn't in the API documentation we
were given, but independent write-ups of this same API report that
requests without one get a 403. Confirm this once the API is actually
called for real.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3

from parser import count_entities

API_BASE = "https://sanctionslistservice.ofac.treas.gov"
USER_AGENT = "ofac-sanctions-pipeline/1.0 (portfolio project; contact: set-your-email-here)"
STAGING_BUCKET = os.environ.get("STAGING_BUCKET", "REPLACE_WITH_BUCKET_NAME")
REQUEST_TIMEOUT_SECONDS = 30

s3 = boto3.client("s3")


class OfacApiError(Exception):
    """Raised with the actual status/body/reason, so a failure manifest
    says why, not just that something failed."""


def _get(path: str) -> bytes:
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        # Read the response body before it's gone: OFAC (or whatever's in
        # front of it, e.g. a WAF) may explain the rejection in the body
        # even when the status line alone doesn't.
        body = exc.read()[:500]
        raise OfacApiError(f"HTTP {exc.code} {exc.reason} on {path}: {body!r}") from exc
    except urllib.error.URLError as exc:
        raise OfacApiError(f"URLError on {path}: {exc.reason}") from exc


def _preflight_ok() -> tuple[bool, str | None]:
    try:
        _get("/alive")
        return True, None
    except OfacApiError as exc:
        return False, str(exc)


def _stage_key(started_at: datetime) -> str:
    return f"raw/{started_at.strftime('%Y/%m/%d')}/entities_{started_at.strftime('%Y%m%dT%H%M%SZ')}.xml"


def _manifest_key(started_at: datetime) -> str:
    return f"manifest/{started_at.strftime('%Y/%m/%d')}/run_{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"


def handler(event, context):
    started_at = datetime.now(timezone.utc)
    manifest = {
        "started_at": started_at.isoformat(),
        "status": "failed",
        "entities_pulled": None,
        "staged_key": None,
        "error_message": None,
    }

    try:
        preflight_ok, preflight_error = _preflight_ok()
        if not preflight_ok:
            manifest["error_message"] = f"Pre-flight /alive check failed: {preflight_error}"
        else:
            raw = _get("/entities?list=SDN%20List")

            # Confirms well-formed XML and gets a count for the manifest,
            # without fully parsing every entity into structured objects,
            # that expensive part happens once, in the Load/Match Lambda,
            # not redundantly here too. Doing it twice is what caused an
            # out-of-memory kill on the first real run against this API.
            entity_count = count_entities(raw)

            staged_key = _stage_key(started_at)
            s3.put_object(Bucket=STAGING_BUCKET, Key=staged_key, Body=raw, ContentType="application/xml")

            manifest["status"] = "success"
            manifest["entities_pulled"] = entity_count
            manifest["staged_key"] = staged_key

    except Exception as exc:  # noqa: BLE001 - deliberately broad: this handler
        # must never raise past this point, or no manifest gets written and
        # the run goes unlogged entirely (see module docstring).
        manifest["error_message"] = f"{type(exc).__name__}: {exc}"

    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()

    # The manifest write is intentionally not inside the try/except above:
    # if S3 itself is unreachable, there is genuinely nothing more this
    # function can do to get a record logged, and that failure surfaces
    # as a Lambda invocation error in CloudWatch instead.
    s3.put_object(
        Bucket=STAGING_BUCKET,
        Key=_manifest_key(started_at),
        Body=json.dumps(manifest).encode("utf-8"),
        ContentType="application/json",
    )

    return manifest
