import csv
import io
import json
import zipfile
from xml.sax.saxutils import escape

from app.schemas import ModelSnapshot


def _svg(model: ModelSnapshot) -> bytes:
    if not model.modules:
        return b'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="240"><text x="40" y="80">No modules identified</text></svg>'
    width = max(560, 240 * len(model.modules) + 80)
    height = 360
    blocks = []
    positions = {}
    for index, module in enumerate(model.modules):
        x = 40 + index * 240
        positions[module.id] = (x, 100)
    index_by_id = {module.id: index for index, module in enumerate(model.modules)}
    for connection in model.connections:
        source = positions.get(connection.source_module)
        target = positions.get(connection.target_module)
        if source is None or target is None:
            continue
        x1, y1 = source[0] + 180, source[1] + 40
        x2, y2 = target[0], target[1] + 40
        if index_by_id[connection.target_module] <= index_by_id[connection.source_module]:
            blocks.append(f'<path d="M{x1} {y1} C{x1 + 50} 320, {x2 - 50} 320, {x2} {y2}" fill="none" stroke="#58677d" stroke-width="2" marker-end="url(#arrow)"/>')
        else:
            blocks.append(f'<path d="M{x1} {y1} L{x2} {y2}" fill="none" stroke="#58677d" stroke-width="2" marker-end="url(#arrow)"/>')
    for module in model.modules:
        x, y = positions[module.id]
        blocks.append(f'<rect x="{x}" y="{y}" width="180" height="80" rx="6" fill="#eef4ff" stroke="#315a9b"/>')
        blocks.append(f'<text x="{x + 90}" y="{y + 34}" text-anchor="middle" font-size="16" fill="#1e2a3a">{escape(module.id)}</text>')
        blocks.append(f'<text x="{x + 90}" y="{y + 58}" text-anchor="middle" font-size="13" fill="#1e2a3a">{escape(module.name[:16])}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#58677d"/></marker></defs>'
            + ''.join(blocks) + '</svg>').encode("utf-8")


def _csv_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    def safe_cell(value: object) -> object:
        if isinstance(value, (int, float)):
            return value
        text = str(value) if value is not None else ""
        return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text

    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows([[safe_cell(value) for value in row] for row in rows])
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def build_artifacts(model: ModelSnapshot, formats: list[str]) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    if "json" in formats:
        result["model.json"] = json.dumps(model.model_dump(), ensure_ascii=False, indent=2).encode("utf-8")
    if "md" in formats:
        result["需求解析结果.md"] = _markdown(model).encode("utf-8")
    if "svg" in formats:
        result["Simulink模型结构图.svg"] = _svg(model)
    if "csv" in formats:
        result["模块清单.csv"] = _csv_bytes(
            ["编号", "模块名称", "推荐模块类型", "输入", "输出", "说明"],
            [[m.id, m.name, m.simulink_type, ",".join(m.inputs), ",".join(m.outputs), m.description] for m in model.modules],
        )
        result["模块参数表.csv"] = _csv_bytes(
            ["模块编号", "参数名称", "参数值", "单位", "来源状态"],
            [[p.owner_id or "", p.name, p.value if p.value is not None else "未提供", p.unit, p.status] for p in model.parameters],
        )
        result["连线关系表.csv"] = _csv_bytes(
            ["起点模块/端口", "终点模块/端口", "信号名称", "信号含义"],
            [[f"{c.source_module}/{c.source_port}", f"{c.target_module}/{c.target_port}", c.signal_name, c.meaning] for c in model.connections],
        )
    return result


def build_zip(model: ModelSnapshot, formats: list[str]) -> bytes:
    files = build_artifacts(model, formats)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


def _markdown(model: ModelSnapshot) -> str:
    lines = [f"# {model.system_name}", "", f"- 领域：{model.domain}", f"- 控制目标：{model.control_objective}", f"- 模型状态：{model.buildability_status}", "", "## 模块清单", ""]
    for module in model.modules:
        lines.append(f"- **{module.id} {module.name}**：`{module.simulink_type}`；{module.description}")
    lines += ["", "## 方程", ""]
    if model.equations:
        lines += ["| 编号 | 表达式 | 状态 | 证据 |", "|---|---|---|---|"]
        for equation in model.equations:
            evidence = ", ".join(equation.get("evidence", []))
            lines.append(f"| {equation.get('id', '')} | `{equation.get('expression', '')}` | {equation.get('status', '待确认')} | {evidence} |")
    else:
        lines.append("未识别到方程。")
    lines += ["", "## 参数", "", "| 模块 | 参数 | 值 | 单位 | 状态 |", "|---|---|---|---|---|"]
    for parameter in model.parameters:
        value = parameter.value if parameter.value is not None else "未提供"
        lines.append(f"| {parameter.owner_id or '-'} | {parameter.name} | {value} | {parameter.unit or '单位未知'} | {parameter.status} |")
    lines += ["", "## 连线", "", "| 起点 | 终点 | 信号 | 含义 |", "|---|---|---|---|"]
    for connection in model.connections:
        lines.append(f"| {connection.source_module}/{connection.source_port} | {connection.target_module}/{connection.target_port} | {connection.signal_name} | {connection.meaning} |")
    lines += ["", "## 待确认项", ""]
    lines.extend(f"- {question.get('question', '')}" for question in model.open_questions)
    return "\n".join(lines) + "\n"
