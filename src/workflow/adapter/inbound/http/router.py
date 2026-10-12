"""Routes of WF-01..WF-03 (07-api/contracts/openapi/qone-workflow.yaml), validation at the border
and the representation of a saga. The internal detail of a failure is never serialized (Annex E)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictStr

from workflow.adapter.inbound.http.errors import BorderError
from workflow.application.port.inbound.sagas import Caller, SagaUseCases, StartEnrollStudent
from workflow.domain.saga.saga import Origin, Saga

PREFIX = "/api/v1/sagas"
router = APIRouter(prefix=PREFIX)


class StartBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    studentId: StrictStr  # noqa: N815 - the contract's camelCase
    sectionId: StrictStr  # noqa: N815
    origin: StrictStr
    confirmationId: StrictStr | None = None  # noqa: N815


def use_cases(request: Request) -> SagaUseCases:
    return request.app.state.use_cases


def caller(request: Request) -> Caller:
    return request.app.state.verifier.verify(request.headers.get("Authorization"))


UseCases = Annotated[SagaUseCases, Depends(use_cases)]
Current = Annotated[Caller, Depends(caller)]


@router.get("/health")
def health(request: Request):
    if not request.app.state.store_ping():
        return JSONResponse(
            {
                "error": "SERVICE_UNAVAILABLE",
                "message": "the saga store is not available",
                "traceId": request.state.correlation_id,
            },
            status_code=503,
        )
    return {"status": "ok", "service": request.app.state.service, "version": request.app.state.version}


@router.post("/enroll-student")
def start(body: StartBody, who: Current, service: UseCases, request: Request):
    details: list[tuple[str, str]] = []
    key = request.headers.get("Idempotency-Key", "")
    if not 8 <= len(key) <= 128:
        details.append(("Idempotency-Key", "header required, 8 to 128 characters"))
    ids: dict[str, UUID | None] = {}
    for field, value in (
        ("studentId", body.studentId),
        ("sectionId", body.sectionId),
        ("confirmationId", body.confirmationId),
    ):
        if value is None:
            ids[field] = None
            continue
        try:
            ids[field] = UUID(value)
        except ValueError:
            details.append((field, "must be a UUID"))
    try:
        origin = Origin(body.origin)
    except ValueError:
        details.append(("origin", "must be one of UI, AGENT"))
    if details:
        raise BorderError(details)
    command = StartEnrollStudent(ids["studentId"], ids["sectionId"], origin, ids["confirmationId"])
    result = service.start(who, command, key, request.state.correlation_id)
    if not result.created:
        return JSONResponse(represent(result.saga), status_code=200)
    return JSONResponse(represent(result.saga), status_code=201, headers={"Location": f"{PREFIX}/{result.saga.id}"})


@router.get("/{saga_id}")
def get_saga(saga_id: str, who: Current, service: UseCases):
    try:
        parsed = UUID(saga_id)
    except ValueError:
        raise BorderError([("id", "must be a UUID")]) from None
    return represent(service.get(who, parsed))


def rfc3339(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def represent(saga: Saga) -> dict:
    return {
        "id": str(saga.id),
        "type": saga.type,
        "studentId": str(saga.student_id),
        "sectionId": str(saga.section_id),
        "origin": saga.origin.value,
        "confirmationId": str(saga.confirmation_id) if saga.confirmation_id else None,
        "status": saga.status.value,
        "completedSteps": [s.value for s in saga.completed_steps],
        "failedStep": saga.failed_step.value if saga.failed_step else None,
        "reason": saga.reason.value if saga.reason else None,
        "reservationId": str(saga.reservation_id) if saga.reservation_id else None,
        "enrollmentId": str(saga.enrollment_id) if saga.enrollment_id else None,
        "createdAt": rfc3339(saga.created_at),
        "updatedAt": rfc3339(saga.updated_at),
    }
