from typing import Any, Generator

import numpy as np
from qkit.measure.unified_measurements import Experiment, ASTNode, Sweep, Axis, MeasurementTypeAdapter

from dokuwiki_autodoc.autodoc import QkitDocumentationBuilder, AutoDocumentation

"""
Visitor-Pattern based documentation adapter for unified measurements.

Entrypoint is the `generate_documentation` function.

Internally, this is based on generators and deferred yielding.
"""

def generate_summary(experiment: Experiment) -> str:
    return "\n".join(_visit_experiment(experiment))

def derive_table_slug(experiment: Experiment) -> str:
    name = experiment._name
    return name.lower().replace(" ", "_")

def create_table_entry(tb: QkitDocumentationBuilder._TableBuilder, experiment: Experiment, setpoints: dict[str, tuple[float, str]]):
    # The builder already adds UUID and date
    tb.add_column("Type", lambda _: experiment._name)
    name_unique_axes = {axes.name: axes for axes in _collect_axes(experiment)}
    for _, axes in name_unique_axes.items():
        if len(axes.range) == 1:
            tb.add_column(f"{axes.name} [{axes.unit}]", lambda _: f"{axes.range[0]:.4e}")
        else:
            # This axis is a proper range and needs start, stop and step count
            tb.add_column(f"{axes.name} Start [{axes.unit}]", lambda _: f"{np.min(axes.range):.4e}")
            tb.add_column(f"{axes.name} Stop [{axes.unit}]", lambda _: f"{np.max(axes.range):.4e}")
            tb.add_column(f"{axes.name} Steps", lambda _: f"{len(axes.range)}")
    for variable, (value, unit) in setpoints.items():
        tb.add_column(f"{variable} [{unit}]", lambda _: f"{value:.4e}")

def generate_sweep_summary(experiment: Experiment) -> str:
    return "\n".join(_collect_sweep_summaries(experiment))

def generate_setpoint_summary(setpoint: dict[str, tuple[float, str]]) -> str:
    return "\n".join(_build_setpoint(setpoint))

def _build_setpoint(setpoint: dict[str, tuple[float, str]]) -> Generator[str, Any, None]:
    for variable, (value, unit) in setpoint.items():
        yield _list_item(f"{variable} ({value:.4e} {unit})")

def _collect_sweep_summaries(experiment: Experiment) -> Generator[str, Any, None]:
    for axes in list(_collect_axes(experiment)):
        yield from _build_sweep_summary(axes)

def _build_sweep_summary(axis: Axis) -> Generator[str, Any, None]:
    yield _heading(f"Sweep: {axis.name}", level=1)
    yield _list_item(f"Start: {np.min(axis.range)} {axis.unit}")
    yield _list_item(f"Stop: {np.max(axis.range)} {axis.unit}")
    yield _list_item(f"Steps: {len(axis.range)}")


def _collect_axes(node: ASTNode):
    if isinstance(node, Sweep):
        yield node._axis
    if isinstance(node, MeasurementTypeAdapter):
        for es in node.expected_structure:
            yield from es.axes
    else:
        for child in node._children:
            yield from _collect_axes(child)

def _heading(text: str, level: int):
    assert 0 <= level <= 5, "Heading level must be between 0 and 5."
    heding_tokens = 5 - level
    return "=" * heding_tokens + " " + text + " " + "=" * heding_tokens

def _enumeration_item(text: str, level: int = 1):
    return "  " * level + "- " + text

def _list_item(text: str, level: int = 1):
    return "  " * level + "* " + text


def _visit_ast_node(node: ASTNode, list_depth: int = 0):
    for child in node._children:
        if isinstance(child, MeasurementTypeAdapter):
            yield _enumeration_item(f"{type(child).__name__}", level=list_depth)
        elif isinstance(child, Sweep):
            axis = child._axis
            sweep_description = f"{axis.name} ({np.min(axis.range)} {axis.unit} -> {np.max(axis.range)} {axis.unit}, {len(axis.range)} steps)"
            yield _list_item(sweep_description, level=list_depth)
            yield from _visit_ast_node(child, list_depth=list_depth + 1)
        else:
            raise TypeError(f"Unknown child type: {type(child)}")

def _visit_experiment(experiment: Experiment):
    yield _heading(f"Experiment: {experiment._name}", level=0)
    yield from _visit_ast_node(experiment, list_depth=1)


