"""
LLM 适配层

统一接口封装 Claude / GPT / Deepseek / 阿里百炼 / Ollama。
新增模型只需加一个 Adapter 子类。
"""

import logging
import os
import json
import re
from abc import ABC, abstractmethod
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# 强制 UTF-8 编码，解决容器内 ascii codec 报错
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("LC_ALL", "C.UTF-8")
os.environ.setdefault("LANG", "C.UTF-8")


def _clean_api_key(key: str) -> str:
    """Normalize API key text loaded from env files with potential encoding artifacts."""
    if not key:
        return ""

    cleaned = key.replace("\ufeff", "").strip().strip("'\"")
    # Keep only printable ASCII to avoid hidden control chars from copied keys.
    cleaned = re.sub(r"[^\x20-\x7E]", "", cleaned)
    return cleaned.strip()


def _has_key(raw_key: str) -> bool:
    key = _clean_api_key(raw_key)
    if not key:
        return False

    normalized = key.lower()
    placeholder_literals = {
        "sk-xxx",
        "xxx",
        "your_api_key",
        "your-api-key",
        "your_key",
        "change-me",
        "changeme",
        "replace-me",
        "replace_this",
        "none",
        "null",
    }

    if normalized in placeholder_literals:
        return False
    if normalized.startswith("<") and normalized.endswith(">"):
        return False
    if re.fullmatch(r"sk(?:-[a-z]+)?-x+", normalized):
        return False
    if normalized.endswith("-xxx") or normalized.endswith("_xxx"):
        return False

    return True


