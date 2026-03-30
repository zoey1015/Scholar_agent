"""
LLM 适配层

统一接口封装 Claude / GPT / Deepseek / Ollama 等 API 差异。
新增模型只需加一个 Adapter 子类。
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


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


class AnthropicAdapter(LLMAdapter):
    provider = "claude"

    def __init__(self):
        from backend.config import get_settings
        import anthropic
        self.client = anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)

    async def chat(self, model, messages, system="", temperature=0.7, max_tokens=4096) -> str:
        response = await self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            temperature=temperature,
        )
        return response.content[0].text


class OpenAIAdapter(LLMAdapter):
    provider = "openai"

    def __init__(self):
        from backend.config import get_settings
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=get_settings().openai_api_key)

    async def chat(self, model, messages, system="", temperature=0.7, max_tokens=4096) -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        response = await self.client.chat.completions.create(
            model=model, messages=msgs, temperature=temperature, max_tokens=max_tokens,
        )
        return response.choices[0].message.content


class DeepseekAdapter(LLMAdapter):
    provider = "deepseek"

    def __init__(self):
        from backend.config import get_settings
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(
            api_key=get_settings().deepseek_api_key,
            base_url="https://api.deepseek.com/v1",
        )

    async def chat(self, model, messages, system="", temperature=0.7, max_tokens=4096) -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        response = await self.client.chat.completions.create(
            model=model, messages=msgs, temperature=temperature, max_tokens=max_tokens,
        )
        return response.choices[0].message.content


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


class DashScopeAdapter(LLMAdapter):
    """阿里百炼（通义千问系列），兼容 OpenAI 接口格式"""

    provider = "qwen"

    def __init__(self):
        from backend.config import get_settings
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(
            api_key=get_settings().dashscope_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    async def chat(self, model, messages, system="", temperature=0.7, max_tokens=4096) -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        response = await self.client.chat.completions.create(
            model=model,
            messages=msgs,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content


# ========================
# 模型路由器
# ========================

# 模型名称前缀 → Adapter 类的映射
PROVIDER_MAP = {
    "claude": AnthropicAdapter,
    "gpt": OpenAIAdapter,
    "deepseek": DeepseekAdapter,
    "qwen": DashScopeAdapter,
    "ollama": OllamaAdapter,
}

# 缓存已实例化的 adapter（避免重复创建客户端）
_adapter_cache: dict[str, LLMAdapter] = {}


def resolve_adapter(model: str) -> tuple[LLMAdapter, str]:
    """
    根据模型名称解析对应的 Adapter 和实际模型名

    Examples:
        "claude-sonnet-4-20250514" → (AnthropicAdapter, "claude-sonnet-4-20250514")
        "gpt-4o"                  → (OpenAIAdapter, "gpt-4o")
        "deepseek-chat"           → (DeepseekAdapter, "deepseek-chat")
        "qwen-plus"               → (DashScopeAdapter, "qwen-plus")
        "ollama:qwen2"            → (OllamaAdapter, "qwen2")
    """
    for prefix, adapter_cls in PROVIDER_MAP.items():
        if model.startswith(prefix):
            if prefix not in _adapter_cache:
                _adapter_cache[prefix] = adapter_cls()
            return _adapter_cache[prefix], model

    raise ValueError(f"Unknown model provider for: {model}. Supported prefixes: {list(PROVIDER_MAP.keys())}")
