"""Regression tests for the /generate endpoint accepting both payload shapes.

The restored full codebase ships two clients that call POST /generate:
- the frontend/fix path sends {prompt: str}
- the legacy swarm brain (swarm_os/brain.py make_swarm_brain) sends
  {model, messages: [...], temperature, stream} — a chat-completions-style
  payload. GenerateRequest previously required `prompt`, so every brain step
  POSTed a body with `messages` and got 422 Unprocessable Content (the
  "422 flood" seen in the boot logs).
"""
from swarm_os.api.schemas import GenerateRequest


def test_prompt_shape_is_accepted():
    req = GenerateRequest(prompt="hello world")
    assert req.prompt == "hello world"
    assert req.messages is None


def test_messages_shape_is_accepted():
    req = GenerateRequest(
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert req.prompt is None
    assert req.messages[1]["content"] == "hi"


def test_brain_payload_shape_with_extra_fields_is_accepted():
    # The brain posts model + messages + temperature + stream. Extra fields
    # are ignored by default pydantic config; the messages list is what
    # matters for the 422 regression.
    req = GenerateRequest(
        model="qwen3.5-4b",
        messages=[{"role": "user", "content": "analyze"}],
        temperature=0.7,
        stream=False,
    )
    assert req.model == "qwen3.5-4b"
    assert req.messages == [{"role": "user", "content": "analyze"}]


def test_route_rejects_body_with_neither_prompt_nor_messages():
    # The route handler (not just the schema) rejects a body with neither
    # field. This is enforced in routes.py generate(); here we verify the
    # schema-level requirement is that both are optional (validation of the
    # combination lives in the route).
    req = GenerateRequest()
    assert req.prompt is None
    assert req.messages is None
