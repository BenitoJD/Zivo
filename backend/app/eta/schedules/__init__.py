# Import schedule modules here so @eta_scheduler decorators register at startup.
from app.eta.schedules import item_retirement  # noqa: F401
from app.eta.schedules import newspaper  # noqa: F401
from app.eta.schedules import seo  # noqa: F401
