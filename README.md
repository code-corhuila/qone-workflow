# qone-workflow

> Cross-domain processes: the enroll-student saga

Part of the **Qampus (Quorum One)** distributed system — team `quorum-one`, Grupo 1.
Governance and documentation live in [`qone-docs`](https://github.com/code-corhuila/qone-docs).

## Purpose

The orchestrator of the **enroll-student** saga (course norm 5.8, Annex E, ADR-008): the only way
an enrollment is written. Five steps, each with its compensation, the state persisted after every
step in Redis, an idempotent start. Python 3.12 + FastAPI, hexagonal with one port per participant;
the domain and the use case import no framework, HTTP client or Redis client (a test enforces it).

| Step | Participant | Compensation |
|---|---|---|
| `check-tuition` | billing BI-06: the current invoice must be `PAID` | none (read) |
| `check-eligibility` | enrollment EN-04: duplicate, prerequisites, schedule | none (read) |
| `reserve-seat` | catalog CA-10 | catalog CA-11, release |
| `confirm-enrollment` | enrollment EN-05, `CONFIRMED` with the reservation | enrollment EN-06, cancel |
| `commit-seat` | catalog CA-12 | last step, nothing after it to undo |

A rejected gate ends `COMPENSATED` with `failedStep` and the domain `reason` (and the enrollment
domain records the rejected attempt, D-01); a participant that does not answer (network,
timeout, a 5xx after the retries) ends `COMPENSATED` with `reason: PARTICIPANT_UNAVAILABLE`, not
recorded as a rejection (HU-RES-001); a failed compensation ends `FAILED` for a person to decide. Every step call carries the workflow's `SERVICE_TOKEN`, `Idempotency-Key: <sagaId>:<step>`
and the caller's `X-Correlation-Id`.

- Contract: `07-api/contracts/openapi/qone-workflow.yaml` (WF-01..WF-03) in `qone-docs`.
- Piece page: `09-microservices/services/07-workflow/README.md`. Story: `code-corhuila/qone-docs#42` (HU-ENR-002).

Who starts it (`code-corhuila/qone-docs#55`, D-08): a STUDENT for themselves with `origin: UI`, or
the SERVICE token whose subject is `qone-advisor-api` with `origin: AGENT` and the `confirmationId`;
any other SERVICE subject gets `403`. The `check-tuition` gate passes only on an invoice `PAID`
with its `providerReference` (the provider's evidence); `PAID` without it is `TUITION_UNPAID`.

## How to run it

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\\Scripts\\activate
pip install -e ".[dev]"
ruff check .
python -m pytest                                   # add TEST_REDIS_URL=redis://localhost:6379/0 for the Redis tests
cp .env.example .env                               # REDIS_URL, JWT_PUBLIC_KEY, SERVICE_TOKEN, participant URLs
python -m apps.api                                 # http://localhost:8000/api/v1/sagas/health
```

Quality gates of CI (HU-PLT-005): the tests run with `pytest --cov=src --cov-fail-under=90` (the
**coverage floor is 90 %** of `src/`, measured 90.4 % without Redis when it was set; raise it, never
lower it), `pip-audit --skip-editable` fails on a known vulnerability, and gitleaks scans the whole
history for secrets (`.gitleaks.toml` allowlists `tests/`, whose keys and tokens are fake
fixtures).

Without `REDIS_URL` the sagas live in memory and a restart loses them (development only; the
log says so). At start the service resumes every saga a restart left `RUNNING`: the idempotent
step keys make a repeated step harmless. Health answers `503` when Redis does not.

Metrics (HU-OBS-001, ADR-010): `GET /metrics` at the root of the app (not under `/api/v1/sagas`:
the gateway never routes it; Prometheus scrapes `http://qone-workflow:8000/metrics` on the platform
network). It exposes `http_server_requests_total{service,method,route,status_class}`,
`http_server_request_duration_seconds{service,method,route}` (route = the matched template or
`unmatched`) and, for every saga that ends, `workflow_saga_requests_total{saga,outcome}` and
`workflow_saga_duration_seconds{saga,outcome}` (outcome `completed`, `compensated` or `failed`).

Ready probe (HU-PLT-004, ADR-011): `GET /api/v1/sagas/health/ready`, public like `/health`, PINGs
Redis with a 1 s timeout and answers `200 {"status":"ready","service":"qone-workflow","checks":{"redis":"ok"}}`
or `503` with the common envelope (`SERVICE_UNAVAILABLE`, `redis is not reachable`). In memory there
is nothing to wait for: it answers ready with no checks. `/health` stays the liveness.

Container image and compose (`deploy/`, Annex G): `docker build -f deploy/Dockerfile -t
qone-workflow:dev .`, then `deploy/smoke.sh`; `deploy/compose.yml` is what `qone-infra` includes,
next to the `redis:7` service it composes with `appendonly yes` (ADR-008). Limits: three attempts
per call with full jitter, 3 s to connect and 10 s to answer per call, keep-alive 60 s, graceful
shutdown 20 s; the request budget of 30 s is the gateway's.

## Branching

Three permanent branches. **None of them accepts a direct commit** — you enter through a child
branch and leave through a Pull Request.

```
develop  <--PR--  feat/... fix/... chore/...
qa       <--PR--  qa/...
main     <--PR--  release/...  hotfix/...
```

Promotion happens **by re-application** (`git cherry-pick -x`), never by merging one permanent
branch into another: `merge develop -> qa` and `merge qa -> main` do not exist in this model.

`main` requires **1 approval from `ariel5253`**. On `develop` and `qa` the team sets its own review
rule.

Full policy: `00-governance/branching-policy.md` in `qone-docs`.
