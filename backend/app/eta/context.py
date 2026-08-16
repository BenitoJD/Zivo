import uuid
from contextvars import ContextVar, Token

from app.engine_runtime import pick

_CurrentJobId = ContextVar[uuid.UUID | None]("eta_current_job_id", default=None)


def _coerce_job_id(job_id: str | uuid.UUID) -> uuid.UUID:
    def _from_str() -> uuid.UUID:
        try:
            return uuid.UUID(str(job_id))
        except ValueError as exc:
            raise ValueError(f"Invalid job_id: {job_id}") from exc

    return pick(isinstance(job_id, uuid.UUID), lambda: job_id, _from_str)


def set_current_job_id(job_id: str | uuid.UUID | None) -> Token:
    return pick(
        job_id is None,
        lambda: _CurrentJobId.set(None),
        lambda: _CurrentJobId.set(_coerce_job_id(job_id)),
    )


def reset_current_job_id(token: Token) -> None:
    _CurrentJobId.reset(token)


def get_current_job_id() -> uuid.UUID | None:
    return _CurrentJobId.get()
