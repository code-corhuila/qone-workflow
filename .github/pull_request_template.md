## User story

code-corhuila/qone-docs#NN

## What changes and why

## How it was tested

- [ ] `ci.yml` green: ruff, pytest (domain, use cases, HTTP with the in-memory adapters, architecture)
- [ ] Integration tests against Redis run locally with `TEST_REDIS_URL`

## Promotion trail

Only towards `qa` or `main`: the re-applied commits, each with its `(cherry picked from commit <sha>)` line.

## Checklist

- [ ] No secrets, no `.env`, no `*.pem`
- [ ] Contract `07-api/contracts/openapi/qone-workflow.yaml` respected
- [ ] `domain/` and `application/` import no framework, HTTP client or driver
