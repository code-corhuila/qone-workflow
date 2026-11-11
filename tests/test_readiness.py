"""HU-PLT-004 (code-corhuila/qone-docs#59): `/health/ready` says whether the workflow can take
traffic. Core level: each dependency check is asked, a failing or raising one makes it not
ready. HTTP level: 200 ready, 503 with the common envelope. Adapter: Redis PING down is False."""

import os

import pytest
import redis

from tests.http_support import make_client
from workflow.adapter.outbound.state.redis import RedisReachable
from workflow.application.usecase.readiness import CheckReadiness

READY = "/api/v1/sagas/health/ready"


class Check:
    def __init__(self, name: str, answer) -> None:
        self.name = name
        self.answer = answer

    def reachable(self) -> bool:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_every_check_ok_is_ready():
    result = CheckReadiness([Check("redis", True)]).check()
    assert result.ready and result.checks == {"redis": "ok"} and result.unreachable == []


def test_a_failing_or_raising_check_is_not_ready():
    result = CheckReadiness([Check("redis", False), Check("other", RuntimeError("boom"))]).check()
    assert not result.ready and result.checks == {"redis": "down", "other": "down"}
    assert result.unreachable == ["redis", "other"]


def test_without_dependencies_the_in_memory_runtime_is_ready():
    assert CheckReadiness([]).check().ready


def test_ready_answers_200_with_the_checks():
    client, _ = make_client()
    r = client.get(READY)
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "service": "qone-workflow", "checks": {}}


def test_ready_answers_503_with_the_envelope_when_redis_is_down():
    client, _ = make_client(readiness=CheckReadiness([Check("redis", False)]))
    r = client.get(READY, headers={"X-Correlation-Id": "ready-probe-1"})
    assert r.status_code == 503
    assert r.json() == {"error": "SERVICE_UNAVAILABLE", "message": "redis is not reachable", "traceId": "ready-probe-1"}
    assert client.get("/api/v1/sagas/health").status_code == 200, "liveness does not change"


def test_redis_ping_against_a_closed_port_is_not_reachable():
    client = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2, socket_timeout=0.2)
    check = RedisReachable(client)
    assert check.name == "redis" and check.reachable() is False


@pytest.mark.skipif(not os.environ.get("TEST_REDIS_URL"), reason="TEST_REDIS_URL not set")
def test_redis_ping_against_a_real_redis_is_reachable():
    assert RedisReachable(redis.Redis.from_url(os.environ["TEST_REDIS_URL"], socket_timeout=1)).reachable() is True
