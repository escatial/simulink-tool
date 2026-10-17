import re
from typing import Any

from app.schemas import ModelSnapshot
from app.services.llm import LLMUnavailable, extract_with_llm


MODULE_TYPES = {
    "pi": "Gain",
    "pid": "Gain",
    "比例": "Gain",
    "积分": "Integrator",
    "微分": "Derivative",
    "饱和": "Saturation",
    "限幅": "Saturation",
    "电机": "State-Space",
    "scope": "Scope",
    "示波器": "Scope",
}


def _all_text(blocks: list[dict[str, Any]]) -> str:
    return "\n".join(block["text"] for block in blocks)


def _evidence_for(blocks: list[dict[str, Any]], pattern: str) -> list[str]:
    regex = re.compile(pattern, re.IGNORECASE)
    return [block["id"] for block in blocks if regex.search(block["text"])][:3]


def _fallback_model(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    text = _all_text(blocks)
    lower = text.lower()
    motor = any(word in lower or word in text for word in ("电机", "motor", "转速", "电枢"))
    domain = "motor_control" if motor else "generic_control"
    system_name = "直流电机速度闭环" if motor and ("速度" in text or "转速" in text) else "未命名控制系统"
    modules: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_module(module_id: str, name: str, simulink_type: str, evidence_pattern: str, description: str = ""):
        if module_id in seen:
            return
        seen.add(module_id)
        modules.append(
            {
                "id": module_id,
                "name": name,
                "simulink_type": simulink_type,
                "inputs": [],
                "outputs": [],
                "description": description,
                "origin": "inferred",
                "evidence": _evidence_for(blocks, evidence_pattern),
            }
        )

    if motor:
        add_module("B01", "给定值", "Step", r"给定|参考|reference|setpoint", "速度或位置给定")
        add_module("B02", "误差计算", "Sum", r"误差|error|反馈", "给定值减反馈值")
        if "pid" in lower or "pid" in text.lower():
            add_module("B03", "PID 控制器", "Gain", r"pid|比例|积分|微分", "建议按 PID 参数拆分或替换为现有控制器实现")
        elif "积分" in text:
            add_module("B03", "PI 控制器", "Gain", r"比例|积分|pi", "建议使用 Gain 与 Integrator 组合")
        else:
            add_module("B03", "控制器", "Gain", r"控制器|controller|调节器", "控制器类型需要用户确认")
        add_module("B04", "电机模型", "State-Space", r"电机|motor|电气方程|机械方程", "根据电气和机械方程配置")
        add_module("B05", "反馈环节", "Gain", r"反馈|测速|位置反馈|传感器", "反馈增益或传感器模型")
        add_module("B06", "Scope", "Scope", r"scope|示波器|观测|输出", "观察速度、位置或电流")
        connections = [
            {"source_module": "B01", "source_port": "out1", "target_module": "B02", "target_port": "in1", "signal_name": "给定值", "meaning": "参考输入", "evidence": _evidence_for(blocks, r"给定|参考")},
            {"source_module": "B02", "source_port": "out1", "target_module": "B03", "target_port": "in1", "signal_name": "误差", "meaning": "跟踪误差", "evidence": _evidence_for(blocks, r"误差")},
            {"source_module": "B03", "source_port": "out1", "target_module": "B04", "target_port": "in1", "signal_name": "控制量", "meaning": "电压或执行输入", "evidence": _evidence_for(blocks, r"控制器|电压|电流")},
            {"source_module": "B04", "source_port": "out1", "target_module": "B05", "target_port": "in1", "signal_name": "输出反馈", "meaning": "电机速度、位置或电流", "evidence": _evidence_for(blocks, r"速度|转速|位置|反馈")},
            {"source_module": "B05", "source_port": "out1", "target_module": "B02", "target_port": "in2", "signal_name": "反馈值", "meaning": "负反馈信号", "evidence": _evidence_for(blocks, r"反馈")},
        ]
    else:
        add_module("B01", "输入", "Step", r"输入|给定|参考|input")
        add_module("B02", "被控对象", "Transfer Fcn", r"对象|系统|plant|传递函数")
        add_module("B03", "输出观测", "Scope", r"输出|观测|scope")
        connections = [
            {"source_module": "B01", "source_port": "out1", "target_module": "B02", "target_port": "in1", "signal_name": "输入", "meaning": "系统输入", "evidence": _evidence_for(blocks, r"输入")},
            {"source_module": "B02", "source_port": "out1", "target_module": "B03", "target_port": "in1", "signal_name": "输出", "meaning": "系统输出", "evidence": _evidence_for(blocks, r"输出")},
        ]

    parameters: list[dict[str, Any]] = []
    parameter_patterns = [
        ("电枢电阻", "ohm", r"电枢电阻|电阻"),
        ("电感", "H", r"电感"),
        ("转动惯量", "kg*m^2", r"转动惯量|惯量"),
        ("粘性摩擦系数", "N*m*s", r"摩擦|粘性"),
        ("反电动势常数", "V*s/rad", r"反电动势|电势常数"),
        ("转矩常数", "N*m/A", r"转矩常数"),
        ("采样时间", "s", r"采样时间|sample time"),
        ("仿真结束时间", "s", r"仿真时间|结束时间|stop time"),
    ]
    for index, (name, unit, pattern) in enumerate(parameter_patterns, start=1):
        evidence = _evidence_for(blocks, pattern)
        value = None
        if evidence:
            match = re.search(rf"{pattern}[^\d-]*([-+]?\d+(?:\.\d+)?)", text, re.IGNORECASE)
            value = match.group(1) if match else None
        parameters.append(
            {
                "id": f"P{index:02d}",
                "owner_id": "B04" if motor else None,
                "name": name,
                "value": value,
                "unit": unit,
                "status": "confirmed_from_document" if value is not None else "missing",
                "origin": "extracted" if value is not None else "inferred",
                "evidence": evidence,
            }
        )

    questions = [
        {"id": "Q01", "question": "请确认系统的输入、输出和反馈信号定义。", "severity": "blocking", "evidence": []},
        {"id": "Q02", "question": "请补充所有状态方程所需的关键参数和初始条件。", "severity": "blocking", "evidence": []},
    ]
    if motor and not any(param["value"] is not None for param in parameters[:6]):
        questions.append({"id": "Q03", "question": "未提取到足够的电机参数，电机模型只能作为结构建议。", "severity": "blocking", "evidence": []})

    return {
        "schema_version": "1.0",
        "system_name": system_name,
        "domain": domain,
        "control_objective": "跟踪给定信号并观察系统响应" if motor else "根据文档确定控制目标",
        "input_signals": [{"name": "给定值", "meaning": "参考输入", "evidence": _evidence_for(blocks, r"给定|输入|参考")}],
        "output_signals": [{"name": "系统输出", "meaning": "需要在 Simulink 中观察的输出", "evidence": _evidence_for(blocks, r"输出|速度|转速|位置")}],
        "modules": modules,
        "parameters": parameters,
        "connections": connections,
        "equations": [],
        "simulation_config": {"start_time": "0", "stop_time": None, "solver": "待确认", "sample_time": None},
        "assumptions": [{"id": "A01", "text": "模型结构由文档语义和基础控制模板推断，需用户确认。", "evidence": []}],
        "open_questions": questions,
        "validation": {"status": "needs_review", "issues": []},
        "buildability_status": "补充参数后可搭建" if motor else "结构存在歧义",
    }


async def extract_model(blocks: list[dict[str, Any]]) -> tuple[ModelSnapshot, str]:
    try:
        source = await extract_with_llm(blocks)
    except LLMUnavailable:
        source = None
    origin = "llm" if source else "fallback_rules"
    payload = source or _fallback_model(blocks)
    if not source and get_llm_configured():
        payload["open_questions"].append(
            {"id": "Q_LLM", "question": "LLM 抽取未成功，当前结果来自本地规则，请人工复核。", "severity": "blocking", "evidence": []}
        )
    return ModelSnapshot.model_validate(payload), origin


def get_llm_configured() -> bool:
    from app.config import get_settings

    settings = get_settings()
    return settings.llm_enabled and bool(settings.llm_api_key and settings.llm_model)
