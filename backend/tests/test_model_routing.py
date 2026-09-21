"""Tests for selectable text-model and reasoning routes."""

from pydantic import ValidationError

from app.schemas import ChatRequest
from app.services.ai import completion_kwargs, resolve_text_route


def test_auto_uses_luna_for_a_short_everyday_prompt():
    route = resolve_text_route("Explain photosynthesis simply.", mode="auto")

    assert route.model == "gpt-5.6-luna"
    assert route.reasoning_effort == "medium"
    assert route.label == "Auto"


def test_auto_uses_terra_for_lesson_design():
    route = resolve_text_route(
        "Create a differentiated Grade 6 lesson plan about fractions.", mode="auto"
    )

    assert route.model == "gpt-5.6-terra"
    assert route.reasoning_effort == "medium"


def test_luna_mode_honors_high_reasoning():
    route = resolve_text_route(
        "Compare two approaches.", mode="luna", reasoning_effort="high"
    )

    assert route.model == "gpt-5.6-luna"
    assert route.reasoning_effort == "high"
    assert route.label == "Luna"


def test_terra_mode_defaults_to_high_reasoning():
    route = resolve_text_route("Analyse the evidence.", mode="terra")

    assert route.model == "gpt-5.6-terra"
    assert route.reasoning_effort == "high"
    assert route.label == "Thinking"


def test_sol_mode_defaults_to_high_reasoning():
    route = resolve_text_route("Design a complex implementation plan.", mode="sol")

    assert route.model == "gpt-5.6-sol"
    assert route.reasoning_effort == "high"
    assert route.label == "Pro"


def test_unknown_mode_falls_back_to_auto_routing():
    route = resolve_text_route("Hello", mode="unknown")

    assert route.model == "gpt-5.6-luna"
    assert route.label == "Auto"


def test_chat_request_defaults_to_auto_route():
    request = ChatRequest(message="Hello")

    assert request.mode == "auto"
    assert request.reasoning_effort is None


def test_chat_request_rejects_an_unknown_reasoning_effort():
    try:
        ChatRequest(message="Hello", reasoning_effort="maximum")
    except ValidationError:
        return
    raise AssertionError("An unknown reasoning effort must be rejected")


def test_gpt_56_completion_includes_requested_reasoning_effort():
    kwargs = completion_kwargs("gpt-5.6-terra", reasoning_effort="high")

    assert kwargs["reasoning_effort"] == "high"


def test_legacy_models_do_not_receive_reasoning_effort():
    assert "reasoning_effort" not in completion_kwargs(
        "gpt-4o", reasoning_effort="high"
    )
