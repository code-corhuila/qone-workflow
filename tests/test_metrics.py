"""HU-OBS-001 (code-corhuila/qone-docs#56): the RED metrics of every request and the outcome and
duration of every saga. Core level: the orchestrator reports each ended saga through its port.
HTTP level: `GET /metrics` at the root, the route template as label, the saga series."""

import pytest

from tests.fakes import LAURA, SECTION, FixedClock, Participants, RecordingMetrics, SequenceIds
from tests.http_support import LAURA as LAURA_ID
from tests.http_support import SECTION as SECTION_ID
from tests.http_support import bearer, make_client
from workflow.adapter.outbound.state.memory import InMemorySagaStore
from workflow.application.port.inbound.sagas import Caller, Role, StartEnrollStudent
from workflow.application.usecase.enroll_student import EnrollStudentSaga
from workflow.domain.saga.saga import Origin, Reason

laura = Caller(LAURA, Role.STUDENT, "Laura Gómez", str(LAURA))
BASE = "/api/v1/sagas"
BODY = {"studentId": LAURA_ID, "sectionId": SECTION_ID, "origin": "UI"}


@pytest.fixture
def core():
    participants, metrics = Participants(), RecordingMetrics()
    saga = EnrollStudentSaga(
        InMemorySagaStore(), participants, participants, participants, FixedClock(), SequenceIds(), metrics
    )
    return participants, metrics, saga


def start(saga, key="enroll-metrics-0001"):
    return saga.start(laura, StartEnrollStudent(LAURA, SECTION, Origin.UI), key, "corr-metrics")


def test_an_ended_saga_is_reported_once_with_its_outcome_and_duration(core):
    _, metrics, saga = core
    result = start(saga).saga
    assert metrics.ended == [("enroll-student", "completed", (result.updated_at - result.created_at).total_seconds())]
    assert metrics.ended[0][2] > 0
    start(saga)  # the repeated start runs nothing and reports nothing
    assert len(metrics.ended) == 1


def test_compensated_and_failed_outcomes(core):
    participants, metrics, saga = core
    participants.reject["reserve-seat"] = Reason.NO_SEATS
    start(saga, "enroll-metrics-0002")
    participants.reject.clear()
    participants.fail.update({"commit-seat", "release-seat"})
    start(saga, "enroll-metrics-0003")
    assert [(name, outcome) for name, outcome, _ in metrics.ended] == [
        ("enroll-student", "compensated"),
        ("enroll-student", "failed"),
    ]


def test_without_a_metrics_port_the_saga_still_runs():
    participants = Participants()
    saga = EnrollStudentSaga(
        InMemorySagaStore(), participants, participants, participants, FixedClock(), SequenceIds()
    )
    assert start(saga).saga.status.value == "COMPLETED"


@pytest.fixture
def world():
    return make_client()


def sample(client, name, labels):
    return client.app.state.metrics_registry.get_sample_value(name, labels)


def test_metrics_answers_200_text_plain_with_the_metric_names(world):
    client, _ = world
    client.get(f"{BASE}/health")
    r = client.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    for name in (
        "http_server_requests_total",
        "http_server_request_duration_seconds",
        "workflow_saga_requests_total",
        "workflow_saga_duration_seconds",
    ):
        assert name in r.text


def test_a_request_is_counted_under_its_route_template_never_the_raw_path(world):
    client, _ = world
    laura_headers = bearer(LAURA_ID, "STUDENT", "Laura Gómez")
    client.get(f"{BASE}/99999999-9999-4999-8999-999999999999", headers=laura_headers)
    client.get(f"{BASE}/not-a-uuid", headers=laura_headers)
    client.get("/api/v1/nowhere")
    client.get("/metrics")
    labels = {"service": "qone-workflow", "method": "GET", "route": f"{BASE}/{{saga_id}}", "status_class": "4xx"}
    assert sample(client, "http_server_requests_total", labels) == 2
    assert sample(client, "http_server_requests_total", {**labels, "route": "unmatched"}) == 1
    timing = {"service": "qone-workflow", "method": "GET", "route": f"{BASE}/{{saga_id}}"}
    assert sample(client, "http_server_request_duration_seconds_count", timing) == 2
    text = client.get("/metrics").text
    assert "not-a-uuid" not in text and 'route="/metrics"' not in text


def test_a_saga_through_http_grows_its_outcome_series(world):
    client, participants = world
    headers = {**bearer(LAURA_ID, "STUDENT", "Laura Gómez"), "Idempotency-Key": "enroll-metrics-http-1"}
    assert client.post(f"{BASE}/enroll-student", json=BODY, headers=headers).status_code == 201
    participants.reject["check-tuition"] = Reason.TUITION_UNPAID
    client.post(f"{BASE}/enroll-student", json=BODY, headers={**headers, "Idempotency-Key": "enroll-metrics-http-2"})
    for outcome in ("completed", "compensated"):
        labels = {"saga": "enroll-student", "outcome": outcome}
        assert sample(client, "workflow_saga_requests_total", labels) == 1
        assert sample(client, "workflow_saga_duration_seconds_count", labels) == 1
    started = {"service": "qone-workflow", "method": "POST", "route": f"{BASE}/enroll-student", "status_class": "2xx"}
    assert sample(client, "http_server_requests_total", started) == 2
