import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from app.config import get_settings


@dataclass
class SourceBlockData:
    key: str
    block_type: str
    text: str
    locator: dict = field(default_factory=dict)
    confidence: float | None = None


@dataclass
class ParseResult:
    parser_name: str
    parser_version: str
    blocks: list[SourceBlockData]
    warnings: list[str] = field(default_factory=list)
    markdown_content: str = ""


class ParserError(RuntimeError):
    pass


def _markdown_blocks(text: str) -> list[SourceBlockData]:
    blocks: list[SourceBlockData] = []
    current: list[str] = []
    current_start = 1
    section: list[str] = []

    def flush(end_line: int):
        nonlocal current
        content = "\n".join(current).strip()
        if content:
            key = f"line-{current_start}-{end_line}"
            block_type = "table" if "|" in content and "\n" in content else "paragraph"
            blocks.append(
                SourceBlockData(
                    key=key,
                    block_type=block_type,
                    text=content,
                    locator={"line_start": current_start, "line_end": end_line, "section_path": section.copy()},
                    confidence=1.0,
                )
            )
        current = []

    lines = text.splitlines()
    for index, line in enumerate(lines, start=1):
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            flush(index - 1)
            level = len(heading.group(1))
            section[:] = section[: level - 1]
            section.append(heading.group(2))
            blocks.append(
                SourceBlockData(
                    key=f"heading-{index}",
                    block_type="heading",
                    text=heading.group(2),
                    locator={"line": index, "level": level, "section_path": section.copy()},
                    confidence=1.0,
                )
            )
            current_start = index + 1
        elif line.strip():
            if not current:
                current_start = index
            current.append(line)
        else:
            flush(index - 1)
            current_start = index + 1
    flush(len(lines))
    return blocks


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ParserError("DOCX 文件结构损坏") from exc
    root = ElementTree.fromstring(xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for paragraph in root.findall(".//w:p", namespace):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _pdf_text(path: Path) -> tuple[str, list[SourceBlockData]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ParserError("未安装 pypdf，无法解析 PDF") from exc
    reader = PdfReader(str(path))
    blocks: list[SourceBlockData] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            blocks.append(
                SourceBlockData(
                    key=f"page-{page_number}",
                    block_type="page",
                    text=text,
                    locator={"page_number": page_number},
                    confidence=0.95,
                )
            )
    return "\n\n".join(block.text for block in blocks), blocks


def _pdf_markdown(blocks: list[SourceBlockData]) -> str:
    return "\n\n".join(f"## 第 {block.locator['page_number']} 页\n\n{block.text}" for block in blocks) + ("\n" if blocks else "")


def _mineru_parse(path: Path) -> ParseResult:
    settings = get_settings()
    output_dir = Path(tempfile.mkdtemp(prefix="mineru-"))
    try:
        tier = "flash" if path.suffix.lower() == ".docx" else settings.mineru_pdf_tier
        output_file = output_dir / "parsed.md"
        command = [
            settings.mineru_command,
            "parse",
            str(path),
            "-o",
            str(output_file),
            "--tier",
            tier,
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=settings.mineru_timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "MinerU 解析失败").strip()
            raise ParserError(message[-1000:])
        markdown = next(output_dir.rglob("*.md"), None)
        if markdown is None:
            json_file = next(output_dir.rglob("*.json"), None)
            if json_file is not None:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                content = data.get("content") if isinstance(data, dict) else None
                markdown = output_dir / "result.md"
                markdown.write_text(content or json.dumps(data, ensure_ascii=False), encoding="utf-8")
            else:
                raise ParserError("MinerU 未产生 Markdown 或 JSON 结果")
        markdown_content = markdown.read_text(encoding="utf-8")
        blocks = _markdown_blocks(markdown_content)
        for block in blocks:
            block.locator["parser"] = "mineru"
        return ParseResult("mineru", "configured", blocks, markdown_content=markdown_content)
    except FileNotFoundError as exc:
        raise ParserError(f"找不到 MinerU 命令：{settings.mineru_command}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ParserError("MinerU 解析超时") from exc
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def parse_document(path: str, extension: str) -> ParseResult:
    source = Path(path)
    if not source.exists():
        raise ParserError("原始文件不存在")

    # MinerU is opt-in so a fresh clone remains runnable without model downloads.
    mineru_warning = []
    if extension in {".pdf", ".docx"} and get_settings().mineru_command:
        try:
            return _mineru_parse(source)
        except ParserError as exc:
            if get_settings().mineru_required:
                raise
            mineru_warning.append(f"MinerU 未完成解析，已尝试本地回退：{exc}")

    if extension in {".md", ".markdown", ".txt"}:
        content = source.read_text(encoding="utf-8")
        return ParseResult("markdown-local", "1.0", _markdown_blocks(content), markdown_content=content)
    if extension == ".docx":
        content = _docx_text(source)
        markdown_content = "\n\n".join(content.splitlines()) + ("\n" if content else "")
        return ParseResult("docx-local", "1.0", _markdown_blocks(content), mineru_warning, markdown_content)
    if extension == ".pdf":
        _, blocks = _pdf_text(source)
        warnings = mineru_warning + ["PDF 使用本地文本提取；扫描页、公式和复杂表格可能需要 MinerU。"]
        if not blocks:
            warnings.append("未提取到文本，建议配置 MinerU OCR 或上传文本型 PDF。")
        return ParseResult("pdf-local", "1.0", blocks, warnings, _pdf_markdown(blocks))
    raise ParserError(f"暂不支持的文件格式：{extension}")
