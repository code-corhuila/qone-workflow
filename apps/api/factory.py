"""The composition root (Annex C, Annex E): the only module that knows every concrete type and
every limit. It reads the environment, builds the participants, the saga store and the
orchestrator, and resumes the sagas a restart left RUNNING."""

import json
import logging
import os
import sys
import threading

from workflow.adapter.inbound.http.app import create_app
from workflow.adapter.inbound.http.auth import TokenVerifier
from workflow.adapter.outbound.participants.http import HttpBilling, HttpCatalog, HttpClient, HttpEnrollment
from workflow.adapter.outbound.runtime import SystemClock, Uuid4Generator
from workflow.adapter.outbound.state.memory import InMemorySagaStore
from workflow.application.usecase.enroll_student import EnrollStudentSaga

# Explicit limits (norm 5.3.10, Annex D): bounded retries per call, server limits.
CALL_ATTEMPTS = 3
HTTP_KEEP_ALIVE_SECONDS = 60
HTTP_GRACEFUL_SHUTDOWN_SECONDS = 20


class Settings:
    def __init__(self, env: dict[str, str] | None = None) -> None:
        env = os.environ if env is None else env
        self.redis_url = env.get("REDIS_URL", "").strip()
        self.public_key = _public_key(env)
        self.service_token = env.get("SERVICE_TOKEN", "").strip()
        if not self.service_token:
            raise SystemExit("SERVICE_TOKEN is required: participants are called with the workflow's own token")
        self.billing_url = env.get("BILLING_API_URL", "http://qone-billing-api:8000")
        self.enrollment_url = env.get("ENROLLMENT_API_URL", "http://qone-enrollment-api:8000")
        self.catalog_url = env.get("CATALOG_API_URL", "http://qone-catalog-api:8000")
        self.port = int(env.get("PORT", "8000"))
        self.log_level = env.get("LOG_LEVEL", "info").lower()


def _public_key(env: dict[str, str]) -> str:
    inline = env.get("JWT_PUBLIC_KEY", "").strip()
    if inline:
        return inline.replace("\\n", "\n")
    path = env.get("JWT_PUBLIC_KEY_FILE", "").strip()
    if path:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    raise SystemExit("JWT_PUBLIC_KEY or JWT_PUBLIC_KEY_FILE is required: the workflow validates every token itself")


def build_app(settings: Settings | None = None):
    settings = settings or Settings()
    configure_logging(settings.log_level)
    log = logging.getLogger("workflow")
    if settings.redis_url:
        import redis

        from workflow.adapter.outbound.state.redis import RedisSagaStore

        store = RedisSagaStore(redis.Redis.from_url(settings.redis_url, socket_timeout=3, socket_connect_timeout=3))
        ping = store.ping
        log.info("saga store: redis")
    else:
        store, ping = InMemorySagaStore(), lambda: True
        log.warning("saga store: in memory (REDIS_URL not set); a restart loses every saga; development only")
    http = HttpClient(settings.service_token, attempts=CALL_ATTEMPTS)
    saga = EnrollStudentSaga(
        store,
        HttpBilling(http, settings.billing_url),
        HttpEnrollment(http, settings.enrollment_url),
        HttpCatalog(http, settings.catalog_url),
        SystemClock(),
        Uuid4Generator(),
    )
    threading.Thread(target=_resume, args=(saga, log), daemon=True).start()
    return create_app(saga, TokenVerifier(settings.public_key), store_ping=ping)


def _resume(saga: EnrollStudentSaga, log: logging.Logger) -> None:
    try:
        resumed = saga.resume_running()
        if resumed:
            log.warning("resumed %s saga(s) left RUNNING by a restart", resumed)
    except Exception:  # noqa: BLE001 - a failed resume must not stop the service; the next start retries
        log.exception("resume of running sagas failed")


class JsonFormatter(logging.Formatter):
    """One JSON line per record with the traceId (norm 5.3.9)."""

    def format(self, record: logging.LogRecord) -> str:
        line = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("traceId", "method", "path", "status", "durationMs", "step", "detail"):
            if hasattr(record, key):
                line[key] = getattr(record, key)
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
