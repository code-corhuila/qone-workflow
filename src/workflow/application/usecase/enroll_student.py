"""The orchestrator of the enroll-student saga (Annex E): runs the steps in order, persists the
saga after every step, and when a step fails undoes the done steps in reverse. A rejected gate
ends COMPENSATED with its reason, and the enrollment domain records the rejected attempt (D-01);
a failed undo ends FAILED for a person to decide. Every call carries `<sagaId>:<step>` as its
Idempotency-Key and the caller's correlation id, so a resumed or repeated step never acts twice.

Who may start it (07-api/authentication.md): a STUDENT for themselves with origin UI; the
advisor's SERVICE token for a student with origin AGENT and the confirmation (INV-ENR-005).
Who may read it: the student who started it, an ADMIN or a SERVICE; another student gets 404.
"""

import logging
from uuid import UUID

from workflow.application.errors import Forbidden, InvalidInput, NotFound
from workflow.application.port.inbound.sagas import Caller, Role, SagaUseCases, Started, StartEnrollStudent
from workflow.application.port.outbound.participants import (
    Billing,
    Call,
    Catalog,
    Clock,
    Enrollment,
    IdGenerator,
    Rejected,
    SagaStore,
    Unavailable,
)
from workflow.domain.saga.saga import RECORDABLE, Origin, Saga, SagaStatus, Step

log = logging.getLogger("workflow.saga")
KEY_MIN, KEY_MAX = 8, 128


class EnrollStudentSaga(SagaUseCases):
    def __init__(
        self,
        store: SagaStore,
        billing: Billing,
        enrollment: Enrollment,
        catalog: Catalog,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._store = store
        self._billing = billing
        self._enrollment = enrollment
        self._catalog = catalog
        self._clock = clock
        self._ids = ids

    def start(self, caller: Caller, command: StartEnrollStudent, idempotency_key: str, correlation_id: str) -> Started:
        if not KEY_MIN <= len(idempotency_key or "") <= KEY_MAX:
            raise InvalidInput("Idempotency-Key", "header required, 8 to 128 characters")
        if caller.role is Role.STUDENT:
            if command.student_id != caller.user_id:
                raise Forbidden("a student starts the saga only for themselves")
            if command.origin is not Origin.UI or command.confirmation_id is not None:
                raise InvalidInput("origin", "a student starts the saga with origin UI and no confirmationId")
        elif caller.role is Role.SERVICE:
            if command.origin is not Origin.AGENT:
                raise InvalidInput("origin", "a service starts the saga with origin AGENT")
            if command.confirmation_id is None:
                raise InvalidInput("confirmationId", "INV-ENR-005: required when the origin is AGENT")
        else:
            raise Forbidden()
        saga = Saga.start(
            self._ids.new_id(),
            command.student_id,
            command.section_id,
            command.origin,
            command.confirmation_id,
            correlation_id,
            self._clock.now(),
        )
        stored, created = self._store.create(saga, idempotency_key)
        if not created:
            return Started(stored, created=False)  # Annex E: a repeated start runs no step again
        return Started(self.run(stored), created=True)

    def get(self, caller: Caller, saga_id: UUID) -> Saga:
        saga = self._store.find(saga_id)
        if saga is None:
            raise NotFound()
        if caller.role is Role.STUDENT and saga.student_id == caller.user_id:
            return saga
        if caller.role in (Role.ADMIN, Role.SERVICE):
            return saga
        raise NotFound()  # D-03: another student's saga does not exist for this caller

    def resume_running(self) -> int:
        """After a restart: every saga left RUNNING continues from its persisted state."""
        count = 0
        for saga in self._store.running():
            self.run(saga)
            count += 1
        return count

    def run(self, saga: Saga) -> Saga:
        while saga.next_step is not None:
            step = saga.next_step
            try:
                saga = self._do(step, saga)
            except Rejected as rejected:
                saga = saga.step_failed(step, rejected.reason, None, self._clock.now())
            except Unavailable as error:
                log.warning(
                    "step failed", extra={"traceId": saga.correlation_id, "step": step.value, "detail": str(error)}
                )
                saga = saga.step_failed(step, None, str(error), self._clock.now())
            self._store.save(saga)
        if saga.is_compensating:
            saga = self._compensate(saga)
        if saga.status is SagaStatus.RUNNING:
            saga = saga.finish(self._clock.now())
            self._store.save(saga)
        return saga

    def _do(self, step: Step, saga: Saga) -> Saga:
        call = self._call(saga, step.value)
        now = self._clock.now
        if step is Step.CHECK_TUITION:
            self._billing.ensure_tuition_paid(saga.student_id, call)
            return saga.step_done(step, now())
        if step is Step.CHECK_ELIGIBILITY:
            self._enrollment.ensure_eligible(saga.student_id, saga.section_id, call)
            return saga.step_done(step, now())
        if step is Step.RESERVE_SEAT:
            return saga.step_done(step, now(), reservation_id=self._catalog.reserve(saga, call))
        if step is Step.CONFIRM_ENROLLMENT:
            return saga.step_done(step, now(), enrollment_id=self._enrollment.record_confirmed(saga, call))
        assert saga.reservation_id is not None
        self._catalog.commit(saga.section_id, saga.reservation_id, call)
        return saga.step_done(step, now())

    def _compensate(self, saga: Saga) -> Saga:
        for step in saga.pending_undo:
            call = self._call(saga, f"undo-{step.value}")
            try:
                if step is Step.CONFIRM_ENROLLMENT:
                    assert saga.enrollment_id is not None
                    self._enrollment.cancel(saga.enrollment_id, call)
                else:
                    assert saga.reservation_id is not None
                    self._catalog.release(saga.section_id, saga.reservation_id, call)
            except (Unavailable, Rejected) as error:
                log.error(
                    "compensation failed",
                    extra={"traceId": saga.correlation_id, "step": step.value, "detail": str(error)},
                )
                saga = saga.compensation_failed(step, str(error), self._clock.now())
                self._store.save(saga)
                return saga
            saga = saga.undone(step, self._clock.now())
            self._store.save(saga)
        if saga.reason in RECORDABLE:
            try:  # D-01: the enrollment domain keeps the rejected attempt as a fact; best effort
                saga = saga.rejection_recorded(
                    self._enrollment.record_rejected(saga, self._call(saga, "record-rejection")), self._clock.now()
                )
            except (Unavailable, Rejected) as error:
                log.warning(
                    "rejected attempt not recorded", extra={"traceId": saga.correlation_id, "detail": str(error)}
                )
        saga = saga.finish(self._clock.now())
        self._store.save(saga)
        return saga

    @staticmethod
    def _call(saga: Saga, step: str) -> Call:
        return Call(idempotency_key=f"{saga.id}:{step}", correlation_id=saga.correlation_id)
