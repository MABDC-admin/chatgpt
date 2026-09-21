"""Tests for imaging parameter compatibility with gpt-image-2.5-* models.

These models reject parameters that gpt-image-1 accepts:
- response_format
- input_fidelity

The imaging module must conditionally omit them.
"""

from app.services.imaging import _supports_response_format


class TestSupportsResponseFormat:
    """_supports_response_format returns False for gpt-image-2.5-* models."""

    def test_gpt_image_25_flare_not_supported(self):
        assert _supports_response_format("gpt-image-2.5-flare") is False

    def test_gpt_image_25_sunburst_not_supported(self):
        assert _supports_response_format("gpt-image-2.5-sunburst") is False

    def test_gpt_image_1_supported(self):
        assert _supports_response_format("gpt-image-1") is True

    def test_chatgpt_image_latest_supported(self):
        # Not a 2.5 model, should default to supported
        assert _supports_response_format("chatgpt-image-latest") is True


class TestEditKwargsForImage25:
    """edit() must not send input_fidelity or response_format to gpt-image-2.5-* models.

    We test the kwargs construction logic by inspecting what would be sent,
    without actually calling the OpenAI API.
    """

    def test_edit_kwargs_exclude_input_fidelity_for_25_models(self):
        """Build the same kwargs dict that edit() builds and verify
        input_fidelity is absent for gpt-image-2.5-flare."""
        model = "gpt-image-2.5-flare"
        edit_kwargs: dict = dict(
            model=model,
            prompt="test",
            size="1024x1024",
            quality="medium",
            n=1,
        )
        if _supports_response_format(model):
            edit_kwargs["response_format"] = "b64_json"
            edit_kwargs["input_fidelity"] = "high"

        assert "input_fidelity" not in edit_kwargs
        assert "response_format" not in edit_kwargs

    def test_edit_kwargs_include_input_fidelity_for_gpt_image_1(self):
        """gpt-image-1 should still get both parameters."""
        model = "gpt-image-1"
        edit_kwargs: dict = dict(
            model=model,
            prompt="test",
            size="1024x1024",
            quality="medium",
            n=1,
        )
        if _supports_response_format(model):
            edit_kwargs["response_format"] = "b64_json"
            edit_kwargs["input_fidelity"] = "high"

        assert edit_kwargs["input_fidelity"] == "high"
        assert edit_kwargs["response_format"] == "b64_json"
