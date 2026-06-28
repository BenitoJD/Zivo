"""Unit tests for DAG spec validation and cycle detection (no DB required)."""

import pytest

from app.eta.executions import EtaDagValidationError, _validate_dag
from app.eta.spec import EtaDagEdge, EtaDagNode, EtaDagSpec


def _node(key: str) -> EtaDagNode:
    return EtaDagNode(key=key, name="test.noop", payload={})


def test_valid_linear_dag_passes() -> None:
    spec = EtaDagSpec(
        name="linear",
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[EtaDagEdge("a", "b"), EtaDagEdge("b", "c")],
    )
    _validate_dag(spec)  # should not raise


def test_diamond_dag_passes() -> None:
    spec = EtaDagSpec(
        name="diamond",
        nodes=[_node("a"), _node("b"), _node("c"), _node("d")],
        edges=[EtaDagEdge("a", "b"), EtaDagEdge("a", "c"), EtaDagEdge("b", "d"), EtaDagEdge("c", "d")],
    )
    _validate_dag(spec)


def test_empty_name_rejected() -> None:
    with pytest.raises(EtaDagValidationError, match="name"):
        _validate_dag(EtaDagSpec(name="  ", nodes=[_node("a")]))


def test_no_nodes_rejected() -> None:
    with pytest.raises(EtaDagValidationError, match="node"):
        _validate_dag(EtaDagSpec(name="x", nodes=[]))


def test_duplicate_keys_rejected() -> None:
    with pytest.raises(EtaDagValidationError, match="unique"):
        _validate_dag(EtaDagSpec(name="x", nodes=[_node("a"), _node("a")]))


def test_self_dependency_rejected() -> None:
    with pytest.raises(EtaDagValidationError, match="(?i)self"):
        _validate_dag(EtaDagSpec(name="x", nodes=[_node("a")], edges=[EtaDagEdge("a", "a")]))


def test_unknown_edge_source_rejected() -> None:
    with pytest.raises(EtaDagValidationError, match="source"):
        _validate_dag(EtaDagSpec(name="x", nodes=[_node("a")], edges=[EtaDagEdge("z", "a")]))


def test_unknown_edge_target_rejected() -> None:
    with pytest.raises(EtaDagValidationError, match="target"):
        _validate_dag(EtaDagSpec(name="x", nodes=[_node("a")], edges=[EtaDagEdge("a", "z")]))


def test_cycle_rejected() -> None:
    with pytest.raises(EtaDagValidationError, match="Cycle"):
        _validate_dag(
            EtaDagSpec(
                name="cyclic",
                nodes=[_node("x"), _node("y")],
                edges=[EtaDagEdge("x", "y"), EtaDagEdge("y", "x")],
            )
        )


def test_larger_cycle_rejected() -> None:
    with pytest.raises(EtaDagValidationError, match="Cycle"):
        _validate_dag(
            EtaDagSpec(
                name="cyclic3",
                nodes=[_node("a"), _node("b"), _node("c")],
                edges=[EtaDagEdge("a", "b"), EtaDagEdge("b", "c"), EtaDagEdge("c", "a")],
            )
        )
