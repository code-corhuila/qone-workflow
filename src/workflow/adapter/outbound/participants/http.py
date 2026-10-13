"""The participants of the saga over HTTP (Annex E): billing, enrollment and catalog called with the
workflow's SERVICE token (never the student's), `Idempotency-Key: <sagaId>:<step>` on every call
that writes, and the caller's X-Correlation-Id. Retries only on network errors, 429 and 5xx,
bounded, with full jitter (Annex D); any other 4xx is a final answer."""

import random
import time
from collections.abc import Callable
from uuid import UUID

import httpx

from workflow.application.port.outbound.participants import Call, Rejected, Unavailable
from workflow.domain.saga.saga import Reason, Saga

RETRYABLE = {429, 500, 502, 503, 504}


class HttpClient:
    def __init__(
        self,
        service_token: str,
        client: httpx.Client | None = None,
        attempts: int = 3,
        base_delay: float = 0.2,
        max_delay: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = service_token
        self._client = client or httpx.Client(timeout=httpx.Timeout(10.0, connect=3.0))
        self._attempts = attempts
        self._base = base_delay
        self._cap = max_delay
        self._sleep = sleep

    def send(self, method: str, url: str, call: Call, json: dict | None = None, key: bool = False) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token}", "X-Correlation-Id": call.correlation_id}
        if key:
            headers["Idempotency-Key"] = call.idempotency_key
        last = "no attempt made"
        for attempt in range(self._attempts):
            try:
                response = self._client.request(method, url, json=json, headers=headers)
            except httpx.TransportError as error:
                last = f"{method} {url}: {type(error).__name__}"
            else:
                if response.status_code not in RETRYABLE:
                    return response
                last = f"{method} {url}: {response.status_code}"
            if attempt + 1 < self._attempts:
                self._sleep(random.uniform(0, min(self._cap, self._base * 2**attempt)))  # noqa: S311 - jitter, not security
        raise Unavailable(last)


def _unexpected(response: httpx.Response) -> Unavailable:
    return Unavailable(f"{response.request.method} {response.request.url}: unexpected {response.status_code}")


class HttpBilling:
    def __init__(self, http: HttpClient, base_url: str) -> None:
        self._http, self._base = http, base_url.rstrip("/")

    def ensure_tuition_paid(self, student_id: UUID, call: Call) -> None:
        r = self._http.send("GET", f"{self._base}/api/v1/billing/students/{student_id}/invoices/current", call)
        if r.status_code == 404 or (r.status_code == 200 and r.json().get("status") != "PAID"):
            raise Rejected(Reason.TUITION_UNPAID)
        if r.status_code != 200:
            raise _unexpected(r)


class HttpEnrollment:
    def __init__(self, http: HttpClient, base_url: str) -> None:
        self._http, self._base = http, f"{base_url.rstrip('/')}/api/v1/enrollment"

    def ensure_eligible(self, student_id: UUID, section_id: UUID, call: Call) -> None:
        r = self._http.send(
            "POST",
            f"{self._base}/eligibility-checks",
            call,
            {"studentId": str(student_id), "sectionId": str(section_id)},
        )
        if r.status_code != 200:
            raise _unexpected(r)
        verdict = r.json()
        if not verdict.get("eligible"):
            raise Rejected(Reason(verdict.get("reason")))

    def record_confirmed(self, saga: Saga, call: Call) -> UUID:
        body = self._body(saga, "CONFIRMED") | {"reservationId": str(saga.reservation_id)}
        r = self._http.send("POST", f"{self._base}/enrollments", call, body, key=True)
        if r.status_code == 422 and r.json().get("message", "").startswith("INV-ENR-001"):
            raise Rejected(Reason.DUPLICATE)
        if r.status_code not in (200, 201):
            raise _unexpected(r)
        return UUID(r.json()["id"])

    def record_rejected(self, saga: Saga, call: Call) -> UUID:
        assert saga.reason is not None
        body = self._body(saga, "REJECTED") | {"rejectionReason": saga.reason.value}
        r = self._http.send("POST", f"{self._base}/enrollments", call, body, key=True)
        if r.status_code not in (200, 201):
            raise _unexpected(r)
        return UUID(r.json()["id"])

    def cancel(self, enrollment_id: UUID, call: Call) -> None:
        r = self._http.send("POST", f"{self._base}/enrollments/{enrollment_id}/cancel", call)
        if r.status_code != 200:
            raise _unexpected(r)

    @staticmethod
    def _body(saga: Saga, outcome: str) -> dict:
        body = {
            "studentId": str(saga.student_id),
            "sectionId": str(saga.section_id),
            "sagaId": str(saga.id),
            "origin": saga.origin.value,
            "outcome": outcome,
        }
        if saga.confirmation_id:
            body["confirmationId"] = str(saga.confirmation_id)
        return body


class HttpCatalog:
    def __init__(self, http: HttpClient, base_url: str) -> None:
        self._http, self._base = http, f"{base_url.rstrip('/')}/api/v1/catalog/sections"

    def reserve(self, saga: Saga, call: Call) -> UUID:
        body = {"studentId": str(saga.student_id), "sagaId": str(saga.id)}
        r = self._http.send("POST", f"{self._base}/{saga.section_id}/reservations", call, body, key=True)
        if r.status_code == 422 and r.json().get("message", "").startswith("NO_SEATS"):
            raise Rejected(Reason.NO_SEATS)
        if r.status_code not in (200, 201):
            raise _unexpected(r)
        return UUID(r.json()["id"])

    def release(self, section_id: UUID, reservation_id: UUID, call: Call) -> None:
        r = self._http.send("DELETE", f"{self._base}/{section_id}/reservations/{reservation_id}", call)
        if r.status_code != 204:
            raise _unexpected(r)

    def commit(self, section_id: UUID, reservation_id: UUID, call: Call) -> None:
        r = self._http.send("POST", f"{self._base}/{section_id}/reservations/{reservation_id}/commit", call)
        if r.status_code != 200:
            raise _unexpected(r)
