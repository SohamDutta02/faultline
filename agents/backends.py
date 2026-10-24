"""Which model backend to use: providers, default models, keys.

A model id picks its provider: gemini-* goes to Google, claude-* to
Anthropic, gpt-*/o1/o3/o4 to OpenAI, and anything else (Qwen/Qwen3-8B,
llama3.1, ...) to an OpenAI-compatible server at OPENAI_BASE_URL.
FAULTLINE_PROVIDER or --provider overrides the guess.

With no model given, the default is FAULTLINE_MODEL if set, else the default
model of the first provider that has a key (Gemini, then Anthropic, then
OpenAI), else Gemini's.
"""

import settings

from .anthropic_backend import DEFAULT_MODEL as ANTHROPIC_DEFAULT_MODEL
from .llm import GEMINI_DEFAULT_MODEL, Backend, GeminiBackend, LLMError

PROVIDERS = ("gemini", "anthropic", "openai")
DEFAULT_MODELS = {"gemini": GEMINI_DEFAULT_MODEL, "anthropic": ANTHROPIC_DEFAULT_MODEL, "openai": None}
KEY_VARS = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY or OPENAI_BASE_URL"}
_OPENAI_PREFIXES = ("gpt-", "gpt4", "o1", "o3", "o4", "chatgpt")


def provider_for(model: str | None, provider: str | None = None) -> str:
    if provider:
        if provider not in PROVIDERS:
            raise LLMError(f"unknown provider {provider!r}; have {', '.join(PROVIDERS)}")
        return provider
    m = (model or "").lower()
    if m.startswith("gemini"):
        return "gemini"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(_OPENAI_PREFIXES) or model:
        return "openai"
    return configured_provider()


def has_key(provider: str) -> bool:
    if provider == "gemini":
        return bool(settings.gemini_key())
    if provider == "anthropic":
        return bool(settings.anthropic_key())
    return bool(settings.openai_key() or settings.openai_base_url())


def configured_provider() -> str:
    if settings.PROVIDER:
        return provider_for(None, settings.PROVIDER)
    if settings.MODEL:
        return provider_for(settings.MODEL)
    return next((p for p in PROVIDERS if has_key(p) and DEFAULT_MODELS[p]), "gemini")


def resolve(model: str | None = None, provider: str | None = None) -> tuple[str, str | None]:
    """(provider, model) for a request; model may be None for an OpenAI-compatible server with no default."""
    if model:
        return provider_for(model, provider), model
    p = provider_for(None, provider) if provider else configured_provider()
    if settings.MODEL and provider_for(settings.MODEL, settings.PROVIDER) == p:
        return p, settings.MODEL
    return p, DEFAULT_MODELS[p]


def available(model: str | None = None, provider: str | None = None) -> bool:
    try:
        p, m = resolve(model, provider)
    except LLMError:
        return False
    return has_key(p) and bool(m)


_cache: dict[tuple, Backend] = {}


def make_backend(model: str | None = None, provider: str | None = None) -> Backend:
    p, m = resolve(model, provider)
    key = (p, m)
    if key not in _cache:
        if p == "gemini":
            _cache[key] = GeminiBackend(model=m)
        elif p == "anthropic":
            from .anthropic_backend import AnthropicBackend

            _cache[key] = AnthropicBackend(model=m)
        else:
            from .openai_backend import OpenAIBackend

            _cache[key] = OpenAIBackend(model=m)
    return _cache[key]


def describe() -> list[dict]:
    """What the web app shows: each provider, whether it is usable, its default model."""
    default = configured_provider()
    return [
        {"id": p, "available": has_key(p), "default_model": resolve(None, p)[1], "key": KEY_VARS[p],
         "default": p == default}
        for p in PROVIDERS
    ]
