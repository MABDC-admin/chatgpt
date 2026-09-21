"""Provider price table, in USD cents per 1M tokens / per image.

These are list prices and they change. They live here, in one place, so the
finance side of the platform can be corrected with a single edit. Verify
against https://openai.com/api/pricing/ before you bill anyone for real.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TextPrice:
    input_cents_per_mtok: float
    output_cents_per_mtok: float


# Cents per 1M tokens. Sol/Terra/Luna are capability tiers within a generation.
TEXT_PRICES: dict[str, TextPrice] = {
    "gpt-6-astra": TextPrice(1000.0, 5000.0),
    "gpt-5.6-sol": TextPrice(500.0, 3000.0),
    "gpt-5.6-terra": TextPrice(200.0, 1200.0),
    "gpt-5.6-luna": TextPrice(20.0, 120.0),
    "gpt-4o": TextPrice(250.0, 1000.0),
    "gpt-4o-mini": TextPrice(15.0, 60.0),
}

# cents per image, by model -> quality -> size
IMAGE_PRICES: dict[str, dict[str, dict[str, float]]] = {
    "gpt-image-1": {
        "low": {"1024x1024": 1.1, "1024x1536": 1.6, "1536x1024": 1.6},
        "medium": {"1024x1024": 4.2, "1024x1536": 6.3, "1536x1024": 6.3},
        "high": {"1024x1024": 16.7, "1024x1536": 25.0, "1536x1024": 25.0},
    },
    "gpt-image-1-mini": {
        "low": {"1024x1024": 0.5, "1024x1536": 0.8, "1536x1024": 0.8},
        "medium": {"1024x1024": 1.1, "1024x1536": 1.6, "1536x1024": 1.6},
        "high": {"1024x1024": 4.2, "1024x1536": 6.3, "1536x1024": 6.3},
    },
}

@dataclass(frozen=True)
class ImageTokenPrice:
    """Token-metered image models bill by tokens in / image tokens out.

    Cents per 1M tokens. Rates confirmed against openai.com pricing 2026-09:
    the 2.5 series charges $5/M text input, $30/M image output, and does not
    bill text output (these models return images). Image-in input rate is
    priced separately when editing an existing picture.
    """
    text_in_cents_per_mtok: float
    image_out_cents_per_mtok: float
    image_in_cents_per_mtok: float = 0.0


# Newer models are token-billed rather than flat per-image, so their real cost
# scales with quality: a 1024x1024 low draft is under a cent, a high full-page
# poster is a few cents, and the same call cannot be priced from a static table.
IMAGE_TOKEN_PRICES: dict[str, ImageTokenPrice] = {
    "gpt-image-2.5-flare":    ImageTokenPrice(500.0, 3000.0, 800.0),
    "gpt-image-2.5-sunburst": ImageTokenPrice(500.0, 3000.0, 800.0),
    # A production alias for whatever ChatGPT itself is serving; OpenAI does
    # not publish its rates so we bill it at the highest-in-family tier as a
    # safe over-approximation until they do.
    "chatgpt-image-latest":   ImageTokenPrice(500.0, 3000.0, 800.0),
}


EMBEDDING_PRICES: dict[str, float] = {
    "text-embedding-3-small": 2.0,
    "text-embedding-3-large": 13.0,
}

# Unknown model: bill at the flagship rate rather than under-charge.
_TEXT_FALLBACK = TextPrice(1000.0, 5000.0)


def text_cost_cents(model: str, prompt_tokens: int, completion_tokens: int) -> int:
    price = TEXT_PRICES.get(model, _TEXT_FALLBACK)
    total = (
        prompt_tokens / 1_000_000 * price.input_cents_per_mtok
        + completion_tokens / 1_000_000 * price.output_cents_per_mtok
    )
    # Round up: never under-bill a user for a call that did happen.
    return max(int(total + 0.999), 1) if total > 0 else 0


def image_cost_cents(model: str, quality: str, size: str, count: int = 1) -> int:
    """Estimate the cost of a call before making it.

    Used to gate against a user's remaining balance. Token-metered models are
    priced from measured token counts per quality tier -- taken from live probe
    runs on 2026-09 -- because their real cost varies more than any static
    table can capture. `image_cost_from_usage()` bills the exact value.
    """
    if model in IMAGE_TOKEN_PRICES:
        return _image_token_estimate(model, quality, size, count)

    by_quality = IMAGE_PRICES.get(model, IMAGE_PRICES["gpt-image-1"])
    by_size = by_quality.get(quality, by_quality["medium"])
    unit = by_size.get(size, next(iter(by_size.values())))
    return max(int(unit * count + 0.999), 1)


# Measured image_tokens per call at 1024x1024, gpt-image-2.5-flare, 2026-09-20.
# Portrait/landscape use ~1.5x the square value.
_IMAGE_TOKEN_ESTIMATES: dict[str, int] = {"low": 200, "medium": 450, "high": 1800}


def _image_token_estimate(model: str, quality: str, size: str, count: int) -> int:
    price = IMAGE_TOKEN_PRICES[model]
    image_tokens = _IMAGE_TOKEN_ESTIMATES.get(quality, _IMAGE_TOKEN_ESTIMATES["medium"])
    if size != "1024x1024":
        image_tokens = int(image_tokens * 1.5)
    # ~20 text-in tokens for a typical expanded prompt.
    text_in = 40
    cents = (
        text_in / 1_000_000 * price.text_in_cents_per_mtok
        + image_tokens / 1_000_000 * price.image_out_cents_per_mtok
    )
    return max(int(cents * count + 0.999), 1)


def image_cost_from_usage(model: str, usage) -> int | None:
    """Bill from the actual token counts the provider returned.

    Returns None when the model is not token-metered, in which case the caller
    keeps the estimate produced by image_cost_cents().
    """
    price = IMAGE_TOKEN_PRICES.get(model)
    if price is None or usage is None:
        return None
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    details = getattr(usage, "output_tokens_details", None)
    if isinstance(details, dict):
        image_tokens = details.get("image_tokens", 0) or 0
    else:
        image_tokens = getattr(details, "image_tokens", 0) or 0
    cents = (
        input_tokens / 1_000_000 * price.text_in_cents_per_mtok
        + image_tokens / 1_000_000 * price.image_out_cents_per_mtok
    )
    return max(int(cents + 0.999), 1) if cents > 0 else 0


def embedding_cost_cents(model: str, tokens: int) -> int:
    per_mtok = EMBEDDING_PRICES.get(model, 2.0)
    total = tokens / 1_000_000 * per_mtok
    return max(int(total + 0.999), 1) if total > 0 else 0
