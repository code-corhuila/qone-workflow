"""What the workflow offers (Annex E, inbound ports): start the saga and read it."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from workflow.domain.saga.saga import Origin, Saga


class Role(StrEnum):
    STUDENT = "STUDENT"
    PROFESSOR = "PROFESSOR"
    ADMIN = "ADMIN"
    SERVICE = "SERVICE"


@dataclass(frozen=True)
class Caller:
    user_id: UUID | None
    role: Role
    name: str = ""
    subject: str = ""


@dataclass(frozen=True)
class StartEnrollStudent:
    student_id: UUID
    section_id: UUID
    origin: Origin
    confirmation_id: UUID | None = None


@dataclass(frozen=True)
class Started:
    saga: Saga
    created: bool


class SagaUseCases(Protocol):
    def start(
        self, caller: Caller, command: StartEnrollStudent, idempotency_key: str, correlation_id: str
    ) -> Started: ...

    def get(self, caller: Caller, saga_id: UUID) -> Saga: ...
