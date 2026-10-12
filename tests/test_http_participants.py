"""The HTTP participants against a mock transport: the paths, the service token, the step key, the
correlation, the mapping of domain answers to Rejected, and the bounded retries with jitter."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest

from workflow.adapter.outbound.participants.http import HttpBilling, HttpCatalog, HttpClient, HttpEnrollment
from workflow.application.port.outbound.participants import Call, Rejected, Unavailable
from workflow.domain.saga.saga import Origin, Reason, Saga, Step

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
CALL = Call("saga-1:reserve-seat", "corr-1")


def saga():
    s = Saga.start(uuid4(), uuid4(), uuid4(), Origin.UI, None, "corr-1", NOW)
    s = (
        s.step_done(Step.CHECK_TUITION, NOW)
        .step_done(Step.CHECK_ELIGIBILITY, NOW)
        .step_done(Step.RESERVE_SEAT, NOW, reservation_id=uuid4())
    )
    return s


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def client(recorder, attempts=3):
    sleeps: list[float] = []
    http = HttpClient(
        "svc-token", httpx.Client(transport=httpx.MockTransport(recorder)), attempts=attempts, sleep=sleeps.append
    )
    return http, sleeps


def test_the_tuition_gate():
    for status, body, rejected in [
        (200, {"status": "PAID"}, False),
        (200, {"status": "ISSUED"}, True),
        (404, {"error": "NOT_FOUND"}, True),
    ]:
        rec = Recorder(httpx.Response(status, json=body))
        http, _ = client(rec)
        student = uuid4()
        if rejected:
            with pytest.raises(Rejected) as caught:
                HttpBilling(http, "http://billing:8000").ensure_tuition_paid(student, CALL)
            assert caught.value.reason is Reason.TUITION_UNPAID
        else:
            HttpBilling(http, "http://billing:8000").ensure_tuition_paid(student, CALL)
        request = rec.requests[0]
        assert request.url.path == f"/api/v1/billing/students/{student}/invoices/current"
        assert (
            request.headers["Authorization"] == "Bearer svc-token" and request.headers["X-Correlation-Id"] == "corr-1"
        )
        assert "Idempotency-Key" not in request.headers


def test_eligibility_and_the_enrollment_record():
    rec = Recorder(
        httpx.Response(200, json={"eligible": False, "reason": "SCHEDULE_CONFLICT"}),
        httpx.Response(201, json={"id": "66666666-6666-4666-8666-666666666699"}),
        httpx.Response(
            422,
            json={
                "error": "BUSINESS_RULE_VIOLATION",
                "message": "INV-ENR-001: the student already has an active enrollment",
            },
        ),
    )
    http, _ = client(rec)
    enrollment, s = HttpEnrollment(http, "http://enrollment:8000/"), saga()
    with pytest.raises(Rejected) as caught:
        enrollment.ensure_eligible(s.student_id, s.section_id, CALL)
    assert caught.value.reason is Reason.SCHEDULE_CONFLICT
    assert enrollment.record_confirmed(s, CALL) == UUID("66666666-6666-4666-8666-666666666699")
    sent = rec.requests[1]
    assert sent.headers[
        "Idempotency-Key"
    ] == "saga-1:reserve-seat" and b'"outcome":"CONFIRMED"' in sent.content.replace(b" ", b"")
    with pytest.raises(Rejected) as caught:
        enrollment.record_confirmed(s, CALL)
    assert caught.value.reason is Reason.DUPLICATE


def test_the_catalog_steps():
    rec = Recorder(
        httpx.Response(201, json={"id": "77777777-7777-4777-8777-777777777799"}),
        httpx.Response(422, json={"error": "BUSINESS_RULE_VIOLATION", "message": "NO_SEATS: full"}),
        httpx.Response(204),
        httpx.Response(200, json={"status": "COMMITTED"}),
    )
    http, _ = client(rec)
    catalog, s = HttpCatalog(http, "http://catalog:8000"), saga()
    assert catalog.reserve(s, CALL) == UUID("77777777-7777-4777-8777-777777777799")
    with pytest.raises(Rejected) as caught:
        catalog.reserve(s, CALL)
    assert caught.value.reason is Reason.NO_SEATS
    catalog.release(s.section_id, s.reservation_id, CALL)
    catalog.commit(s.section_id, s.reservation_id, CALL)
    assert [r.method for r in rec.requests] == ["POST", "POST", "DELETE", "POST"]
    assert rec.requests[0].url.path == f"/api/v1/catalog/sections/{s.section_id}/reservations"


def test_retries_only_network_429_and_5xx_bounded_with_jitter():
    rec = Recorder(httpx.Response(503), httpx.Response(429), httpx.Response(204))
    http, sleeps = client(rec)
    HttpCatalog(http, "http://catalog:8000").release(uuid4(), uuid4(), CALL)
    assert len(rec.requests) == 3 and len(sleeps) == 2 and all(0 <= s <= 2.0 for s in sleeps)
    rec = Recorder(httpx.Response(503), httpx.Response(503), httpx.Response(503))
    http, sleeps = client(rec)
    with pytest.raises(Unavailable):
        HttpCatalog(http, "http://catalog:8000").release(uuid4(), uuid4(), CALL)
    assert len(rec.requests) == 3 and len(sleeps) == 2
    rec = Recorder(httpx.Response(400, json={"error": "VALIDATION_ERROR"}))
    http, sleeps = client(rec)
    with pytest.raises(Unavailable):
        HttpCatalog(http, "http://catalog:8000").commit(uuid4(), uuid4(), CALL)
    assert len(rec.requests) == 1 and sleeps == [], "a 4xx is final"

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    http = HttpClient("svc-token", httpx.Client(transport=httpx.MockTransport(down)), attempts=2, sleep=lambda s: None)
    with pytest.raises(Unavailable) as caught:
        HttpBilling(http, "http://billing:8000").ensure_tuition_paid(uuid4(), CALL)
    assert "ConnectError" in str(caught.value)
