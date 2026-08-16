from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.engine_runtime import pick
from app.eta.execution_state import update_execution_state_sync
from app.eta.spec import EtaDagNode, EtaDagSpec
from app.eta.submit import build_job, eta_session
from app.models import (
    EtaExecution,
    EtaExecutionMode,
    EtaExecutionStatus,
    EtaJobDependency,
    Job,
    JobPriority,
    JobStatus,
    JobWorkload,
)


class EtaDagValidationError(ValueError):
    pass


def _reject(cond: bool, message: str) -> None:
    def _raise() -> None:
        raise EtaDagValidationError(message)

    pick(cond, _raise, lambda: None)


def _validate_dag(spec: EtaDagSpec) -> None:
    _reject(not spec.name.strip(), "Execution name is required")
    _reject(not spec.nodes, "At least one node is required")

    keys = [node.key.strip() for node in spec.nodes]
    _reject(any(not key for key in keys), "Node keys must be non-empty")
    _reject(len(set(keys)) != len(keys), "Node keys must be unique")

    node_key_set = set(keys)
    indegree: dict[str, int] = {key: 0 for key in node_key_set}
    adjacency: dict[str, list[str]] = {key: [] for key in node_key_set}

    def _check_edge(edge) -> None:
        _reject(edge.source not in node_key_set, f"Edge source not found: {edge.source}")
        _reject(edge.target not in node_key_set, f"Edge target not found: {edge.target}")
        _reject(edge.source == edge.target, f"Self dependency is not allowed: {edge.source}")
        adjacency[edge.source].append(edge.target)
        indegree[edge.target] += 1

    for edge in spec.edges:
        _check_edge(edge)

    queue = deque(
        map(
            lambda item: item[0],
            filter(lambda item: item[1] == 0, indegree.items()),
        )
    )
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1

        def _dec(child: str) -> None:
            indegree[child] -= 1
            pick(indegree[child] == 0, lambda: queue.append(child), lambda: None)

        for child in adjacency[node]:
            _dec(child)

    _reject(visited != len(node_key_set), "Cycle detected in DAG")


def _merge_parent_job_ids(payload: Any, parent_job_ids: Sequence[str]) -> dict[str, Any]:
    _reject(not isinstance(payload, dict), "Node payload must normalize to a JSON object")
    merged = dict(payload)
    existing = merged.get("parent_job_ids") or []
    _reject(
        bool(existing) and not isinstance(existing, list),
        "Node payload field 'parent_job_ids' must be a list",
    )
    combined = sorted({str(job_id) for job_id in [*existing, *parent_job_ids]})
    pick(bool(combined), lambda: merged.__setitem__("parent_job_ids", combined), lambda: None)
    return merged


def _create_execution(
    db: Session,
    *,
    name: str,
    mode: EtaExecutionMode,
    account_id: Any,
    priority: JobPriority,
) -> EtaExecution:
    execution = EtaExecution(
        name=name.strip(),
        mode=mode,
        status=EtaExecutionStatus.queued,
        account_id=account_id,
        priority=priority,
    )
    db.add(execution)
    db.flush()
    return execution


