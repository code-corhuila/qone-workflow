"""HTTP level: the Annex E entry checks: 401 without token, 400 per field, 201 on start, 200 on
the retry, the key scoped to its owner and 422 for another body (#55), 404 with the envelope,
the internal detail never in the response, the correlation."""

import pytest

from tests.http_support import LAURA, OTHER_PRIVATE, PATRICIA, SANTIAGO, SECTION, bearer, make_client, mint
from workflow.domain.saga.saga import Reason

BASE = "/api/v1/sagas"
KEY = {"Idempotency-Key": "enroll-6f1d2c3e-9b7a-4e1f-8c2d-0a1b2c3d4e5f"}
BODY = {"studentId": LAURA, "sectionId": SECTION, "origin": "UI"}


@pytest.fixture
def world():
    return make_client()


def laura():
    return bearer(LAURA, "STUDENT", "Laura Gómez")


def test_health_and_the_401_variants(world):
    client, _ = world
    assert client.get(f"{BASE}/health").json()["service"] == "qone-workflow"
    assert client.post(f"{BASE}/enroll-student", json=BODY, headers=KEY).status_code == 401
    for kw in ({"alg": "HS256"}, {"minutes": -5}, {"key": OTHER_PRIVATE}):
        token = mint(LAURA, "STUDENT", **kw)
        assert (
            client.post(
                f"{BASE}/enroll-student", json=BODY, headers={**KEY, "Authorization": f"Bearer {token}"}
            ).status_code
            == 401
        )


def test_start_201_with_location_then_200_and_the_correlation_travels(world):
    client, participants = world
    r = client.post(f"{BASE}/enroll-student", json=BODY, headers={**laura(), **KEY, "X-Correlation-Id": "e2e-saga-1"})
    assert (
        r.status_code == 201
        and r.headers["Location"] == f"{BASE}/{r.json()['id']}"
        and r.headers["X-Correlation-Id"] == "e2e-saga-1"
    )
    body = r.json()
    assert (
        body["status"] == "COMPLETED"
        and body["completedSteps"][-1] == "commit-seat"
        and body["type"] == "enroll-student"
    )
    assert {c[2] for c in participants.calls} == {"e2e-saga-1"}
    again = client.post(f"{BASE}/enroll-student", json=BODY, headers={**laura(), **KEY})
    assert again.status_code == 200 and again.json() == body and len(participants.calls) == 5
    assert client.get(f"{BASE}/{body['id']}", headers=laura()).json() == body


def test_two_students_with_the_same_key_each_get_their_own_saga(world):
    """#55: Santiago sends Laura's Idempotency-Key; he gets a new saga of his own, never Laura's."""
    client, participants = world
    lauras = client.post(f"{BASE}/enroll-student", json=BODY, headers={**laura(), **KEY})
    santiago = bearer(SANTIAGO, "STUDENT", "Santiago Ruiz")
    his = client.post(f"{BASE}/enroll-student", json={**BODY, "studentId": SANTIAGO}, headers={**santiago, **KEY})
    assert lauras.status_code == 201 and his.status_code == 201
    assert his.json()["id"] != lauras.json()["id"] and his.json()["studentId"] == SANTIAGO
    assert LAURA not in his.text and len(participants.calls) == 10


def test_the_same_key_with_another_body_is_422_and_starts_nothing(world):
    client, participants = world
    first = client.post(f"{BASE}/enroll-student", json=BODY, headers={**laura(), **KEY})
    other = {**BODY, "sectionId": "55555555-5555-4555-8555-555555555506"}
    r = client.post(f"{BASE}/enroll-student", json=other, headers={**laura(), **KEY})
    assert first.status_code == 201 and r.status_code == 422
    assert r.json()["error"] == "IDEMPOTENCY_KEY_REUSED" and {"message", "traceId"} <= r.json().keys()
    assert first.json()["id"] not in r.text and len(participants.calls) == 5


def test_a_compensated_saga_shows_the_step_and_the_reason_never_the_internal_detail(world):
    client, participants = world
    participants.reject["reserve-seat"] = Reason.NO_SEATS
    body = client.post(f"{BASE}/enroll-student", json=BODY, headers={**laura(), **KEY}).json()
    assert body["status"] == "COMPENSATED" and body["failedStep"] == "reserve-seat" and body["reason"] == "NO_SEATS"
    participants.reject.clear()
    participants.fail.add("commit-seat")
    body = client.post(
        f"{BASE}/enroll-student", json=BODY, headers={**laura(), "Idempotency-Key": "enroll-second-0001"}
    ).text
    assert "COMPENSATED" in body and "qone-internal-host" not in body and "503" not in body and "detail" not in body


def test_a_participant_down_shows_the_step_and_participant_unavailable_never_the_detail(world):
    """HU-RES-001 (#57): qone-billing-api stopped."""
    client, participants = world
    participants.fail.add("check-tuition")
    r = client.post(f"{BASE}/enroll-student", json=BODY, headers={**laura(), **KEY})
    body = r.json()
    assert r.status_code == 201 and body["status"] == "COMPENSATED"
    assert body["failedStep"] == "check-tuition" and body["reason"] == "PARTICIPANT_UNAVAILABLE"
    assert "qone-internal-host" not in r.text and "503" not in r.text


def test_the_border_names_every_field_and_the_rules_of_the_starter(world):
    client, _ = world
    bad = client.post(
        f"{BASE}/enroll-student", json={"studentId": "x", "sectionId": SECTION, "origin": "WEB"}, headers=laura()
    )
    assert bad.status_code == 400 and [d["field"] for d in bad.json()["details"]] == [
        "Idempotency-Key",
        "studentId",
        "origin",
    ]
    assert (
        client.post(f"{BASE}/enroll-student", json={"sectionId": SECTION}, headers={**laura(), **KEY}).status_code
        == 400
    )
    assert (
        client.post(
            f"{BASE}/enroll-student", json={**BODY, "studentId": SANTIAGO}, headers={**laura(), **KEY}
        ).status_code
        == 403
    )
    r = client.post(
        f"{BASE}/enroll-student",
        json={**BODY, "origin": "AGENT"},
        headers={**bearer("qone-advisor-api", "SERVICE"), **KEY},
    )
    assert r.status_code == 400 and r.json()["details"][0]["field"] == "confirmationId"
    assert (
        client.post(f"{BASE}/enroll-student", json=BODY, headers={**bearer(PATRICIA, "ADMIN"), **KEY}).status_code
        == 403
    )


def test_reading_a_saga(world):
    client, _ = world
    saga = client.post(f"{BASE}/enroll-student", json=BODY, headers={**laura(), **KEY}).json()["id"]
    assert client.get(f"{BASE}/{saga}", headers=bearer(PATRICIA, "ADMIN")).status_code == 200
    assert client.get(f"{BASE}/{saga}", headers=bearer(SANTIAGO, "STUDENT")).status_code == 404
    assert client.get(f"{BASE}/not-a-uuid", headers=laura()).json()["details"] == [
        {"field": "id", "message": "must be a UUID"}
    ]
    r = client.get(f"{BASE}/00000000-0000-4000-8000-000000000000", headers=laura())
    assert r.status_code == 404 and {"error", "message", "traceId"} <= r.json().keys()
