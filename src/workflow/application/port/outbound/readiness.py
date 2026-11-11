"""What the ready probe asks (HU-PLT-004, ADR-011): one check per dependency the workflow needs to
take traffic. The adapter answers within a short timeout; any failure is `False`, never a hang."""

from typing import Protocol


class DependencyCheck(Protocol):
    name: str

    def reachable(self) -> bool:
        """True when the dependency answers now (Redis: PING)."""
