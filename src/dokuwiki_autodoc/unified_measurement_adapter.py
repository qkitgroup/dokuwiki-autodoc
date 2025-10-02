from typing import Any, Generator

import numpy as np
from qkit.measure.unified_measurements import Experiment, ParentOfMeasurements, ParentOfSweep, Sweep, Axis

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
    for axes in list(_collect_axes(experiment)):
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


def _collect_axes(experiment: Experiment):
    yield from _collect_measurement_axes(experiment)
    if experiment._sweep_child is not None:
        yield from _collect_sweep(experiment._sweep_child)


def _collect_sweep(sweep: Sweep):
    yield sweep._axis
    yield from _collect_measurement_axes(sweep)
    if sweep._sweep_child is not None:
        yield from _collect_sweep(sweep._sweep_child)

def _collect_measurement_axes(pom: ParentOfMeasurements):
    for measurement in pom._measurements:
        for es in measurement.expected_structure:
            yield from es.axes

def _heading(text: str, level: int):
    assert 0 <= level <= 5, "Heading level must be between 0 and 5."
    heding_tokens = 5 - level
    return "=" * heding_tokens + " " + text + " " + "=" * heding_tokens

def _enumeration_item(text: str, level: int = 1):
    return "  " * level + "- " + text

def _visit_sweep(sweep: Sweep, list_depth: int = 1):
    """
    Emit the current sweep, then the measurements, and then the child sweep.
    """
    axis = sweep._axis
    sweep_description = f"{axis.name} ({np.min(axis.range)} {axis.unit} -> {np.max(axis.range)} {axis.unit}, {len(axis.range)} steps)"
    yield _list_item(sweep_description, level=list_depth)
    yield from _visit_measurements(sweep, list_depth=list_depth + 1)
    if sweep._sweep_child is not None:
        yield from _visit_sweep(sweep._sweep_child, list_depth=list_depth + 1)

def _list_item(text: str, level: int = 1):
    return "  " * level + "* " + text

def _visit_measurements(measurements: ParentOfMeasurements, list_depth: int = 0):
    """
    Emit the current
    """
    for measurement in measurements._measurements:
        yield _enumeration_item(f"{type(measurement).__name__}", level=list_depth)

def _visit_experiment(experiment: Experiment):
    yield _heading(f"Experiment: {experiment._name}", level=0)
    yield from _visit_measurements(experiment, list_depth=0)
    if experiment._sweep_child is not None:
        yield from _visit_sweep(experiment._sweep_child, list_depth=1)


