from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .storage import PROJECT_ROOT


CONFIG_PATH = Path(os.environ.get("RPG_WORLD_LLM_CONFIG", PROJECT_ROOT / "config.json"))
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config.example.json"


class LLMError(RuntimeError):
    pass


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if EXAMPLE_CONFIG_PATH.exists():
        return json.loads(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def public_config_status() -> dict[str, Any]:
    config = load_config()
    provider_name = config.get("default_provider", "")
    provider = (config.get("providers") or {}).get(provider_name, {})
    api_key_env = provider.get("api_key_env", "")
    return {
        "config_path": str(CONFIG_PATH),
        "using_example": not CONFIG_PATH.exists(),
        "default_provider": provider_name,
        "provider_type": provider.get("type", ""),
        "base_url": provider.get("base_url", ""),
        "api_key_env": api_key_env,
        "api_key_present": bool(os.environ.get(api_key_env)) if api_key_env else False,
        "models": config.get("models", {}),
    }


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: dict[str, Any]


class OpenAICompatibleClient:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or load_config()
        provider_name = self.config.get("default_provider", "")
        provider = (self.config.get("providers") or {}).get(provider_name, {})
        self.provider_name = provider_name
        self.base_url = (provider.get("base_url") or "").rstrip("/")
        self.api_key_env = provider.get("api_key_env") or ""
        self.api_key = os.environ.get(self.api_key_env, "")
        self.timeout = int(provider.get("timeout_seconds", 90))

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.api_key)

    def model_for(self, role: str) -> str:
        models = self.config.get("models") or {}
        return models.get(role) or models.get("default") or "deepseek-v4-flash"

    def chat_json(self, role: str, messages: list[dict[str, str]], *, temperature: float = 0.7) -> dict[str, Any]:
        response = self.chat(role, messages, temperature=temperature)
        return parse_json_object(response.content)

    def chat(self, role: str, messages: list[dict[str, str]], *, temperature: float = 0.7) -> LLMResponse:
        if not self.available:
            raise LLMError(f"LLM provider is not configured. Set {self.api_key_env or 'API key env'}.")
        model = self.model_for(role)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise LLMError(str(exc)) from exc
        parsed = json.loads(raw)
        content = parsed.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            raise LLMError("LLM response did not include message content")
        return LLMResponse(content=content, model=model, usage=parsed.get("usage", {}))


def parse_json_object(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.strip("`")
        clean = clean.removeprefix("json").strip()
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        end = clean.rfind("}")
        if start < 0 or end < start:
            raise LLMError("LLM did not return a JSON object")
        value = json.loads(clean[start : end + 1])
    if not isinstance(value, dict):
        raise LLMError("LLM JSON response must be an object")
    return value

