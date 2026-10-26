"""AI providers for Ask Pomo, each with your own key: Anthropic (Claude), OpenAI (ChatGPT), Google Gemini,
xAI (Grok) and OpenRouter (hundreds of models behind one key).

Anthropic goes through the official Anthropic SDK. The others all offer an OpenAI-compatible
chat endpoint, which Pomo talks to directly. Model lists move fast, so each provider has a few
suggestions here, "Load models" fetches the provider's current list, and any model ID can be typed in.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

LIST_SKIP = ("embedding", "tts", "whisper", "dall-e", "image", "imagine", "audio", "realtime", "moderation",
             "transcribe", "search", "video", "veo", "imagen", "lyria", "aqa", "babbage", "davinci")


@dataclass(frozen=True)
class Provider:
    key: str
    label: str           # "OpenAI (ChatGPT)"
    short: str           # "OpenAI", for the chat header
    kind: str            # "anthropic" or "compat" (an OpenAI-compatible chat endpoint)
    base_url: str
    models: tuple[tuple[str, str], ...]  # suggested (model id, label); the first is the default
    keys_url: str
    env: tuple[str, ...]  # environment variables that may hold a key
    key_hint: str         # what a key looks like, for the placeholder


PROVIDERS: dict[str, Provider] = {p.key: p for p in (
    Provider("anthropic", "Anthropic (Claude)", "Claude", "anthropic", "",
             (("claude-opus-5-5", "Claude Opus 5.5"), ("claude-sonnet-5-5", "Claude Sonnet 5.5"),
              ("claude-haiku-5-5", "Claude Haiku 5.5"), ("claude-fable-5-1", "Claude Fable 5.1"),
              ("claude-opus-5", "Claude Opus 5")),
             "https://console.anthropic.com/settings/keys", ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
             "sk-ant-…"),
    Provider("openai", "OpenAI (ChatGPT)", "OpenAI", "compat", "https://api.openai.com/v1",
             (("gpt-5.4", "GPT-5.4"), ("gpt-5.4-mini", "GPT-5.4 mini"), ("gpt-5.4-nano", "GPT-5.4 nano")),
             "https://platform.openai.com/api-keys", ("OPENAI_API_KEY",), "sk-…"),
    Provider("gemini", "Google Gemini", "Gemini", "compat", "https://generativelanguage.googleapis.com/v1beta/openai",
             (("gemini-3.5-flash", "Gemini 3.5 Flash"), ("gemini-3.1-pro-preview", "Gemini 3.1 Pro (preview)"),
              ("gemini-flash-latest", "Gemini Flash (latest)")),
             "https://aistudio.google.com/apikey", ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "AIza…"),
    Provider("xai", "xAI (Grok)", "Grok", "compat", "https://api.x.ai/v1",
             (("grok-4.6", "Grok 4.6"), ("grok-4.3", "Grok 4.3")),
             "https://console.x.ai", ("XAI_API_KEY",), "xai-…"),
    Provider("openrouter", "OpenRouter", "OpenRouter", "compat", "https://openrouter.ai/api/v1",
             (("openrouter/auto", "Auto (OpenRouter picks)"), ("anthropic/claude-opus-5.5", "Claude Opus 5.5"),
              ("openai/gpt-5.4", "GPT-5.4"), ("google/gemini-3.5-flash", "Gemini 3.5 Flash"),
              ("x-ai/grok-4.6", "Grok 4.6")),
             "https://openrouter.ai/keys", ("OPENROUTER_API_KEY",), "sk-or-…"),
)}
DEFAULT_PROVIDER = "anthropic"


def provider(key: str) -> Provider:
    return PROVIDERS.get(key) or PROVIDERS[DEFAULT_PROVIDER]


def default_model(key: str) -> str:
    return provider(key).models[0][0]


def model_label(key: str, model: str) -> str:
    return dict(provider(key).models).get(model, model)


def env_key(key: str) -> str | None:
    for name in provider(key).env:
        if os.environ.get(name):
            return os.environ[name]
    return None


def key_file(data_dir: Path, key: str) -> Path:
    """Where the encrypted key lives. Anthropic keeps its original file name, so saved keys carry over."""
    return data_dir / ("ai_key.bin" if key == "anthropic" else f"ai_key_{key}.bin")


def model_for(settings, key: str | None = None) -> str:
    key = key or settings.ai_provider
    if key == "anthropic":
        return settings.ai_model or default_model(key)
    return settings.ai_models.get(key) or default_model(key)


def set_model(settings, key: str, model: str) -> None:
    model = model.strip()
    if key == "anthropic":
        settings.ai_model = model or default_model(key)
    else:
        settings.ai_models = {**settings.ai_models, key: model or default_model(key)}


def chat_models(ids: list[str]) -> list[str]:
    """From a provider's full model list, the ones that chat (no embeddings, voices, images...)."""
    out = []
    for raw in ids:
        model = raw.removeprefix("models/")
        if model and not any(word in model.lower() for word in LIST_SKIP):
            out.append(model)
    return sorted(set(out))
