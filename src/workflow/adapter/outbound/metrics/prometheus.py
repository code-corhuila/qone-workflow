"""The saga metrics port on Prometheus (HU-OBS-001, ADR-010): a counter and a histogram per saga
and outcome, registered in the registry of the app that `GET /metrics` exposes."""

from prometheus_client import CollectorRegistry, Counter, Histogram

# The same buckets as the HTTP latency: the saga's SLO (p95) is read against them.
LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10)


class PrometheusSagaMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self._requests = Counter(
            "workflow_saga_requests_total",
            "Sagas that ended, by saga and outcome (completed, compensated, failed)",
            ["saga", "outcome"],
            registry=registry,
        )
        self._duration = Histogram(
            "workflow_saga_duration_seconds",
            "Seconds from the start of a saga to its end, by saga and outcome",
            ["saga", "outcome"],
            buckets=LATENCY_BUCKETS,
            registry=registry,
        )

    def saga_ended(self, saga: str, outcome: str, seconds: float) -> None:
        self._requests.labels(saga=saga, outcome=outcome).inc()
        self._duration.labels(saga=saga, outcome=outcome).observe(max(seconds, 0.0))
