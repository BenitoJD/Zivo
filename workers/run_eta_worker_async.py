from app.engine_runtime import pick
from app.eta.worker_async import run_eta_worker_async_entrypoint

pick(__name__ == "__main__", run_eta_worker_async_entrypoint, lambda: None)
