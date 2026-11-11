"""What the workflow needs (Annex E, outbound ports): one port per participant, each with its do
and its undo, the saga store, a clock and an id generator. A participant answers a domain
verdict (a rejected gate) with `Rejected`; anything it could not answer (network, timeout, a
5xx after the bounded retries) is `Unavailable` and makes the saga compensate."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from workflow.domain.saga.saga import Origin, Reason, Saga


class Rejected(Exception):
    def __init__(self, reason: Reason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class Unavailable(Exception):
    """The participant could not give an answer; the detail stays in the saga and the log."""


@dataclass(frozen=True)
class StartKey:
    """The Idempotency-Key of a start, scoped to its owner (#55): the user id of the token or the
    SERVICE subject, so another caller's key never finds this saga. `fingerprint` is the SHA-256
    of the canonical request body: a replay must carry the same one."""

    owner: str
    key: str
    fingerprint: str


@dataclass(frozen=True)
class Claim:
    """What the store answers to a start: the saga under the owner's key, whether this call
    created it, and the fingerprint stored with the key."""

    saga: Saga
    created: bool
    fingerprint: str


@dataclass(frozen=True)
class Call:
    """What travels with every participant call: the step's key and the caller's correlation id."""

    idempotency_key: str
    correlation_id: str


class Billing(Protocol):
    def ensure_tuition_paid(self, student_id: UUID, call: Call) -> None:
        """BI-06; Rejected(TUITION_UNPAID) when the current invoice is missing or not PAID."""


class Enrollment(Protocol):
    def ensure_eligible(self, student_id: UUID, section_id: UUID, call: Call) -> None:
        """EN-04; Rejected(DUPLICATE | PREREQ_MISSING | SCHEDULE_CONFLICT)."""

    def record_confirmed(self, saga: Saga, call: Call) -> UUID:
        """EN-05 CONFIRMED with the reservation; Rejected(DUPLICATE) on INV-ENR-001. Returns the enrollment id."""

    def record_rejected(self, saga: Saga, call: Call) -> UUID:
        """EN-05 REJECTED with the saga's reason (D-01). Returns the enrollment id."""

    def cancel(self, enrollment_id: UUID, call: Call) -> None:
        """EN-06, idempotent."""


class Catalog(Protocol):
    def reserve(self, saga: Saga, call: Call) -> UUID:
        """CA-10; Rejected(NO_SEATS). Returns the reservation id."""

    def release(self, section_id: UUID, reservation_id: UUID, call: Call) -> None:
        """CA-11, idempotent."""

    def commit(self, section_id: UUID, reservation_id: UUID, call: Call) -> None:
        """CA-12, idempotent."""


class SagaStore(Protocol):
    def create(self, saga: Saga, key: StartKey) -> Claim:
        """Stores a new saga under its owner's key, atomically: the saga created, or the saga the
        same owner's key started first with the fingerprint it was started with."""

    def save(self, saga: Saga) -> None: ...

    def find(self, saga_id: UUID) -> Saga | None: ...

    def running(self) -> list[Saga]:
        """The sagas left RUNNING (a restart in the middle), to resume."""


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new_id(self) -> UUID: ...


__all__ = [
    "Billing",
    "Call",
    "Catalog",
    "Claim",
    "Clock",
    "Enrollment",
    "IdGenerator",
    "Origin",
    "Rejected",
    "SagaStore",
    "StartKey",
    "Unavailable",
]
