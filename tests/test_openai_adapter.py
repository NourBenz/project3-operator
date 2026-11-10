from types import SimpleNamespace

import pytest

from agent.llm import ModelError, OpenAIModel
from agent.registry import ToolRegistry


def test_openai_model_requires_key_without_constructing_client(monkeypatch):
    monkeypatch.setattr("agent.llm.load_dotenv", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ModelError, match="OPENAI_API_KEY is not configured"):
        OpenAIModel()


def test_openai_model_parses_mocked_tool_call_without_network():
    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content="This is a reproducible crash, so bug/high is recommended.",
                    tool_calls=[SimpleNamespace(function=SimpleNamespace(
                        name="update_labels",
                        arguments='{"issue_id":12,"labels":["bug","high"],"operation_id":"triage-12","expected_version":1}',
                    ))],
                ))],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    model = OpenAIModel(api_key="test-key", client=client)
    decision = model.decide("Triage issue 12", [], ToolRegistry().openai_tools())

    assert captured["tool_choice"] == "auto"
    assert captured["parallel_tool_calls"] is False
    assert captured["messages"][0]["role"] == "system"
    assert decision.tool_calls[0].name == "update_labels"
    assert decision.tool_calls[0].arguments["labels"] == ["bug", "high"]
    assert decision.explanation.startswith("This is a reproducible crash")
    assert (decision.input_tokens, decision.output_tokens) == (11, 7)
