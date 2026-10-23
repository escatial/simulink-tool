import re
from typing import Any

from app.schemas import ModelSnapshot
from app.services.llm import LLMUnavailable, extract_with_llm


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
NAME = r"[A-Za-z\u4e00-\u9fff][A-Za-z0-9_\-\u4e00-\u9fff ()/]{0,48}"
PARAM_NAME = r"(?:[A-Za-z][A-Za-z0-9_]{0,15}|[\u03bc\u00b5\u0394\u03b8\u03c6][A-Za-z0-9_]{0,12})"
PROSE_MARKERS = ("式中", "其中", "关系", "为了", "如图", "图", "表", "本文", "可以", "分别", "有效", "控制在")


def _looks_like_equation(expression: str) -> bool:
    value = re.sub(r"\s+", " ", expression.strip().strip("`$"))
    value = value.rstrip("，。；;、")
    if not value or len(value) > 180 or "=" not in value:
        return False
    if any(marker in value for marker in PROSE_MARKERS):
        return False
    if sum(1 for char in value if "\u4e00" <= char <= "\u9fff") > 3:
        return False
    first = value[0]
    if not (first.isalpha() or first in "(\\$[{"):
        return False
    if not re.search(r"[=+*/^()'′]", value):
        return False
    return True


def _module_name_is_credible(name: str) -> bool:
    value = re.sub(r"\s+", " ", name.strip(" -:，,。；;()（）"))
    if not value or len(value) > 40:
        return False
    if any(marker in value for marker in ("作为", "通过", "为了", "可以", "相比", "不同", "其中", "本文", "of ", "under ")):
        return False
    return value.lower() not in {"simulink", "pid", "pd"}


def _all_text(blocks: list[dict[str, Any]]) -> str:
    return "\n".join(str(block.get("text", "")) for block in blocks)


def _first_title(blocks: list[dict[str, Any]]) -> str:
    for block in blocks:
        text = str(block.get("text", "")).strip()
        if not text:
            continue
        if block.get("type") == "heading" or text.startswith("#"):
            return re.sub(r"^#+\s*", "", text.splitlines()[0]).strip()[:160]
        first_line = text.splitlines()[0].strip()
        if len(first_line) <= 100:
            return first_line
    return "未识别论文模型"


