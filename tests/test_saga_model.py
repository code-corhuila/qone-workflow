"""Core level: the saga's states and transitions, no server, no Redis."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from workflow.domain.saga.saga import STEPS, Origin, Reason, Saga, SagaRuleError, SagaStatus, Step

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def started():
    return Saga.start(uuid4(), uuid4(), uuid4(), Origin.UI, None, "corr-1", NOW)


def run_through(saga, upto):
    for step in STEPS[:upto]:
        extra = (
            {"reservation_id": uuid4()}
            if step is Step.RESERVE_SEAT
            else {"enrollment_id": uuid4()}
            if step is Step.CONFIRM_ENROLLMENT
            else {}
        )
        saga = saga.step_done(step, NOW, **extra)
    return saga


def test_a_saga_starts_running_at_the_first_step():
    saga = started()
    assert saga.status is SagaStatus.RUNNING and saga.next_step is Step.CHECK_TUITION and saga.type == "enroll-student"


def test_steps_complete_in_order_and_the_saga_completes():
    saga = run_through(started(), 5)
    assert saga.completed_steps == STEPS and saga.next_step is None and saga.reservation_id and saga.enrollment_id
    done = saga.finish(NOW + timedelta(seconds=1))
    assert done.status is SagaStatus.COMPLETED and done.updated_at == NOW + timedelta(seconds=1)
    assert done.finish(NOW) == done


def test_a_step_out_of_order_is_refused():
    with pytest.raises(SagaRuleError):
        started().step_done(Step.RESERVE_SEAT, NOW)
    with pytest.raises(SagaRuleError):
        started().finish(NOW)


def test_a_rejected_gate_undoes_in_reverse_and_ends_compensated():
    saga = run_through(started(), 4).step_failed(Step.COMMIT_SEAT, None, "catalog 503", NOW)
    assert saga.is_compensating and saga.next_step is None
    assert saga.pending_undo == (Step.CONFIRM_ENROLLMENT, Step.RESERVE_SEAT)
    with pytest.raises(SagaRuleError):
        saga.finish(NOW)
    saga = saga.undone(Step.CONFIRM_ENROLLMENT, NOW).undone(Step.RESERVE_SEAT, NOW)
    final = saga.finish(NOW)
    assert (
        final.status is SagaStatus.COMPENSATED
        and final.failed_step is Step.COMMIT_SEAT
        and final.detail == "catalog 503"
    )


def test_a_gate_with_nothing_to_undo_compensates_at_once_with_its_reason():
    saga = (
        started()
        .step_done(Step.CHECK_TUITION, NOW)
        .step_failed(Step.CHECK_ELIGIBILITY, Reason.PREREQ_MISSING, None, NOW)
    )
    assert saga.pending_undo == ()
    final = saga.finish(NOW)
    assert (
        final.status is SagaStatus.COMPENSATED
        and final.reason is Reason.PREREQ_MISSING
        and final.completed_steps == (Step.CHECK_TUITION,)
    )


def test_a_failed_compensation_ends_failed_for_a_person():
    saga = run_through(started(), 3).step_failed(Step.CONFIRM_ENROLLMENT, None, "x", NOW)
    failed = saga.compensation_failed(Step.RESERVE_SEAT, "catalog unreachable", NOW)
    assert failed.status is SagaStatus.FAILED and "undo of reserve-seat failed" in failed.detail
    with pytest.raises(SagaRuleError):
        saga.undone(Step.CHECK_TUITION, NOW)
