from __future__ import annotations

# 使用 lru_cache 缓存 OpenAI 兼容客户端，避免每次请求都重复创建连接对象。
from functools import lru_cache
# Iterable 仅用于类型标注，说明 messages 可以是可迭代对象。
from typing import Iterable

# MiniMax 当前通过 OpenAI 兼容接口接入，所以直接使用 openai SDK。
from openai import OpenAI

# 从配置中读取模型名称、API Key 和基础地址。
from app.config import MINIMAX_MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL


class MinimaxConfigError(RuntimeError):
    # 当模型配置缺失时抛出该异常，由上层统一降级处理。
    pass


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    # 如果没有 API Key，直接抛出配置异常，避免发起无意义请求。
    if not OPENAI_API_KEY:
        raise MinimaxConfigError(
            "Missing OPENAI_API_KEY or MINIMAX_API_KEY. Set it before starting the service."
        )
    # 返回 OpenAI 兼容客户端，base_url 指向 MiniMax 接口地址。
    return OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)


def _collect_text_from_messages(messages: Iterable[dict]) -> str:
    # 该函数用于把 messages 中的文本提取出来。
    # 当前主流程没有强依赖它，但保留作为调试或后续埋点的辅助函数。
    parts: list[str] = []
    # 遍历每条 message。
    for message in messages:
        # 取出 content 字段。
        content = message.get("content")
        # 如果本身就是字符串，直接加入。
        if isinstance(content, str):
            parts.append(content)
            continue
        # 如果 content 是多段结构化内容列表，则继续提取 type=text 的片段。
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
    # 最终把所有文本拼接成一个字符串返回。
    return "".join(parts)


def chat_completion(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_output_tokens: int = 2048,
    extra_body: dict | None = None,
) -> str:
    # 获取已缓存的客户端实例。
    client = get_client()
    # 发起非流式对话补全请求。
    response = client.chat.completions.create(
        # 如果调用方没传模型，则使用配置中的默认模型。
        model=model or MINIMAX_MODEL_NAME,
        # 传入 messages。
        messages=messages,
        # 控制随机性。
        temperature=temperature,
        # 兼容 OpenAI SDK 的字段名仍然是 max_tokens。
        max_tokens=max_output_tokens,
        # 透传 MiniMax 扩展参数，例如 reasoning_split。
        extra_body=extra_body or {},
    )
    # 读取第一条候选的回答正文。
    text = response.choices[0].message.content or ""
    # 去掉首尾空白后返回。
    return text.strip()


def stream_chat_completion(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_output_tokens: int = 2048,
    extra_body: dict | None = None,
):
    # 获取客户端实例。
    client = get_client()
    # 发起流式对话补全请求。
    stream = client.chat.completions.create(
        model=model or MINIMAX_MODEL_NAME,
        messages=messages,
        temperature=temperature,
        max_tokens=max_output_tokens,
        stream=True,
        extra_body=extra_body or {},
    )
    # 原样逐块向上层产出 chunk，由上层决定如何解析和转发。
    for chunk in stream:
        yield chunk