def submit_dag(
    db: Session | None = None,
    *,
    spec: EtaDagSpec,
    account_id: Any = None,
    mode: EtaExecutionMode = EtaExecutionMode.dag,
) -> EtaExecution:
    _validate_dag(spec)

    with eta_session(db) as active_db:
        execution = _create_execution(
            active_db,
            name=spec.name,
            mode=mode,
            account_id=account_id,
            priority=spec.priority or JobPriority.MEDIUM,
        )

        jobs_by_key: dict[str, Job] = {}
        for node in spec.nodes:
            job = build_job(
                name=node.name,
                payload=node.payload,
                account_id=account_id,
                workload=node.workload,
                priority=node.priority or spec.priority,
                run_after=node.run_after,
                execution_id=execution.id,
                node_key=node.key,
            )
            active_db.add(job)
            jobs_by_key[node.key] = job

        active_db.flush()

        dependencies: list[EtaJobDependency] = []
        parent_ids_by_job_id: dict[object, list[str]] = defaultdict(list)

        for edge in spec.edges:
            source_job = jobs_by_key[edge.source]
            target_job = jobs_by_key[edge.target]
            dependencies.append(
                EtaJobDependency(
                    job_id=target_job.id,
                    depends_on_job_id=source_job.id,
                )
            )
            parent_ids_by_job_id[target_job.id].append(str(source_job.id))

        pick(bool(dependencies), lambda: active_db.add_all(dependencies), lambda: None)

        def _attach_parents(job: Job) -> None:
            parent_ids = parent_ids_by_job_id.get(job.id, [])
            pick(
                bool(parent_ids),
                lambda: setattr(job, "payload", _merge_parent_job_ids(job.payload, parent_ids)),
                lambda: None,
            )

        for job in jobs_by_key.values():
            _attach_parents(job)

        execution.total_jobs = len(spec.nodes)
        execution.queued_jobs = len(spec.nodes)
        execution.running_jobs = 0
        execution.succeeded_jobs = 0
        execution.failed_jobs = 0
        execution.cancelled_jobs = 0
        execution.status = EtaExecutionStatus.queued

        active_db.commit()
        active_db.refresh(execution)
        return execution


def submit_group(
    db: Session | None = None,
    *,
    name: str,
    job_name: str,
    items: Iterable[Any],
    account_id: Any = None,
    priority: JobPriority = JobPriority.MEDIUM,
    workload: JobWorkload | None = None,
) -> EtaExecution:
    nodes = [
        EtaDagNode(
            key=f"item-{idx}",
            name=job_name,
            payload=item,
            workload=workload,
            priority=priority,
        )
        for idx, item in enumerate(items)
    ]
    _reject(not nodes, "Group submission requires at least one item")
    spec = EtaDagSpec(
        name=name,
        nodes=nodes,
        edges=[],
        priority=priority,
    )
    return submit_dag(db, spec=spec, account_id=account_id, mode=EtaExecutionMode.group)


def _scoped_query(query, account_id: Any, column):
    return pick(
        account_id is not None,
        lambda: query.filter(column == account_id),
        lambda: query,
    )


def get_execution(
    db: Session, execution_id: object, account_id: Any = None
) -> EtaExecution | None:
    query = db.query(EtaExecution).filter(EtaExecution.id == execution_id)
    return _scoped_query(query, account_id, EtaExecution.account_id).first()


def list_execution_jobs(db: Session, execution_id: object, account_id: Any = None) -> list[Job]:
    query = db.query(Job).filter(Job.execution_id == execution_id).order_by(Job.created_at.asc())
    return _scoped_query(query, account_id, Job.account_id).all()


def cancel_execution(
    db: Session, execution_id: object, account_id: Any = None
) -> EtaExecution | None:
    execution = get_execution(db, execution_id, account_id=account_id)

    def _cancel(active: EtaExecution) -> EtaExecution:
        db.query(Job).filter(
            Job.execution_id == active.id,
            Job.status.in_([JobStatus.queued, JobStatus.running]),
        ).update(
            {
                Job.status: JobStatus.cancelled,
                Job.error: "Execution cancelled",
                Job.locked_by: None,
                Job.locked_at: None,
            },
            synchronize_session=False,
        )
        update_execution_state_sync(db, active.id)
        db.commit()
        db.refresh(active)
        return active

    return pick(execution is None, lambda: None, lambda: _cancel(execution))


def retry_failed_jobs(
    db: Session, execution_id: object, account_id: Any = None
) -> tuple[EtaExecution, int] | None:
    execution = get_execution(db, execution_id, account_id=account_id)

    def _retry(active: EtaExecution) -> tuple[EtaExecution, int]:
        updated = (
            db.query(Job)
            .filter(
                Job.execution_id == active.id,
                Job.status == JobStatus.failed,
            )
            .update(
                {
                    Job.status: JobStatus.queued,
                    Job.error: None,
                    Job.run_after: None,
                    Job.locked_at: None,
                    Job.locked_by: None,
                    Job.attempts: 0,
                },
                synchronize_session=False,
            )
        )
        update_execution_state_sync(db, active.id)
        db.commit()
        db.refresh(active)
        return active, updated

    return pick(execution is None, lambda: None, lambda: _retry(execution))
