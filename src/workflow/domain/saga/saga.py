"""The enroll-student saga (ADR-008, Annex E): its steps in order, its states and the rules of
its life. Pure Python: no framework, no HTTP client, no Redis (Annex C dependency rule).

RUNNING -> COMPLETED          every step done
RUNNING -> COMPENSATED        a gate rejected (or a step failed) and every done step was undone
RUNNING -> FAILED             a compensation failed: a person must decide (Annex E)

A step is recorded as completed only in order; the failed step and the domain reason are kept;
the internal detail of a failure never leaves the saga's state (the contract shows failedStep
and reason only).
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

SAGA_TYPE = "enroll-student"


class SagaStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPENSATED = "COMPENSATED"
    FAILED = "FAILED"


class Step(StrEnum):
    CHECK_TUITION = "check-tuition"
    CHECK_ELIGIBILITY = "check-eligibility"
    RESERVE_SEAT = "reserve-seat"
    CONFIRM_ENROLLMENT = "confirm-enrollment"
    COMMIT_SEAT = "commit-seat"


STEPS = (Step.CHECK_TUITION, Step.CHECK_ELIGIBILITY, Step.RESERVE_SEAT, Step.CONFIRM_ENROLLMENT, Step.COMMIT_SEAT)
# The steps that change something elsewhere and their undo; reads and the last step have none.
UNDOABLE = (Step.RESERVE_SEAT, Step.CONFIRM_ENROLLMENT)


class Reason(StrEnum):
    TUITION_UNPAID = "TUITION_UNPAID"
    PREREQ_MISSING = "PREREQ_MISSING"
    NO_SEATS = "NO_SEATS"
    SCHEDULE_CONFLICT = "SCHEDULE_CONFLICT"
    DUPLICATE = "DUPLICATE"


# The reasons the enrollment domain records as a REJECTED attempt (D-01); DUPLICATE is not one.
RECORDABLE = (Reason.TUITION_UNPAID, Reason.PREREQ_MISSING, Reason.NO_SEATS, Reason.SCHEDULE_CONFLICT)


class Origin(StrEnum):
    UI = "UI"
    AGENT = "AGENT"


class SagaRuleError(Exception):
    """A transition the saga does not allow; a programming error of the orchestrator."""


@dataclass(frozen=True)
class Saga:
    id: UUID
    student_id: UUID
    section_id: UUID
    origin: Origin
    confirmation_id: UUID | None
    status: SagaStatus
    completed_steps: tuple[Step, ...]
    failed_step: Step | None
    reason: Reason | None
    reservation_id: UUID | None
    enrollment_id: UUID | None
    correlation_id: str
    detail: str | None
    created_at: datetime
    updated_at: datetime
    compensated_steps: tuple[Step, ...] = ()

    @staticmethod
    def start(
        saga_id: UUID,
        student_id: UUID,
        section_id: UUID,
        origin: Origin,
        confirmation_id: UUID | None,
        correlation_id: str,
        now: datetime,
    ) -> "Saga":
        moment = _utc(now)
        return Saga(
            saga_id,
            student_id,
            section_id,
            origin,
            confirmation_id,
            SagaStatus.RUNNING,
            (),
            None,
            None,
            None,
            None,
            correlation_id,
            None,
            moment,
            moment,
        )

    @property
    def type(self) -> str:
        return SAGA_TYPE

    @property
    def next_step(self) -> Step | None:
        if (
            self.status is not SagaStatus.RUNNING
            or self.failed_step is not None
            or len(self.completed_steps) == len(STEPS)
        ):
            return None
        return STEPS[len(self.completed_steps)]

    @property
    def is_compensating(self) -> bool:
        return self.status is SagaStatus.RUNNING and self.failed_step is not None

    @property
    def pending_undo(self) -> tuple[Step, ...]:
        """The completed steps still to undo, in reverse order."""
        return tuple(s for s in reversed(self.completed_steps) if s in UNDOABLE and s not in self.compensated_steps)

    def step_done(
        self, step: Step, now: datetime, reservation_id: UUID | None = None, enrollment_id: UUID | None = None
    ) -> "Saga":
        if step is not self.next_step:
            raise SagaRuleError(f"{step} is not the next step of the saga")
        return replace(
            self,
            completed_steps=(*self.completed_steps, step),
            reservation_id=reservation_id or self.reservation_id,
            enrollment_id=enrollment_id or self.enrollment_id,
            updated_at=_utc(now),
        )

    def step_failed(self, step: Step, reason: Reason | None, detail: str | None, now: datetime) -> "Saga":
        if step is not self.next_step:
            raise SagaRuleError(f"{step} is not the running step of the saga")
        return replace(self, failed_step=step, reason=reason, detail=detail, updated_at=_utc(now))

    def undone(self, step: Step, now: datetime) -> "Saga":
        if step not in self.pending_undo:
            raise SagaRuleError(f"{step} has nothing to undo")
        return replace(self, compensated_steps=(*self.compensated_steps, step), updated_at=_utc(now))

    def rejection_recorded(self, enrollment_id: UUID, now: datetime) -> "Saga":
        return replace(self, enrollment_id=enrollment_id, updated_at=_utc(now))

    def finish(self, now: datetime) -> "Saga":
        if self.status is not SagaStatus.RUNNING:
            return self
        if self.failed_step is None:
            if len(self.completed_steps) != len(STEPS):
                raise SagaRuleError("the saga cannot complete before its last step")
            return replace(self, status=SagaStatus.COMPLETED, updated_at=_utc(now))
        if self.pending_undo:
            raise SagaRuleError("the saga cannot be compensated while a step is still to undo")
        return replace(self, status=SagaStatus.COMPENSATED, updated_at=_utc(now))

    def compensation_failed(self, step: Step, detail: str, now: datetime) -> "Saga":
        return replace(self, status=SagaStatus.FAILED, detail=f"undo of {step} failed: {detail}", updated_at=_utc(now))


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
