"""Contract level: the saga and every error validate against the synced qone-workflow.yaml."""

from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from tests.http_support import LAURA, SECTION, bearer, make_client
from workflow.domain.saga.saga import Reason

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "openapi"
WORKFLOW = yaml.safe_load((CONTRACTS / "qone-workflow.yaml").read_text(encoding="utf-8"))
SHARED = yaml.safe_load((CONTRACTS / "_shared.yaml").read_text(encoding="utf-8"))
BASE = "/api/v1/sagas"


def inline(node, doc):
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            source = SHARED if ref.startswith("./_shared.yaml#") else doc
            target = source
            for part in ref.split("#/")[1].split("/"):
                target = target[part]
            return inline(target, source)
        out = {k: inline(v, doc) for k, v in node.items() if k not in ("nullable", "example")}
        return {"anyOf": [out, {"type": "null"}]} if node.get("nullable") else out
    if isinstance(node, list):
        return [inline(v, doc) for v in node]
    return node


def assert_valid(body, name: str, doc=WORKFLOW):
    schema = inline(doc["components"]["schemas"][name], doc)
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(body))
    assert not errors, [e.message for e in errors]


@pytest.fixture
def world():
    return make_client()


def test_every_path_of_the_contract_is_served(world):
    client, _ = world
    served = set(client.app.openapi()["paths"])
    for path in WORKFLOW["paths"]:
        assert "/api/v1" + path.replace("{id}", "{saga_id}") in served


def test_the_saga_matches_its_schema_in_every_ending(world):
    client, participants = world
    laura = bearer(LAURA, "STUDENT")
    body = {"studentId": LAURA, "sectionId": SECTION, "origin": "UI"}
    assert_valid(body, "StartEnrollStudent")
    assert_valid(client.get(f"{BASE}/health").json(), "Health", SHARED)
    assert_valid(
        client.post(
            f"{BASE}/enroll-student", json=body, headers={**laura, "Idempotency-Key": "contract-saga-1"}
        ).json(),
        "Saga",
    )
    participants.reject["check-tuition"] = Reason.TUITION_UNPAID
    assert_valid(
        client.post(
            f"{BASE}/enroll-student", json=body, headers={**laura, "Idempotency-Key": "contract-saga-2"}
        ).json(),
        "Saga",
    )
    participants.reject.clear()
    participants.fail.update({"commit-seat", "release-seat"})
    failed = client.post(
        f"{BASE}/enroll-student", json=body, headers={**laura, "Idempotency-Key": "contract-saga-3"}
    ).json()
    assert failed["status"] == "FAILED"
    assert_valid(failed, "Saga")


@pytest.mark.parametrize(
    ("path", "auth", "status"),
    [
        ("/not-a-uuid", True, 400),
        ("/00000000-0000-4000-8000-000000000000", True, 404),
        ("/00000000-0000-4000-8000-000000000000", False, 401),
    ],
)
def test_errors_match_the_shared_error_schema(world, path, auth, status):
    client, _ = world
    r = client.get(f"{BASE}{path}", headers=bearer(LAURA, "STUDENT") if auth else {})
    assert r.status_code == status
    assert_valid(r.json(), "Error", SHARED)
