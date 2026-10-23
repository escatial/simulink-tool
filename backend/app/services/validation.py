from app.schemas import ModelSnapshot


BLOCKING_STATUSES = {"missing", "unit_unknown", "multiple_candidates", "needs_confirmation"}


def validate_model(model: ModelSnapshot, source_ids: set[str]) -> dict:
    issues: list[dict] = []
    module_ids = {module.id for module in model.modules}
    evidence_items = [
        *model.modules,
        *model.parameters,
        *model.connections,
        *model.equations,
        *model.input_signals,
        *model.output_signals,
    ]
    for item in evidence_items:
        evidence_values = item.get("evidence", []) if isinstance(item, dict) else item.evidence
        for evidence in evidence_values:
            if evidence not in source_ids:
                issues.append({"code": "invalid_evidence", "severity": "error", "message": f"证据不存在：{evidence}"})

    for parameter in model.parameters:
        if parameter.status in BLOCKING_STATUSES:
            issues.append({"code": "missing_parameter", "severity": "error", "message": f"参数待确认：{parameter.name}"})
        if not parameter.unit:
            issues.append({"code": "unit_unknown", "severity": "warning", "message": f"参数单位未知：{parameter.name}"})

    for connection in model.connections:
        if connection.source_module not in module_ids or connection.target_module not in module_ids:
            issues.append({"code": "invalid_connection", "severity": "error", "message": "连线端点不存在"})

    for question in model.open_questions:
        if question.get("severity") == "blocking":
            issues.append({"code": "open_question", "severity": "error", "message": question.get("question", "存在待确认项")})

    if not model.modules:
        issues.append({"code": "no_modules", "severity": "error", "message": "未识别到模块"})

    errors = [issue for issue in issues if issue["severity"] == "error"]
    if model.domain not in {"motor_control", "temperature_control", "level_control", "mechanical", "generic_control"}:
        buildability = "仅供参考"
    elif any(issue["code"] == "invalid_connection" for issue in errors):
        buildability = "结构存在歧义"
    elif errors:
        buildability = "补充参数后可搭建"
    else:
        buildability = "可直接搭建"
    return {"status": "passed" if not errors else "needs_review", "issues": issues, "buildability_status": buildability}
