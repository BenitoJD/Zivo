"""Job Lifecycle Engine — stale / retry / reclaim / lease / keep.

Version: qb.job_lifecycle.v1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.engine_runtime import Pred, Rule, first_match

JOB_LIFECYCLE_VERSION = "qb.job_lifecycle.v1"
DEFAULT_JOB_LIFECYCLE_POLICY = "job_lifecycle_v1"
JobLifeAction = Literal["keep", "reclaim", "retry", "fail"]

_RULES = (
    Rule(when=(Pred("status", "eq", "running"), Pred("stale_running", "truthy")), action="reclaim"),
    Rule(when=(Pred("status", "eq", "queued"), Pred("stale_queued", "truthy")), action="fail"),
    Rule(when=(Pred("status", "eq", "failed"), Pred("retries_left", "truthy")), action="retry"),
    Rule(when=(Pred("status", "eq", "failed"),), action="fail"),
    Rule(when=(), action="keep"),
)


@dataclass(frozen=True)
class JobLifecycleVerdict:
    action: JobLifeAction
    policy: str = DEFAULT_JOB_LIFECYCLE_POLICY
    policy_version: str = JOB_LIFECYCLE_VERSION


def evaluate_job_lifecycle(
    *,
    status: str,
    stale_running: bool = False,
    stale_queued: bool = False,
    retries_left: bool = False,
    policy: str | None = None,
) -> JobLifecycleVerdict:
    signals = {
        "status": status,
        "stale_running": stale_running,
        "stale_queued": stale_queued,
        "retries_left": retries_left,
    }
    hit = first_match(_RULES, signals)
    return JobLifecycleVerdict(action=hit.action, policy=policy or DEFAULT_JOB_LIFECYCLE_POLICY)
