"""In-memory saga store: for the core and HTTP tests only. It cannot survive a restart, which is
exactly what a saga store must do (Annex E); production uses Redis (ADR-008)."""

from uuid import UUID

from workflow.domain.saga.saga import Saga, SagaStatus


class InMemorySagaStore:
    def __init__(self) -> None:
        self.sagas: dict[UUID, Saga] = {}
        self.keys: dict[str, UUID] = {}
        self.history: list[Saga] = []

    def create(self, saga: Saga, idempotency_key: str) -> tuple[Saga, bool]:
        earlier = self.keys.get(idempotency_key)
        if earlier is not None:
            return self.sagas[earlier], False
        self.keys[idempotency_key] = saga.id
        self.save(saga)
        return saga, True

    def save(self, saga: Saga) -> None:
        self.sagas[saga.id] = saga
        self.history.append(saga)

    def find(self, saga_id: UUID) -> Saga | None:
        return self.sagas.get(saga_id)

    def running(self) -> list[Saga]:
        return [s for s in self.sagas.values() if s.status is SagaStatus.RUNNING]
