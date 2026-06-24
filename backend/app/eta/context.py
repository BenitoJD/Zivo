import uuid
from contextvars import ContextVar, Token

_CurrentJobId = ContextVar[uuid.UUID | None]("eta_current_job_id", default=None)


def _coerce_job_id(job_id: str | uuid.UUID) -> uuid.UUID:
    if isinstance(job_id, uuid.UUID):
        return job_id
    try:
        return uuid.UUID(str(job_id))
    except ValueError as exc:
        raise ValueError(f"Invalid job_id: {job_id}") from exc


def set_current_job_id(job_id: str | uuid.UUID | None) -> Token:
    if job_id is None:
        return _CurrentJobId.set(None)
    return _CurrentJobId.set(_coerce_job_id(job_id))


def reset_current_job_id(token: Token) -> None:
    _CurrentJobId.reset(token)


def get_current_job_id() -> uuid.UUID | None:
    return _CurrentJobId.get()
