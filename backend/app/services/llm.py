import asyncio
import json
import re
from typing import Any

import httpx

from app.config import get_settings
from app.services.storage import get_selected_llm_model


SYSTEM_PROMPT = """你是期刊论文数学模型与仿真方案抽取助手。
只根据输入的论文证据片段抽取，不补造论文没有给出的参数、方程、模块、连线、初始条件或仿真配置。
输出 JSON，字段包括 system_name、domain、control_objective、input_signals、output_signals、modules、parameters、connections、equations、simulation_config、assumptions、open_questions。
每个模块、参数、信号、连线和方程都必须在 evidence 数组中引用输入的 source block id；无法确认的内容放入 open_questions。
参数只有在论文给出明确数值时才使用 confirmed_from_document，否则使用 missing 或 needs_confirmation。
equations 中保留论文公式表达、变量和证据，不要将公式改写成未经确认的数值模型。
modules 中的 simulink_type 只能使用 Gain、Sum、Product、Integrator、Derivative、Transfer Fcn、State-Space、Saturation、Switch、Unit Delay、Step、Constant、Scope、Subsystem；无法映射时使用 Subsystem 并在 open_questions 中说明。
"""


class LLMUnavailable(RuntimeError):
    pass


async def fetch_upstream_models() -> list[dict[str, str]]:
    settings = get_settings()
    if not settings.llm_api_key:
        raise LLMUnavailable("未配置 MiniMax API Key")
    url = settings.llm_base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {settings.llm_api_key}"})
            response.raise_for_status()
        payload = response.json()
        items = payload.get("data", payload.get("models", [])) if isinstance(payload, dict) else []
        return [
            {"id": str(item["id"]), "name": str(item.get("id", ""))}
            for item in items
            if isinstance(item, dict) and item.get("id")
        ]
    except (httpx.HTTPError, ValueError) as exc:
        raise LLMUnavailable("从 MiniMax 上游获取模型列表失败") from exc


def _strip_json_fence(text: str) -> str:
    value = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", value, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1)
    start = value.find("{")
    if start >= 0:
        decoder = json.JSONDecoder()
        try:
            _, end = decoder.raw_decode(value[start:])
            return value[start : start + end]
        except json.JSONDecodeError:
            pass
    return value


async def extract_with_llm(blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
    settings = get_settings()
    model = get_selected_llm_model(settings.llm_model)
    if not settings.llm_enabled or not settings.llm_api_key or not model:
        return None
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15, read=settings.llm_timeout_seconds, write=30, pool=15)) as client:
        return await _extract_chunk(client, url, settings, model, blocks)


async def _extract_chunk(client: httpx.AsyncClient, url: str, settings: Any, model: str, blocks: list[dict[str, Any]]) -> Any:
    evidence = "\n\n".join(f"[{b['id']}] {b['text']}" for b in blocks)
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": evidence},
        ],
        "response_format": {"type": "json_object"},
    }
    payload["stream"] = True
    try:
        content_parts: list[str] = []
        async with client.stream("POST", url, headers={"Authorization": f"Bearer {settings.llm_api_key}"}, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content")
                except (KeyError, IndexError, json.JSONDecodeError):
                    continue
                if delta:
                    content_parts.append(delta)
        content = "".join(content_parts)
        return json.loads(_strip_json_fence(content))
    except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
        raise LLMUnavailable("LLM 抽取失败") from exc
