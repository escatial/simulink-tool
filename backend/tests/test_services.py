from app.schemas import ModelSnapshot
from app.services.exporter import build_artifacts, build_zip
from app.services.extraction import _extract_equations, _extract_modules, _fallback_model
from app.services.llm import _strip_json_fence
from app.services.parsing import _markdown_blocks
from app.services.validation import validate_model


def test_markdown_parser_preserves_sections_and_tables():
    blocks = _markdown_blocks("# 电机\n\n## 参数\n\n| 名称 | 值 |\n|---|---|\n| R | 2.4 |\n")

    assert blocks[0].block_type == "heading"
    assert blocks[0].text == "电机"
    assert any(block.block_type == "table" for block in blocks)
    table = next(block for block in blocks if block.block_type == "table")
    assert table.locator["section_path"] == ["电机", "参数"]


def test_fallback_extraction_does_not_invent_motor_template():
    blocks = [
        {"id": "src-1", "text": "论文研究柔性梁动力学。"},
        {"id": "src-2", "text": "m=2.4 kg\nJ x'' + c x' + kx = F。"},
    ]
    snapshot = ModelSnapshot.model_validate(_fallback_model(blocks))

    assert snapshot.domain == "unknown"
    assert all(parameter.name not in {"电枢电阻", "电感", "转动惯量"} for parameter in snapshot.parameters)
    assert any(equation["expression"].startswith("J") for equation in snapshot.equations)
    assert {evidence for equation in snapshot.equations for evidence in equation["evidence"]} <= {"src-1", "src-2"}


def test_fallback_ignores_prose_as_equations_or_modules():
    blocks = [{
        "id": "src-paper",
        "text": "关系。记式中 x 轴方向小车的阻力为 fx = μx ẋ，Y 轴方向大车。\n"
        "本文运用 Simulink 搭建 PID 控制器对起重机模型进行仿真。\n"
        "模糊 PID 控制器用于抑制摆角。",
    }]

    assert _extract_equations(blocks) == []
    names = [item["name"] for item in _extract_modules(blocks)]
    assert set(names) == {"PID 控制器", "模糊 PID 控制器"}


def test_llm_json_parser_ignores_reasoning_prefix():
    raw = "<think>先分析，再输出 JSON。</think>\n```json\n{\"ok\": true}\n```"
    assert _strip_json_fence(raw) == '{"ok": true}'


def test_validation_reports_missing_parameters_and_invalid_evidence():
    snapshot = ModelSnapshot(
        system_name="测试",
        domain="motor_control",
        modules=[{"id": "B01", "name": "输入", "simulink_type": "Step", "evidence": ["missing"]}],
        parameters=[{"id": "P01", "name": "惯量", "status": "missing", "unit": "kg*m^2"}],
    )
    result = validate_model(snapshot, {"src-1"})

    codes = {issue["code"] for issue in result["issues"]}
    assert "invalid_evidence" in codes
    assert "missing_parameter" in codes
    assert result["buildability_status"] == "补充参数后可搭建"


def test_validation_checks_equation_evidence():
    snapshot = ModelSnapshot(
        system_name="论文模型",
        modules=[{"id": "M01", "name": "模型", "simulink_type": "Subsystem"}],
        equations=[{"id": "E01", "expression": "x = u", "evidence": ["missing"]}],
    )
    result = validate_model(snapshot, {"src-1"})

    assert any(issue["code"] == "invalid_evidence" for issue in result["issues"])


def test_export_contains_expected_artifacts():
    snapshot = ModelSnapshot(
        system_name="测试",
        modules=[{"id": "B01", "name": "输入", "simulink_type": "Step"}],
    )
    files = build_artifacts(snapshot, ["svg", "csv", "md", "json"])
    archive = build_zip(snapshot, ["svg", "csv", "md", "json"])

    assert "Simulink模型结构图.svg" in files
    assert "模块清单.csv" in files
    assert "需求解析结果.md" in files
    assert archive[:2] == b"PK"
