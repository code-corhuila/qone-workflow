"""The saga store in Redis (ADR-008): one hash per saga (`saga:<id>`, the status and the document),
one key per owner and Idempotency-Key (`saga-key:<owner>:<key>`, #55) holding the saga id and the
fingerprint of the body, written with NX so the first start wins, and the set of sagas left
RUNNING (`sagas:running`) for the resume after a restart. Redis runs with `appendonly yes`; the
idempotent steps absorb a lost last write."""

import json
from datetime import datetime
from uuid import UUID

import redis

from workflow.application.port.outbound.participants import Claim, StartKey
from workflow.domain.saga.saga import Origin, Reason, Saga, SagaStatus, Step

RUNNING = "sagas:running"


class RedisSagaStore:
    def __init__(self, client: redis.Redis) -> None:
        self._redis = client

    def ping(self) -> bool:
        try:
            return bool(self._redis.ping())
        except redis.RedisError:
            return False

    def create(self, saga: Saga, key: StartKey) -> Claim:
        name = f"saga-key:{key.owner}:{key.key}"
        claimed = json.dumps({"sagaId": str(saga.id), "fingerprint": key.fingerprint})
        if not self._redis.set(name, claimed, nx=True):
            stored = json.loads(self._redis.get(name))
            earlier = self.find(UUID(stored["sagaId"]))
            assert earlier is not None
            return Claim(earlier, False, stored["fingerprint"])
        self.save(saga)
        return Claim(saga, True, key.fingerprint)

    def save(self, saga: Saga) -> None:
        pipe = self._redis.pipeline(transaction=True)
        pipe.hset(f"saga:{saga.id}", mapping={"status": saga.status.value, "doc": json.dumps(to_doc(saga))})
        if saga.status is SagaStatus.RUNNING:
            pipe.sadd(RUNNING, str(saga.id))
        else:
            pipe.srem(RUNNING, str(saga.id))
        pipe.execute()

    def find(self, saga_id: UUID) -> Saga | None:
        doc = self._redis.hget(f"saga:{saga_id}", "doc")
        return from_doc(json.loads(doc)) if doc else None

    def running(self) -> list[Saga]:
        sagas = [self.find(UUID(raw.decode())) for raw in self._redis.smembers(RUNNING)]
        return [s for s in sagas if s is not None]


def to_doc(s: Saga) -> dict:
    return {
        "id": str(s.id),
        "studentId": str(s.student_id),
        "sectionId": str(s.section_id),
        "origin": s.origin.value,
        "confirmationId": str(s.confirmation_id) if s.confirmation_id else None,
        "status": s.status.value,
        "completedSteps": [x.value for x in s.completed_steps],
        "compensatedSteps": [x.value for x in s.compensated_steps],
        "failedStep": s.failed_step.value if s.failed_step else None,
        "reason": s.reason.value if s.reason else None,
        "reservationId": str(s.reservation_id) if s.reservation_id else None,
        "enrollmentId": str(s.enrollment_id) if s.enrollment_id else None,
        "correlationId": s.correlation_id,
        "detail": s.detail,
        "createdAt": s.created_at.isoformat(),
        "updatedAt": s.updated_at.isoformat(),
    }


def from_doc(d: dict) -> Saga:
    opt = lambda value: UUID(value) if value else None  # noqa: E731
    return Saga(
        id=UUID(d["id"]),
        student_id=UUID(d["studentId"]),
        section_id=UUID(d["sectionId"]),
        origin=Origin(d["origin"]),
        confirmation_id=opt(d["confirmationId"]),
        status=SagaStatus(d["status"]),
        completed_steps=tuple(Step(x) for x in d["completedSteps"]),
        failed_step=Step(d["failedStep"]) if d["failedStep"] else None,
        reason=Reason(d["reason"]) if d["reason"] else None,
        reservation_id=opt(d["reservationId"]),
        enrollment_id=opt(d["enrollmentId"]),
        correlation_id=d["correlationId"],
        detail=d["detail"],
        created_at=datetime.fromisoformat(d["createdAt"]),
        updated_at=datetime.fromisoformat(d["updatedAt"]),
        compensated_steps=tuple(Step(x) for x in d["compensatedSteps"]),
    )


class RedisReachable:
    """The ready probe's check of Redis (HU-PLT-004): a PING on a client with a short timeout."""

    name = "redis"

    def __init__(self, client: redis.Redis) -> None:
        self._redis = client

    def reachable(self) -> bool:
        try:
            return bool(self._redis.ping())
        except redis.RedisError:
            return False
