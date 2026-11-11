"""What the orchestrator reports about itself (HU-OBS-001, ADR-010): every saga that ends, with
its outcome and its duration. The port keeps the use case free of the metrics library; the
running workflow plugs in Prometheus, a test plugs in a recorder."""

from typing import Protocol


class SagaMetrics(Protocol):
    def saga_ended(self, saga: str, outcome: str, seconds: float) -> None:
        """One ended saga: its type (`enroll-student`), `completed`, `compensated` or `failed`, and
        the seconds from its start to its end."""


class NoMetrics:
    """Nothing is reported: the default when no metrics port is given."""

    def saga_ended(self, saga: str, outcome: str, seconds: float) -> None:
        return None
