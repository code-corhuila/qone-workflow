"""In-memory saga store: for the core and HTTP tests only. It cannot survive a restart, which is
exactly what a saga store must do (Annex E); production uses Redis (ADR-008)."""

from uuid import UUID

from workflow.application.port.outbound.participants import Claim, StartKey
from workflow.domain.saga.saga import Saga, SagaStatus


class InMemorySagaStore:
    def __init__(self) -> None:
        self.sagas: dict[UUID, Saga] = {}
        self.keys: dict[tuple[str, str], tuple[UUID, str]] = {}  # (owner, key) -> (saga id, fingerprint)
        self.history: list[Saga] = []

    def create(self, saga: Saga, key: StartKey) -> Claim:
        earlier = self.keys.get((key.owner, key.key))
        if earlier is not None:
            return Claim(self.sagas[earlier[0]], False, earlier[1])
        self.keys[(key.owner, key.key)] = (saga.id, key.fingerprint)
        self.save(saga)
        return Claim(saga, True, key.fingerprint)

    def save(self, saga: Saga) -> None:
        self.sagas[saga.id] = saga
        self.history.append(saga)

    def find(self, saga_id: UUID) -> Saga | None:
        return self.sagas.get(saga_id)

    def running(self) -> list[Saga]:
        return [s for s in self.sagas.values() if s.status is SagaStatus.RUNNING]
