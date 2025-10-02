import pytest
from qkit.measure.unified_measurements import *
from qkit.measure.samples_class import Sample

from dokuwiki_autodoc.autodoc import QkitDocumentationBuilder
from dokuwiki_autodoc.unified_measurement_adapter import generate_summary, generate_sweep_summary, create_table_entry
import numpy as np
from .test_auto_documentation import autodoc, qkit_fix

EXPERIMENT_EXPECTED_DOCUMENTATION = textwrap.dedent("""\
===== Experiment: Test Experiment =====
  * first (0.0 s -> 1.0 s, 10 steps)
    - ScalarMeasurement""")

@pytest.fixture
def unified_experiment():
    sample = Sample()
    exp = Experiment(name="Test Experiment", sample=sample)
    axis = Axis(name="first", unit="s", range=np.linspace(0, 1, 10))
    with exp.sweep(lambda val: None, axis) as sweep:
        sweep.measure(ScalarMeasurement('const', lambda: 1.0))
    return exp

def test_tree_access(unified_experiment):
    # Build the documentation for this experiment automatically
    documentation = generate_summary(unified_experiment)
    assert documentation == EXPERIMENT_EXPECTED_DOCUMENTATION

def test_axis_summary(unified_experiment):
    expected =  textwrap.dedent("""\
    ==== Sweep: first ====
      * Start: 0.0 s
      * Stop: 1.0 s
      * Steps: 10""")
    actual = generate_sweep_summary(unified_experiment)
    assert actual == expected

def test_end_to_end(qkit_fix, autodoc, unified_experiment):
    setpoint = {
        'bias': (0, 'V')
    }
    with QkitDocumentationBuilder(autodoc, 'sample:test:qkit', 'RZDWVZ') as builder:
        builder.from_unified_measurement(unified_experiment, setpoints=setpoint)
        with builder.table_builder() as tb:
            tb.from_unified(unified_experiment, setpoints=setpoint)
            tb.add_column("Comment", lambda _: "Look! A comment!")

    autodoc.wiki.pages.set.assert_called_once()  # This function must have been called exactly once
    call_args = autodoc.wiki.pages.set.call_args.args
    call_kwargs = autodoc.wiki.pages.set.call_args.kwargs
    assert len(call_args) == 2, "Invalid argument count!"
    assert call_args[0] == 'sample:test:qkit:RZDWVZ'
    assert call_args[1] == open("./tests/unified-expected.txt").read(), f"Found:\n {call_args[1]}"
    assert call_kwargs['sum'] == 'Automatic Report Generation.'



#QKIT_TEMPLATE = """
# {% extends "doc_base.txt.liquid" %}
# {% block title %} TwoTone Power Scan {% endblock %}
# {% block content %}
# Bias {[bias]}
# {% endblock%}
# """
#
# with QkitDocumentationBuilder(doc, AutoDocumentation.join_path([sample.wiki, 'twotone_scan'])) as builder:
#         builder.upload_images()
#         builder.update_context(bias=bias, vna_attenuation=vna.get_power_offset(),
#                                mw={'start': mw_start, 'stop': mw_stop, 'step': mw_step, 'attenuation': mw_src.get_power_offset(), 'power_start': power_start, 'power_stop': power_stop, 'power_step': power_step})
#         builder.generate_report(QKIT_TEMPLATE)
#         with builder.table_builder() as tb:
#             tb.add_column("resonator", lambda data: res)
#             tb.add_column("f_r (GHz)", lambda data: round(res_freq/1e9,3))
#             tb.add_column("mw range (GHz)", lambda data: str(round(mw_start/1e9,1)) + " to " + str(round(mw_stop/1e9,1)))
#             tb.add_column("mw step (MHz)", lambda data: round(mw_step/1e6,1))
#             tb.add_column("U bias (mV)", lambda data: u_bias*1e3)
#             tb.add_column("drive power (dBm)", lambda data: mw_power)
#             tb.add_column("vna power (dBm)", lambda data: data.settings['vna']['power'])
#             tb.add_column("#avg", lambda data: int(data.settings['vna']['averages']))
#             tb.add_column("bw (Hz)", lambda data: int(data.settings['vna']['bandwidth']))
#             tb.add_column("Comment", lambda _: input("Comment: "))