# Import schedule modules here so @eta_scheduler decorators register at startup.
from app.eta.schedules import cache_purge  # noqa: F401