def _extract_equations(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    equations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for block in blocks:
        block_id = str(block["id"])
        for line in str(block.get("text", "")).splitlines():
            expression = line.strip().strip("`$")
            if not _looks_like_equation(expression):
                continue
            if re.fullmatch(rf"{PARAM_NAME}\s*=\s*{NUMBER}\s*[A-Za-z/%*^._-]*", expression.rstrip("，。；;")):
                continue
            key = (expression, block_id)
            if key in seen:
                continue
            seen.add(key)
            equations.append({"id": f"E{len(equations) + 1:02d}", "expression": expression, "evidence": [block_id], "status": "extracted"})
    return equations


def _extract_parameters(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parameters: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, value: str, unit: str, block_id: str):
        key = (name.strip().lower(), value)
        if key in seen or not name.strip():
            return
        seen.add(key)
        parameters.append({
            "id": f"P{len(parameters) + 1:02d}",
            "owner_id": None,
            "name": name.strip().strip("|:-"),
            "value": value,
            "unit": unit.strip(),
            "status": "confirmed_from_document" if unit.strip() else "unit_unknown",
            "origin": "extracted",
            "evidence": [block_id],
        })

    for block in blocks:
        block_id = str(block["id"])
        for line in str(block.get("text", "")).splitlines():
            cells = [cell.strip() for cell in line.split("|")]
            if len(cells) >= 3:
                for cell_index, cell in enumerate(cells[1:], start=1):
                    match = re.fullmatch(rf"({NUMBER})\s*([A-Za-z\u4e00-\u9fff/%*^._-]*)", cell)
                    if match and cells[0] and not re.fullmatch(r":?-+:?", cells[0]):
                        unit = match.group(2) or next((item for item in cells[cell_index + 1:] if item and not re.fullmatch(rf"{NUMBER}", item)), "")
                        add(cells[0], match.group(1), unit, block_id)
                        break
            match = re.search(rf"^\s*({PARAM_NAME})\s*(?:=|:|：)\s*({NUMBER})\s*([A-Za-z/%*^._-]*)\s*$", line)
            if match:
                add(*match.groups(), block_id)
    return parameters


def _extract_signals(blocks: list[dict[str, Any]], keyword: str) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    pattern = re.compile(rf"(?:{keyword})\s*[:：=]\s*([^。\n;；]+)", re.IGNORECASE)
    for block in blocks:
        for match in pattern.finditer(str(block.get("text", ""))):
            name = match.group(1).strip()
            if name:
                signals.append({"name": name[:120], "meaning": "论文原文明确给出的信号", "evidence": [str(block["id"])]})
    return signals[:20]


def _extract_modules(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    seen: set[str] = set()
    patterns = (
        (re.compile(r"桥式?起重机的?四自由度模型|起重机四自由度模型"), "桥式起重机四自由度模型"),
        (re.compile(r"模糊\s*PID\s*控制器", re.IGNORECASE), "模糊 PID 控制器"),
        (re.compile(r"PID\s*控制器", re.IGNORECASE), "PID 控制器"),
        (re.compile(r"PD\s*控制器", re.IGNORECASE), "PD 控制器"),
        (re.compile(r"模糊控制系统", re.IGNORECASE), "模糊控制系统"),
        (re.compile(r"PID\s*调节器", re.IGNORECASE), "PID 调节器"),
        (re.compile(r"(?:fuzzy\s+PID|PID|PD)\s+controller", re.IGNORECASE), None),
    )
    for block in blocks:
        text = str(block.get("text", ""))
        for pattern, fixed_name in patterns:
            for match in pattern.finditer(text):
                name = fixed_name or re.sub(r"\s+", " ", match.group(0)).strip()
                if not _module_name_is_credible(name) or name.lower() in seen:
                    continue
                seen.add(name.lower())
                modules.append({
                    "id": f"M{len(modules) + 1:02d}",
                    "name": name,
                    "simulink_type": "Subsystem",
                    "inputs": [],
                    "outputs": [],
                    "description": "论文中显式提及的组件，具体实现需要根据方程确认",
                    "origin": "extracted",
                    "evidence": [str(block["id"])],
                })
    return modules


def _simulation_config(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    text = _all_text(blocks)
    patterns = {
        "start_time": rf"(?:start\s*time|开始时间|起始时间)\s*[:：=]\s*({NUMBER})",
        "stop_time": rf"(?:stop\s*time|结束时间|仿真时间)\s*[:：=]\s*({NUMBER})",
        "sample_time": rf"(?:sample\s*time|采样时间|步长)\s*[:：=]\s*({NUMBER})",
        "solver": r"(?:solver|求解器)\s*[:：=]\s*([A-Za-z0-9_-]+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            config[key] = match.group(1)
    return config


def _fallback_model(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract only source-backed facts; never synthesize a domain template."""
    equations = _extract_equations(blocks)
    parameters = _extract_parameters(blocks)
    modules = _extract_modules(blocks)
    input_signals = _extract_signals(blocks, r"输入|input")
    output_signals = _extract_signals(blocks, r"输出|output")
    simulation_config = _simulation_config(blocks)

    questions = []
    if not equations:
        questions.append({"id": "Q_EQUATIONS", "question": "未识别到可用于仿真的数学方程，请确认论文模型表达。", "severity": "blocking", "evidence": []})
    if not modules:
        questions.append({"id": "Q_MODULES", "question": "未识别到明确的模型模块或组件边界，请补充仿真结构。", "severity": "blocking", "evidence": []})
    if not parameters:
        questions.append({"id": "Q_PARAMETERS", "question": "未识别到带数值的模型参数，请补充参数表或参数取值。", "severity": "blocking", "evidence": []})
    if not simulation_config:
        questions.append({"id": "Q_SIMULATION", "question": "未识别到仿真时间、求解器或步长，请确认仿真配置。", "severity": "warning", "evidence": []})

    return {
        "schema_version": "1.0",
        "system_name": _first_title(blocks),
        "domain": "unknown",
        "control_objective": "",
        "input_signals": input_signals,
        "output_signals": output_signals,
        "modules": modules,
        "parameters": parameters,
        "connections": [],
        "equations": equations,
        "simulation_config": simulation_config,
        "assumptions": [],
        "open_questions": questions,
        "validation": {"status": "needs_review", "issues": []},
        "buildability_status": "仅供参考",
    }


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop sentence fragments that an LLM or PDF text extractor labeled as model facts."""
    cleaned = dict(payload)
    cleaned["equations"] = [
        item for item in cleaned.get("equations", [])
        if isinstance(item, dict) and _looks_like_equation(str(item.get("expression", "")))
    ]
    cleaned["modules"] = [
        item for item in cleaned.get("modules", [])
        if isinstance(item, dict) and _module_name_is_credible(str(item.get("name", "")))
    ]
    return cleaned


def _stringify_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("value", "statement", "description", "name"):
            if value.get(key) is not None:
                return _stringify_value(value[key])
        if "primary" in value:
            return _stringify_value(value["primary"])
        if "primary_objective" in value:
            return _stringify_value(value["primary_objective"])
        if isinstance(value.get("primary"), dict):
            return _stringify_value(value["primary"].get("description"))
    return str(value)


def _normalize_endpoint(value: Any) -> tuple[str, str]:
    text = _stringify_value(value).strip()
    if not text:
        return "", ""
    if "." in text and not text.split(".", 1)[1].isdigit():
        module, port = text.split(".", 1)
        return module, port
    return text, ""


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Map common LLM synonyms onto the stable snapshot schema."""
    cleaned = _sanitize_payload(payload)
    for key in ("system_name", "domain", "control_objective"):
        cleaned[key] = _stringify_value(cleaned.get(key))
    parameters = []
    for index, item in enumerate(cleaned.get("parameters", []), start=1):
        if not isinstance(item, dict):
            continue
        parameters.append({
            "id": item.get("id") or f"P{index:02d}",
            **item,
            "value": _stringify_value(item.get("value")),
            "unit": _stringify_value(item.get("unit")),
        })
    cleaned["parameters"] = parameters
    connections = []
    for item in cleaned.get("connections", []):
        if not isinstance(item, dict):
            continue
        source, target = (item.get("source_module"), item.get("target_module")) if "source_module" in item else (item.get("from"), item.get("to"))
        if source is None and target is None:
            continue
        source_module, source_port = _normalize_endpoint(source)
        target_module, target_port = _normalize_endpoint(target)
        connections.append({
            "id": item.get("id") or f"C{len(connections) + 1:02d}",
            "source_module": source_module,
            "source_port": source_port,
            "target_module": target_module,
            "target_port": target_port,
            "signal_name": _stringify_value(item.get("signal_name")) or _stringify_value(item.get("signal")),
            "evidence": item.get("evidence", []),
        })
    cleaned["connections"] = connections
    if not isinstance(cleaned.get("simulation_config"), dict):
        cleaned["simulation_config"] = {}
    cleaned["modules"] = [
        {
            "id": item.get("id") or item.get("module_id"),
            **item,
        }
        for item in cleaned.get("modules", []) if isinstance(item, dict)
    ]
    return cleaned


async def extract_model(blocks: list[dict[str, Any]]) -> tuple[ModelSnapshot, str]:
    try:
        source = await extract_with_llm(blocks)
    except LLMUnavailable:
        source = None
    origin = "llm" if source else "fallback_rules"
    payload = _normalize_payload(source or _fallback_model(blocks))
    payload.setdefault("open_questions", [])

    def add_question(question_id: str, question: str, severity: str) -> None:
        if not any(item.get("id") == question_id for item in payload["open_questions"] if isinstance(item, dict)):
            payload["open_questions"].append({"id": question_id, "question": question, "severity": severity, "evidence": []})

    if not payload.get("equations"):
        add_question("Q_EQUATIONS", "未识别到可信的数学方程，请根据原文公式确认。", "blocking")
    if not payload.get("modules"):
        add_question("Q_MODULES", "未识别到可信的模块边界，请根据论文结构确认。", "blocking")
    if not source and get_llm_configured():
        add_question("Q_LLM", "LLM 抽取未成功，当前结果来自证据抽取回退，请人工复核。", "blocking")
    return ModelSnapshot.model_validate(payload), origin


def get_llm_configured() -> bool:
    from app.config import get_settings

    settings = get_settings()
    return settings.llm_enabled and bool(settings.llm_api_key and settings.llm_model)
