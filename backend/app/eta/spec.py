from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.models import JobPriority, JobWorkload


@dataclass(frozen=True)
class EtaDagNode:
    key: str
    name: str
    payload: Any
    workload: JobWorkload | None = None
    priority: JobPriority | None = None
    run_after: datetime | None = None


@dataclass(frozen=True)
class EtaDagEdge:
    source: str
    target: str


@dataclass(frozen=True)
class EtaDagSpec:
    name: str
    nodes: list[EtaDagNode]
    edges: list[EtaDagEdge] = field(default_factory=list)
    priority: JobPriority | None = None