class LLMAdapter(ABC):
    """LLM 适配器抽象基类"""

    provider: str = "base"

    @abstractmethod
    async def chat(
        self,
        model: str,
        messages: list[dict],
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """统一的对话接口，返回模型回答文本"""
        ...

    async def chat_stream(
        self,
        model: str,
        messages: list[dict],
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        """默认流式实现：不支持原生流式的 provider 退化为一次性返回。"""
        text = await self.chat(
            model=model,
            messages=messages,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if text:
            yield text


async def _iter_openai_compatible_stream(response) -> AsyncGenerator[str, None]:
    async for line in response.aiter_lines():
        if not line:
            continue
        if not line.startswith("data:"):
            continue

        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue

        try:
            payload = json.loads(data)
            delta = payload["choices"][0].get("delta", {})
            content = delta.get("content", "")
            if content:
                yield content
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            continue


class DashScopeAdapter(LLMAdapter):
    """
    阿里百炼（通义千问系列），兼容 OpenAI 接口。
    支持 qwen-plus / qwen-max / qwen-turbo / qwen3-xxx / qwen3.5-xxx 等所有 qwen 系列。
    """
    provider = "qwen"

    def __init__(self):
        from backend.config import get_settings
        settings = get_settings()
        api_key = _clean_api_key(settings.dashscope_api_key)
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY not configured")

        # 使用 httpx 直接调用，避免 openai 库的编码问题
        import httpx
        self.client = httpx.AsyncClient(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=120.0,
        )

    async def chat(self, model, messages, system="", temperature=0.7, max_tokens=4096) -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)

        payload = {
            "model": model,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            response = await self.client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"DashScope API error: {e}")
            # 尝试读取响应体获取更详细的错误
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            raise

    async def chat_stream(self, model, messages, system="", temperature=0.7, max_tokens=4096) -> AsyncGenerator[str, None]:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)

        payload = {
            "model": model,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        async with self.client.stream("POST", "/chat/completions", json=payload) as response:
            response.raise_for_status()
            async for content in _iter_openai_compatible_stream(response):
                yield content


class DeepseekAdapter(LLMAdapter):
    provider = "deepseek"

    def __init__(self):
        from backend.config import get_settings
        import httpx
        settings = get_settings()
        api_key = _clean_api_key(settings.deepseek_api_key)
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY not configured")

        self.client = httpx.AsyncClient(
            base_url="https://api.deepseek.com/v1",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=120.0,
        )

    async def chat(self, model, messages, system="", temperature=0.7, max_tokens=4096) -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)

        response = await self.client.post("/chat/completions", json={
            "model": model, "messages": msgs,
            "temperature": temperature, "max_tokens": max_tokens,
        })
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    async def chat_stream(self, model, messages, system="", temperature=0.7, max_tokens=4096) -> AsyncGenerator[str, None]:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)

        payload = {
            "model": model,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        async with self.client.stream("POST", "/chat/completions", json=payload) as response:
            response.raise_for_status()
            async for content in _iter_openai_compatible_stream(response):
                yield content


class OpenAIAdapter(LLMAdapter):
    provider = "openai"

    def __init__(self):
        from backend.config import get_settings
        import httpx
        settings = get_settings()
        api_key = _clean_api_key(settings.openai_api_key)
        if not api_key:
            raise ValueError("OPENAI_API_KEY not configured")

        self.client = httpx.AsyncClient(
            base_url="https://api.openai.com/v1",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=120.0,
        )

    async def chat(self, model, messages, system="", temperature=0.7, max_tokens=4096) -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)

        response = await self.client.post("/chat/completions", json={
            "model": model, "messages": msgs,
            "temperature": temperature, "max_tokens": max_tokens,
        })
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    async def chat_stream(self, model, messages, system="", temperature=0.7, max_tokens=4096) -> AsyncGenerator[str, None]:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)

        payload = {
            "model": model,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        async with self.client.stream("POST", "/chat/completions", json=payload) as response:
            response.raise_for_status()
            async for content in _iter_openai_compatible_stream(response):
                yield content


class AnthropicAdapter(LLMAdapter):
    provider = "claude"

    def __init__(self):
        from backend.config import get_settings
        import httpx
        settings = get_settings()
        api_key = _clean_api_key(settings.anthropic_api_key)
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not configured")

        self.client = httpx.AsyncClient(
            base_url="https://api.anthropic.com/v1",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=120.0,
        )

    async def chat(self, model, messages, system="", temperature=0.7, max_tokens=4096) -> str:
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system

        response = await self.client.post("/messages", json=payload)
        response.raise_for_status()
        return response.json()["content"][0]["text"]


class OllamaAdapter(LLMAdapter):
    provider = "ollama"

    def __init__(self):
        from backend.config import get_settings
        import httpx
        self.base_url = get_settings().ollama_base_url
        self.client = httpx.AsyncClient(timeout=120.0)

    async def chat(self, model, messages, system="", temperature=0.7, max_tokens=4096) -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        response = await self.client.post(
            f"{self.base_url}/api/chat",
            json={"model": model.replace("ollama:", ""), "messages": msgs, "stream": False},
        )
        return response.json()["message"]["content"]

    async def chat_stream(self, model, messages, system="", temperature=0.7, max_tokens=4096) -> AsyncGenerator[str, None]:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)

        payload = {
            "model": model.replace("ollama:", ""),
            "messages": msgs,
            "stream": True,
        }

        async with self.client.stream(f"POST", f"{self.base_url}/api/chat", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                content = (chunk.get("message") or {}).get("content", "")
                if content:
                    yield content

                if chunk.get("done"):
                    break


# ========================
# 模型路由
# ========================

PROVIDER_MAP = {
    "qwen": DashScopeAdapter,
    "claude": AnthropicAdapter,
    "gpt": OpenAIAdapter,
    "deepseek": DeepseekAdapter,
    "ollama": OllamaAdapter,
}

_adapter_cache: dict[str, LLMAdapter] = {}


def resolve_adapter(model: str) -> tuple[LLMAdapter, str]:
    """
    根据模型名称解析对应 Adapter

    支持所有 qwen 系列（qwen-plus, qwen3-max, qwen3.5-plus 等）
    """
    for prefix, adapter_cls in PROVIDER_MAP.items():
        if model.startswith(prefix):
            if prefix not in _adapter_cache:
                try:
                    _adapter_cache[prefix] = adapter_cls()
                except ValueError as e:
                    raise ValueError(f"Failed to init {prefix} adapter: {e}")
            return _adapter_cache[prefix], model

    raise ValueError(
        f"Unknown model: '{model}'. "
        f"Supported prefixes: {list(PROVIDER_MAP.keys())}. "
        f"Examples: qwen3-max-2026-01-23, qwen3.5-plus, deepseek-chat, gpt-4o"
    )


def list_available_models() -> list[dict]:
    """返回已配置 API Key 对应的可用模型列表。"""
    from backend.config import get_settings

    settings = get_settings()
    models: list[dict] = []

    if _has_key(settings.dashscope_api_key):
        models.extend([
            {"model": "qwen3.5-plus", "provider": "dashscope", "desc": "千问3.5 Plus，均衡推荐"},
            {"model": "qwen3-max-2026-01-23", "provider": "dashscope", "desc": "千问3 Max，强推理"},
            {"model": "qwen3.5-122b-a10b", "provider": "dashscope", "desc": "千问3.5 MoE，大规模"},
            {"model": "qwen-plus", "provider": "dashscope", "desc": "千问 Plus，经典稳定"},
            {"model": "qwen-turbo", "provider": "dashscope", "desc": "千问 Turbo，快速轻量"},
        ])

    if _has_key(settings.deepseek_api_key):
        models.extend([
            {"model": "deepseek-chat", "provider": "deepseek", "desc": "Deepseek V3"},
            {"model": "deepseek-reasoner", "provider": "deepseek", "desc": "Deepseek R1 推理"},
        ])

    if _has_key(settings.anthropic_api_key):
        models.append({"model": "claude-sonnet-4-20250514", "provider": "anthropic", "desc": "Claude Sonnet 4"})

    if _has_key(settings.openai_api_key):
        models.extend([
            {"model": "gpt-4o", "provider": "openai", "desc": "GPT-4o"},
            {"model": "gpt-4o-mini", "provider": "openai", "desc": "GPT-4o Mini"},
        ])

    return models
