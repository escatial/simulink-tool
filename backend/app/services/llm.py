import json
import re
from typing import Any

import httpx

from app.config import get_settings


SYSTEM_PROMPT = """你是控制系统需求结构化助手。只根据给出的证据片段抽取信息，不补造缺失参数。
输出 JSON，字段包括 system_name、domain、control_objective、input_signals、output_signals、modules、parameters、connections、equations、simulation_config、assumptions、open_questions。
每个模块、参数和连接都要在 evidence 数组中引用输入的 source block id；无法确认的内容放入 open_questions。
modules 中的 simulink_type 只能使用 Gain、Sum、Product、Integrator、Derivative、Transfer Fcn、State-Space、Saturation、Switch、Unit Delay、Step、Constant、Scope、Subsystem。
"""


class LLMUnavailable(RuntimeError):
    pass


def _strip_json_fence(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    return match.group(1) if match else text.strip()


async def extract_with_llm(blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.llm_enabled or not settings.llm_api_key or not settings.llm_model:
        return None
    evidence = "\n\n".join(f"[{b['id']}] {b['text']}" for b in blocks)
    payload = {
        "model": settings.llm_model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": evidence},
        ],
        "response_format": {"type": "json_object"},
    }
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json=payload,
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        result = json.loads(_strip_json_fence(content))
        if not isinstance(result, dict):
            raise LLMUnavailable("LLM 返回的 JSON 不是对象")
        return result
    except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
        raise LLMUnavailable("LLM 抽取失败") from exc
