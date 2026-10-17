from app.schemas import ModelSnapshot
from app.services.exporter import build_artifacts, build_zip
from app.services.extraction import _fallback_model
from app.services.parsing import _markdown_blocks
from app.services.validation import validate_model


def test_markdown_parser_preserves_sections_and_tables():
    blocks = _markdown_blocks("# 电机\n\n## 参数\n\n| 名称 | 值 |\n|---|---|\n| R | 2.4 |\n")

    assert blocks[0].block_type == "heading"
    assert blocks[0].text == "电机"
    assert any(block.block_type == "table" for block in blocks)
    table = next(block for block in blocks if block.block_type == "table")
    assert table.locator["section_path"] == ["电机", "参数"]


def test_fallback_extraction_creates_traceable_motor_model():
    blocks = [
        {"id": "src-1", "text": "速度给定经过误差计算、PI控制器和电机模型，速度反馈返回误差计算。"},
        {"id": "src-2", "text": "电枢电阻 R=2.4 Ohm，采样时间 0.001 s。"},
    ]
    snapshot = ModelSnapshot.model_validate(_fallback_model(blocks))

    assert snapshot.domain == "motor_control"
    assert {module.id for module in snapshot.modules} >= {"B01", "B02", "B03", "B04", "B05"}
    assert all(evidence in {"src-1", "src-2"} for module in snapshot.modules for evidence in module.evidence)


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
