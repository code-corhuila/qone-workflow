"""The Redis store: the document round trip always; the store itself against a real Redis when
TEST_REDIS_URL is set (skipped, not failed, without it)."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from workflow.adapter.outbound.state.redis import from_doc, to_doc
from workflow.domain.saga.saga import Origin, Reason, Saga, SagaStatus, Step

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def a_saga():
    s = Saga.start(uuid4(), uuid4(), uuid4(), Origin.AGENT, uuid4(), "corr-1", NOW)
    s = (
        s.step_done(Step.CHECK_TUITION, NOW)
        .step_done(Step.CHECK_ELIGIBILITY, NOW)
        .step_done(Step.RESERVE_SEAT, NOW, reservation_id=uuid4())
    )
    return s.step_failed(Step.CONFIRM_ENROLLMENT, Reason.DUPLICATE, "x", NOW).undone(Step.RESERVE_SEAT, NOW)


def test_the_document_keeps_every_field():
    saga = a_saga()
    assert from_doc(to_doc(saga)) == saga


URL = os.environ.get("TEST_REDIS_URL", "")


@pytest.mark.skipif(not URL, reason="TEST_REDIS_URL not set")
def test_the_store_against_redis():
    import redis

    from workflow.adapter.outbound.state.redis import RedisSagaStore

    store = RedisSagaStore(redis.Redis.from_url(URL))
    assert store.ping()
    saga = a_saga()
    key = f"it-{uuid4()}"
    assert store.create(saga, key) == (saga, True)
    other = Saga.start(uuid4(), uuid4(), uuid4(), Origin.UI, None, "corr-2", NOW)
    assert store.create(other, key) == (saga, False), "the first start wins"
    assert store.find(saga.id) == saga and saga.id in {s.id for s in store.running()}
    done = saga.finish(NOW)
    store.save(done)
    assert store.find(saga.id).status is SagaStatus.COMPENSATED and saga.id not in {s.id for s in store.running()}
