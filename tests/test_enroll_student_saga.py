"""Core level: the Annex E checks of the orchestrator with fake participants and the in-memory
store: happy path, failure at each step compensating in reverse, a failed compensation, the
idempotent start, the step keys and the correlation, a resumed saga, who may start and read."""

from uuid import uuid4

import pytest

from tests.fakes import LAURA, SANTIAGO, SECTION, FixedClock, Participants, SequenceIds
from workflow.adapter.outbound.state.memory import InMemorySagaStore
from workflow.application.errors import Forbidden, InvalidInput, NotFound
from workflow.application.port.inbound.sagas import Caller, Role, StartEnrollStudent
from workflow.application.usecase.enroll_student import EnrollStudentSaga
from workflow.domain.saga.saga import Origin, Reason, Saga, SagaStatus, Step

laura = Caller(LAURA, Role.STUDENT, "Laura Gómez", str(LAURA))
admin = Caller(uuid4(), Role.ADMIN, "Patricia Mora")
advisor = Caller(None, Role.SERVICE, "qone-advisor-api", "qone-advisor-api")
KEY = "enroll-6f1d2c3e-9b7a-4e1f-8c2d-0a1b2c3d4e5f"


@pytest.fixture
def world():
    store, participants = InMemorySagaStore(), Participants()
    saga = EnrollStudentSaga(store, participants, participants, participants, FixedClock(), SequenceIds())
    return store, participants, saga


def start(saga, caller=laura, command=None, key=KEY, corr="corr-e2e-1"):
    return saga.start(caller, command or StartEnrollStudent(LAURA, SECTION, Origin.UI), key, corr)


def test_the_happy_path_runs_the_five_steps_in_order_and_completes(world):
    store, participants, saga = world
    result = start(saga)
    assert result.created and result.saga.status is SagaStatus.COMPLETED
    assert participants.operations() == [
        "check-tuition",
        "check-eligibility",
        "reserve-seat",
        "confirm-enrollment",
        "commit-seat",
    ]
    assert (
        result.saga.reservation_id == participants.reservation_id
        and result.saga.enrollment_id == participants.enrollment_id
    )
    assert [s.status for s in store.history].count(SagaStatus.RUNNING) == 6, "persisted at start and after each step"


def test_every_step_carries_its_key_and_the_callers_correlation(world):
    _, participants, saga = world
    saga_id = start(saga).saga.id
    assert [c[1] for c in participants.calls] == [
        f"{saga_id}:{s}"
        for s in ("check-tuition", "check-eligibility", "reserve-seat", "confirm-enrollment", "commit-seat")
    ]
    assert {c[2] for c in participants.calls} == {"corr-e2e-1"}


@pytest.mark.parametrize(
    ("gate", "reason", "undone"),
    [
        ("check-tuition", Reason.TUITION_UNPAID, []),
        ("check-eligibility", Reason.PREREQ_MISSING, []),
        ("reserve-seat", Reason.NO_SEATS, []),
        ("confirm-enrollment", Reason.DUPLICATE, ["release-seat"]),
    ],
)
def test_a_rejected_gate_compensates_with_its_reason(world, gate, reason, undone):
    _, participants, saga = world
    participants.reject[gate] = reason
    result = start(saga).saga
    assert result.status is SagaStatus.COMPENSATED and result.failed_step.value == gate and result.reason is reason
    calls = participants.operations()
    assert calls[calls.index(gate) + 1 :] == undone + (["record-rejection"] if reason is not Reason.DUPLICATE else [])
    if reason is not Reason.DUPLICATE:
        assert result.enrollment_id == participants.enrollment_id, "D-01: the rejected attempt is recorded"


def test_a_failed_last_step_undoes_in_reverse_order(world):
    _, participants, saga = world
    participants.fail.add("commit-seat")
    result = start(saga).saga
    assert result.status is SagaStatus.COMPENSATED and result.failed_step is Step.COMMIT_SEAT and result.reason is None
    assert participants.operations()[-2:] == ["cancel-enrollment", "release-seat"]
    assert result.detail == "commit-seat: upstream 503 from qone-internal-host", (
        "kept in the state, never in the contract fields"
    )


def test_a_failed_compensation_ends_failed(world):
    _, participants, saga = world
    participants.fail.update({"commit-seat", "release-seat"})
    result = start(saga).saga
    assert result.status is SagaStatus.FAILED and "undo of reserve-seat failed" in result.detail
    assert participants.operations()[-2:] == ["cancel-enrollment", "release-seat"]


def test_a_repeated_start_returns_the_same_saga_and_runs_no_step(world):
    _, participants, saga = world
    first = start(saga)
    again = start(saga, key=KEY)
    assert not again.created and again.saga == first.saga and len(participants.calls) == 5


def test_a_saga_left_running_resumes_from_its_persisted_state(world):
    store, participants, saga = world
    halfway = Saga.start(uuid4(), LAURA, SECTION, Origin.UI, None, "corr-restart", FixedClock().now())
    halfway = halfway.step_done(Step.CHECK_TUITION, FixedClock().now()).step_done(
        Step.CHECK_ELIGIBILITY, FixedClock().now()
    )
    store.create(halfway, "enroll-restart-0001")
    assert saga.resume_running() == 1
    assert participants.operations() == ["reserve-seat", "confirm-enrollment", "commit-seat"]
    assert store.find(halfway.id).status is SagaStatus.COMPLETED


def test_who_may_start(world):
    _, _, saga = world
    with pytest.raises(Forbidden):
        start(saga, command=StartEnrollStudent(SANTIAGO, SECTION, Origin.UI))
    with pytest.raises(InvalidInput) as caught:
        start(saga, command=StartEnrollStudent(LAURA, SECTION, Origin.AGENT, uuid4()))
    assert caught.value.field == "origin"
    with pytest.raises(InvalidInput) as caught:
        start(saga, caller=advisor, command=StartEnrollStudent(LAURA, SECTION, Origin.AGENT))
    assert caught.value.field == "confirmationId"
    assert start(
        saga, caller=advisor, command=StartEnrollStudent(LAURA, SECTION, Origin.AGENT, uuid4()), key="enroll-agent-0001"
    ).created
    with pytest.raises(Forbidden):
        start(saga, caller=admin)
    with pytest.raises(InvalidInput) as caught:
        start(saga, key="short")
    assert caught.value.field == "Idempotency-Key"


def test_who_may_read(world):
    _, _, saga = world
    started = start(saga).saga
    assert (
        saga.get(laura, started.id) == started
        and saga.get(admin, started.id) == started
        and saga.get(advisor, started.id) == started
    )
    with pytest.raises(NotFound):
        saga.get(Caller(SANTIAGO, Role.STUDENT), started.id)
    with pytest.raises(NotFound):
        saga.get(laura, uuid4())
