"""Connection presets; model IDs and credentials belong to the installation owner."""

PROVIDERS = {
    "codex": {"label": "Codex", "protocol": "codex", "url": "", "format": "", "key": False},
    "openai": {
        "label": "OpenAI API",
        "protocol": "responses",
        "url": "https://api.openai.com/v1",
        "format": "json_schema",
        "key": True,
    },
    "openai_compatible": {
        "label": "Совместимый API",
        "protocol": "chat",
        "url": "",
        "format": "json_object",
        "key": False,
    },
    "anthropic": {
        "label": "Claude API",
        "protocol": "anthropic",
        "url": "https://api.anthropic.com/v1",
        "format": "tool",
        "key": True,
    },
    "gemini": {
        "label": "Gemini API",
        "protocol": "chat",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "format": "json_object",
        "key": True,
    },
    "openrouter": {
        "label": "OpenRouter",
        "protocol": "chat",
        "url": "https://openrouter.ai/api/v1",
        "format": "json_object",
        "key": True,
    },
    "deepseek": {
        "label": "DeepSeek",
        "protocol": "chat",
        "url": "https://api.deepseek.com",
        "format": "json_object",
        "key": True,
    },
    "ollama": {
        "label": "Ollama",
        "protocol": "chat",
        "url": "http://127.0.0.1:11434/v1",
        "format": "json_schema",
        "key": False,
    },
    "lmstudio": {
        "label": "LM Studio",
        "protocol": "chat",
        "url": "http://127.0.0.1:1234/v1",
        "format": "json_schema",
        "key": False,
    },
    "demo": {"label": "Демо без ИИ", "protocol": "demo", "url": "", "format": "", "key": False},
}


def provider_info(settings):
    preset = PROVIDERS[settings.model_adapter]
    return {"provider": preset["label"], "protocol": preset["protocol"]}
