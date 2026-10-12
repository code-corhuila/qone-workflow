"""Fake participants that record every call and can be told to reject or to fail a step."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from workflow.application.port.outbound.participants import Rejected, Unavailable
from workflow.domain.saga.saga import Reason

LAURA = UUID("11111111-1111-4111-8111-111111111111")
SANTIAGO = UUID("11111111-1111-4111-8111-111111111112")
SECTION = UUID("55555555-5555-4555-8555-555555555505")


class Participants:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []  # (operation, idempotency key, correlation id)
        self.reject: dict[str, Reason] = {}
        self.fail: set[str] = set()
        self.reservation_id = uuid4()
        self.enrollment_id = uuid4()

    def _hit(self, operation: str, call) -> None:
        self.calls.append((operation, call.idempotency_key, call.correlation_id))
        if operation in self.fail:
            raise Unavailable(f"{operation}: upstream 503 from qone-internal-host")
        if operation in self.reject:
            raise Rejected(self.reject[operation])

    def operations(self) -> list[str]:
        return [c[0] for c in self.calls]

    # Billing
    def ensure_tuition_paid(self, student_id, call):
        self._hit("check-tuition", call)

    # Enrollment
    def ensure_eligible(self, student_id, section_id, call):
        self._hit("check-eligibility", call)

    def record_confirmed(self, saga, call):
        self._hit("confirm-enrollment", call)
        return self.enrollment_id

    def record_rejected(self, saga, call):
        self._hit("record-rejection", call)
        return self.enrollment_id

    def cancel(self, enrollment_id, call):
        self._hit("cancel-enrollment", call)

    # Catalog
    def reserve(self, saga, call):
        self._hit("reserve-seat", call)
        return self.reservation_id

    def release(self, section_id, reservation_id, call):
        self._hit("release-seat", call)

    def commit(self, section_id, reservation_id, call):
        self._hit("commit-seat", call)


class FixedClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)

    def now(self):
        self.current = self.current + timedelta(milliseconds=10)
        return self.current


class SequenceIds:
    def __init__(self) -> None:
        self.next = 1

    def new_id(self) -> UUID:
        value = UUID(f"99999999-9999-4999-8999-9999999999{self.next:02d}")
        self.next += 1
        return value
