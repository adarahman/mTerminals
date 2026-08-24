import pytest

from analytics.option_chain_pipeline import OptionChainPipeline
from application.pipeline_config import RuntimeConfig


def test_option_chain_pipeline_passes_immutable_config_to_implementation():
    received = []
    pipeline = OptionChainPipeline(
        implementation=lambda config: received.append(config) or "complete"
    )
    config = RuntimeConfig(symbol="NIFTY")

    assert pipeline.run(config) == "complete"
    assert received == [config]


def test_option_chain_pipeline_rejects_untyped_runtime_input():
    pipeline = OptionChainPipeline(implementation=lambda config: None)

    with pytest.raises(TypeError):
        pipeline.run({"symbol": "NIFTY"})
