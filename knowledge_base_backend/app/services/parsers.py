from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


DOCX_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": DOCX_NS}


class ParseError(Exception):
    pass


@dataclass(slots=True)
class ParsedSection:
    id: str
    title: str
    level: int
    order_index: int = 0
    blocks: list[dict[str, Any]] = field(default_factory=list)
    children: list["ParsedSection"] = field(default_factory=list)


@dataclass(slots=True)
class ParsedDocument:
    full_text: str
    sections: list[ParsedSection]


def parse_document(file_path: Path) -> str:
    return parse_document_structure(file_path).full_text


def parse_document_structure(file_path: Path) -> ParsedDocument:
    suffix = file_path.suffix.lower()
    if suffix == ".docx":
        return _parse_docx_structure(file_path)
    if suffix == ".pdf":
        return _parse_pdf_structure(file_path)
    if suffix in {".xls", ".xlsx"}:
        return _parse_excel_structure(file_path)
    if suffix == ".json":
        return _parse_json_structure(file_path)
    if suffix == ".md":
        return _parse_markdown_structure(file_path)
    if suffix in {".txt", ".csv"}:
        return _parse_plain_structure(file_path)
    raise ParseError(
        f"暂不支持解析 {suffix} 文件，请优先上传 txt、md、json、csv、docx。"
    )


def _parse_pdf_structure(file_path: Path) -> ParsedDocument:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ParseError("PDF parser dependency is missing: pypdf") from exc

    try:
        reader = PdfReader(str(file_path))
    except Exception as exc:
        raise ParseError("PDF 文件损坏或无法读取") from exc

    sections: list[ParsedSection] = []
    page_texts: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if not text:
            continue
        page_texts.append(f"第 {index} 页\n{text}")
        sections.append(
            ParsedSection(
                id=f"page-{index}",
                title=f"第 {index} 页",
                level=1,
                order_index=index,
                blocks=[{"type": "paragraph", "text": text}],
            )
        )

    full_text = "\n\n".join(page_texts).strip()
    if not full_text:
        raise ParseError("PDF 未提取到可用文本，扫描件或图片型 PDF 暂不支持 OCR")
    return ParsedDocument(full_text=full_text, sections=sections)


def _parse_excel_structure(file_path: Path) -> ParsedDocument:
    suffix = file_path.suffix.lower()
    if suffix == ".xlsx":
        sheets = _read_xlsx_sheets(file_path)
    else:
        sheets = _read_xls_sheets(file_path)

    sections: list[ParsedSection] = []
    sheet_texts: list[str] = []
    for index, (sheet_name, rows) in enumerate(sheets, start=1):
        lines = ["\t".join(_format_cell(cell) for cell in row).rstrip() for row in rows]
        lines = [line for line in lines if line.strip()]
        if not lines:
            continue
        text = "\n".join(lines).strip()
        sheet_texts.append(f"# {sheet_name}\n{text}")
        sections.append(
            ParsedSection(
                id=f"sheet-{index}",
                title=sheet_name,
                level=1,
                order_index=index,
                blocks=[{"type": "paragraph", "text": text}],
            )
        )

    full_text = "\n\n".join(sheet_texts).strip()
    if not full_text:
        raise ParseError("Excel 文件未提取到可用文本")
    return ParsedDocument(full_text=full_text, sections=sections)


def _read_xlsx_sheets(file_path: Path) -> list[tuple[str, list[list[Any]]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ParseError("Excel parser dependency is missing: openpyxl") from exc

    try:
        workbook = load_workbook(file_path, read_only=True, data_only=True)
    except Exception as exc:
        raise ParseError("XLSX 文件损坏或无法读取") from exc

    try:
        result: list[tuple[str, list[list[Any]]]] = []
        for worksheet in workbook.worksheets:
            rows = [list(row) for row in worksheet.iter_rows(values_only=True)]
            result.append((worksheet.title, rows))
        return result
    finally:
        workbook.close()


def _read_xls_sheets(file_path: Path) -> list[tuple[str, list[list[Any]]]]:
    try:
        import xlrd
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ParseError("Excel parser dependency is missing: xlrd") from exc

    try:
        workbook = xlrd.open_workbook(str(file_path))
    except Exception as exc:
        raise ParseError("XLS 文件损坏或无法读取") from exc

    result: list[tuple[str, list[list[Any]]]] = []
    for worksheet in workbook.sheets():
        rows = [worksheet.row_values(row_index) for row_index in range(worksheet.nrows)]
        result.append((worksheet.name, rows))
    return result


def _format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _parse_plain_structure(file_path: Path) -> ParsedDocument:
    raw_text = file_path.read_text(encoding="utf-8", errors="ignore")
    if file_path.suffix.lower() == ".json":
        try:
            raw_text = json.dumps(json.loads(raw_text), ensure_ascii=False, indent=2)
        except json.JSONDecodeError as exc:
            raise ParseError("JSON 文件格式不合法") from exc

    normalized = raw_text.replace("\r\n", "\n").strip()
    paragraphs = [line.strip() for line in normalized.split("\n") if line.strip()]
    if not paragraphs and normalized:
        paragraphs = [normalized]

    section = ParsedSection(
        id="section-body",
        title="正文",
        level=1,
        order_index=0,
        blocks=[{"type": "paragraph", "text": paragraph} for paragraph in paragraphs],
    )
    return ParsedDocument(full_text=normalized, sections=[section] if paragraphs else [])


def _parse_markdown_structure(file_path: Path) -> ParsedDocument:
    raw_text = file_path.read_text(encoding="utf-8", errors="ignore")
    return _parse_markdown_text(raw_text)


def _parse_markdown_text(raw_text: str) -> ParsedDocument:
    normalized = raw_text.replace("\r\n", "\n").strip()
    if not normalized:
        return ParsedDocument(full_text="", sections=[])

    root = ParsedSection(id="root", title="ROOT", level=0)
    stack: list[ParsedSection] = [root]
    current_section: ParsedSection | None = None
    intro_section: ParsedSection | None = None
    section_index = 0
    paragraph_buffer: list[str] = []

    def flush_paragraph() -> None:
        nonlocal current_section, intro_section, section_index, paragraph_buffer
        text = "\n".join(part.strip() for part in paragraph_buffer if part.strip()).strip()
        paragraph_buffer = []
        if not text:
            return
        if current_section is None:
            if intro_section is None:
                section_index += 1
                intro_section = ParsedSection(
                    id="section-intro",
                    title="正文",
                    level=1,
                    order_index=section_index,
                )
                root.children.append(intro_section)
                root.blocks.append({"type": "section", "node": intro_section})
            current_section = intro_section
        current_section.blocks.append({"type": "paragraph", "text": text})

    for line in normalized.split("\n"):
        header_match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if header_match:
            flush_paragraph()
            level = len(header_match.group(1))
            title = header_match.group(2).strip()
            section_index += 1
            node = ParsedSection(
                id=f"section-{section_index}",
                title=title,
                level=level,
                order_index=section_index,
            )
            while stack and stack[-1].level >= level:
                stack.pop()
            parent = stack[-1] if stack else root
            parent.children.append(node)
            parent.blocks.append({"type": "section", "node": node})
            stack.append(node)
            current_section = node
            continue

        if not line.strip():
            flush_paragraph()
            continue

        paragraph_buffer.append(line.rstrip())

    flush_paragraph()
    return ParsedDocument(full_text=normalized, sections=root.children)


def _parse_json_structure(file_path: Path) -> ParsedDocument:
    return _parse_plain_structure(file_path)


def _parse_docx_structure(file_path: Path) -> ParsedDocument:
    try:
        with zipfile.ZipFile(file_path) as archive:
            document_xml = archive.read("word/document.xml")
            style_map = _load_style_map(archive)
    except KeyError as exc:
        raise ParseError("DOCX 文件结构不完整，缺少必要的 Word XML") from exc
    except zipfile.BadZipFile as exc:
        raise ParseError("DOCX 文件已损坏或不是合法的 Word 文档") from exc

    root = ElementTree.fromstring(document_xml)
    paragraphs = _extract_paragraphs(root, style_map)
    markdown = _render_paragraphs_as_markdown(paragraphs)
    sections = _build_sections_from_docx_paragraphs(paragraphs)
    return ParsedDocument(full_text=markdown, sections=sections)


def _load_style_map(archive: zipfile.ZipFile) -> dict[str, str]:
    try:
        styles_xml = archive.read("word/styles.xml")
    except KeyError:
        return {}

    root = ElementTree.fromstring(styles_xml)
    style_map: dict[str, str] = {}
    for style in root.findall(".//w:style", NS):
        style_id = style.attrib.get(f"{{{DOCX_NS}}}styleId", "")
        name_node = style.find("./w:name", NS)
        style_name = name_node.attrib.get(f"{{{DOCX_NS}}}val", "") if name_node is not None else ""
        if style_id:
            style_map[style_id] = style_name
    return style_map


def _extract_paragraphs(root: ElementTree.Element, style_map: dict[str, str]) -> list[dict[str, Any]]:
    paragraphs: list[dict[str, Any]] = []
    body = root.find("./w:body", NS)
    if body is None:
        body = root

    for child in list(body):
        if child.tag == f"{{{DOCX_NS}}}p":
            paragraph = _extract_paragraph_entry(child, style_map)
            if paragraph:
                paragraphs.append(paragraph)
        elif child.tag == f"{{{DOCX_NS}}}tbl":
            paragraphs.extend(_extract_table_paragraphs(child, style_map))
    return paragraphs


def _extract_table_paragraphs(table: ElementTree.Element, style_map: dict[str, str]) -> list[dict[str, Any]]:
    table_paragraphs: list[dict[str, Any]] = []
    for paragraph in table.findall(".//w:p", NS):
        paragraph_entry = _extract_paragraph_entry(paragraph, style_map, in_table=True)
        if paragraph_entry:
            table_paragraphs.append(paragraph_entry)
    return table_paragraphs


def _render_paragraphs_as_markdown(paragraphs: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for paragraph in paragraphs:
        text = str(paragraph.get("text", "")).strip()
        if not text:
            continue
        heading_level = int(paragraph.get("heading_level") or 0)
        if heading_level > 0:
            lines.append(f"{'#' * min(heading_level, 6)} {text}")
        else:
            lines.append(text)
        lines.append("")
    return "\n".join(lines).strip()


def _join_paragraph_texts(paragraphs: list[dict[str, Any]]) -> str:
    return "\n".join(
        str(paragraph.get("text", "")).strip()
        for paragraph in paragraphs
        if str(paragraph.get("text", "")).strip()
    ).strip()


def _build_sections_from_docx_paragraphs(paragraphs: list[dict[str, Any]]) -> list[ParsedSection]:
    top_candidates: list[tuple[int, tuple[int, ...], dict[str, Any]]] = []
    expected_top: int | None = None

    for index, paragraph in enumerate(paragraphs):
        style_heading_level = int(paragraph.get("style_heading_level") or 0)
        if style_heading_level != 1:
            continue
        numeric = _extract_numeric_path(str(paragraph.get("text", "")))

        if numeric and len(numeric) == 1:
            top_number = numeric[0]
            if expected_top is None:
                expected_top = top_number
            if top_number != expected_top:
                continue
            top_candidates.append((index, numeric, paragraph))
            expected_top = top_number + 1
            continue

        # Fallback: some manuals use a real heading-1 style but omit the visible number.
        if expected_top is not None:
            top_candidates.append((index, (expected_top,), paragraph))
            expected_top += 1

    sections: list[ParsedSection] = []
    if not top_candidates:
        return _build_sections(paragraphs)

    for top_pos, (start_index, numeric, paragraph) in enumerate(top_candidates):
        end_index = top_candidates[top_pos + 1][0] if top_pos + 1 < len(top_candidates) else len(paragraphs)
        top_number = numeric[0]
        section = ParsedSection(
            id=f"section-top-{top_number}",
            title=str(paragraph.get("text", "")).strip(),
            level=1,
            order_index=len(sections) + 1,
        )

        second_candidates: list[tuple[int, tuple[int, ...], dict[str, Any]]] = []
        for use_style_heading in (True, False):
            expected_second = 1
            second_candidates = []
            for child_index in range(start_index + 1, end_index):
                child_paragraph = paragraphs[child_index]
                style_heading_level = int(child_paragraph.get("style_heading_level") or 0)
                detected_heading_level = int(child_paragraph.get("heading_level") or 0)
                if use_style_heading:
                    if style_heading_level != 2:
                        continue
                elif detected_heading_level != 2:
                    continue

                child_numeric = _extract_numeric_path(str(child_paragraph.get("text", "")))
                if not child_numeric or len(child_numeric) != 2:
                    continue
                if child_numeric[0] != top_number or child_numeric[1] != expected_second:
                    continue
                second_candidates.append((child_index, child_numeric, child_paragraph))
                expected_second += 1
            if second_candidates or not use_style_heading:
                break

        intro_start = start_index + 1
        intro_end = second_candidates[0][0] if second_candidates else end_index
        intro_text = _join_paragraph_texts(paragraphs[intro_start:intro_end])
        if intro_text:
            section.blocks.append({"type": "paragraph", "text": intro_text})

        for second_pos, (child_index, child_numeric, child_paragraph) in enumerate(second_candidates):
            child_end = second_candidates[second_pos + 1][0] if second_pos + 1 < len(second_candidates) else end_index
            child_text = _join_paragraph_texts(paragraphs[child_index + 1 : child_end])
            child = ParsedSection(
                id=f"section-{top_number}-{child_numeric[1]}",
                title=str(child_paragraph.get("text", "")).strip(),
                level=2,
                order_index=second_pos + 1,
            )
            if child_text:
                child.blocks.append({"type": "paragraph", "text": child_text})
            section.children.append(child)
            section.blocks.append({"type": "section", "node": child})

        sections.append(section)

    return sections


def _extract_paragraph_entry(
    paragraph: ElementTree.Element,
    style_map: dict[str, str],
    *,
    in_table: bool = False,
) -> dict[str, Any] | None:
    texts = [node.text for node in paragraph.findall(".//w:t", NS) if node.text]
    text = "".join(texts).strip()
    if not text:
        return None

    ppr = paragraph.find("./w:pPr", NS)
    style_id = ""
    style_name = ""
    numbering_level: int | None = None
    if ppr is not None:
        style_node = ppr.find("./w:pStyle", NS)
        if style_node is not None:
            style_id = style_node.attrib.get(f"{{{DOCX_NS}}}val", "")
            style_name = style_map.get(style_id, "")
        num_pr = ppr.find("./w:numPr", NS)
        if num_pr is not None:
            ilvl = num_pr.find("./w:ilvl", NS)
            if ilvl is not None:
                val = ilvl.attrib.get(f"{{{DOCX_NS}}}val")
                if val and val.isdigit():
                    numbering_level = int(val) + 1

    style_heading_level = 0 if in_table else _level_from_style(style_id, style_name)
    heading_level = 0 if in_table else _detect_heading_level(text, style_id, style_name, numbering_level)
    return {
        "text": text,
        "style_id": style_id,
        "style_name": style_name,
        "style_heading_level": style_heading_level,
        "heading_level": heading_level,
        "in_table": in_table,
    }


def _detect_heading_level(
    text: str,
    style_id: str,
    style_name: str,
    numbering_level: int | None,
    *,
    in_table: bool = False,
) -> int:
    if in_table:
        return 0

    level = _level_from_style(style_id, style_name)
    if level:
        return level

    if numbering_level:
        return numbering_level

    return _level_from_text(text)


def _extract_numeric_path(text: str) -> tuple[int, ...] | None:
    stripped = text.strip()
    match = re.match(r"^(\d+(?:\.\d+)*)(?=[^\d.]|$)", stripped)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _numeric_path_matches_context(candidate: tuple[int, ...], current: tuple[int, ...] | None) -> bool:
    if not current:
        return True

    common = min(len(candidate), len(current))
    for index in range(common):
        left = candidate[index]
        right = current[index]
        if left != right:
            return left > right
    return len(candidate) > len(current)


def _has_later_expected_child(
    paragraphs: list[dict[str, Any]],
    start_index: int,
    top_number: int,
    expected_child_number: int,
) -> bool:
    for paragraph in paragraphs[start_index + 1 :]:
        candidate = _extract_numeric_path(paragraph["text"])
        if not candidate or len(candidate) < 2:
            continue
        if candidate[0] != top_number:
            continue
        if candidate[1] == expected_child_number:
            return True
    return False


def _level_from_style(style_id: str, style_name: str) -> int:
    candidates = [style_id, style_name]
    for candidate in candidates:
        lowered = candidate.lower()
        if not candidate:
            continue
        if "title" in lowered or "heading" in lowered:
            match = re.search(r"(\d+)", candidate)
            if match:
                return max(1, int(match.group(1)))
            return 1
        if "标题" in candidate or "標題" in candidate:
            match = re.search(r"(\d+)", candidate)
            if match:
                return max(1, int(match.group(1)))
            return 1
    return 0


def _level_from_text(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0

    if re.match(r"^第[一二三四五六七八九十百千0-9]+[章节篇卷部]\b", stripped):
        return 1
    if re.match(r"^第[一二三四五六七八九十百千0-9]+[节]\b", stripped):
        return 2
    if re.match(r"^第[一二三四五六七八九十百千0-9]+[条款项]\b", stripped):
        return 3

    numeric = re.match(r"^(\d+(?:\.\d+){0,4})[、.．)\]]?", stripped)
    if numeric:
        return min(len(numeric.group(1).split(".")), 5)

    if re.match(r"^[一二三四五六七八九十]+、", stripped):
        return 1
    if re.match(r"^（[一二三四五六七八九十]+）", stripped):
        return 2
    if re.match(r"^[（(][0-9一二三四五六七八九十]+[)）]", stripped):
        return 2

    return 0


def _has_body_or_child_heading(paragraphs: list[dict[str, Any]], start_index: int) -> bool:
    current_level = int(paragraphs[start_index]["heading_level"] or 0)
    for paragraph in paragraphs[start_index + 1 :]:
        next_level = int(paragraph["heading_level"] or 0)
        if next_level == 0:
            return True
        if next_level > current_level:
            return True
        if next_level <= current_level:
            return False
    return False


def _build_sections(paragraphs: list[dict[str, Any]]) -> list[ParsedSection]:
    root = ParsedSection(id="root", title="ROOT", level=0)
    stack: list[ParsedSection] = [root]
    current_section: ParsedSection | None = None
    intro_section: ParsedSection | None = None
    current_top_number: int | None = None
    current_child_number: int = 0
    uses_numeric_mode = any(
        _extract_numeric_path(paragraph["text"]) is not None
        for paragraph in paragraphs
        if int(paragraph["heading_level"] or 0) > 0
    )
    section_index = 0

    for paragraph_index, paragraph in enumerate(paragraphs):
        text = paragraph["text"]
        heading_level = int(paragraph["heading_level"] or 0)
        candidate_numeric_path = _extract_numeric_path(text) if heading_level > 0 else None
        is_section_heading = False

        if uses_numeric_mode and candidate_numeric_path:
            if len(candidate_numeric_path) <= 2:
                if len(candidate_numeric_path) == 1:
                    top_number = candidate_numeric_path[0]
                    if current_top_number is None:
                        is_section_heading = True
                    elif top_number == current_top_number + 1:
                        expected_child_number = current_child_number + 1 if current_top_number is not None else 1
                        is_section_heading = not _has_later_expected_child(
                            paragraphs,
                            paragraph_index,
                            current_top_number,
                            expected_child_number,
                        )
                elif current_top_number == candidate_numeric_path[0]:
                    expected_child_number = current_child_number + 1 if current_top_number is not None else 1
                    is_section_heading = candidate_numeric_path[1] == expected_child_number
        elif heading_level > 0:
            # Non-numeric headings only participate when the document does not use numbered sections.
            is_section_heading = _has_body_or_child_heading(paragraphs, paragraph_index)

        if is_section_heading:
            section_index += 1
            node = ParsedSection(
                id=f"section-{section_index}",
                title=text,
                level=heading_level,
                order_index=section_index,
            )
            while stack and stack[-1].level >= heading_level:
                stack.pop()
            parent = stack[-1] if stack else root
            parent.children.append(node)
            parent.blocks.append({"type": "section", "node": node})
            stack.append(node)
            current_section = node
            if candidate_numeric_path:
                current_top_number = candidate_numeric_path[0]
                current_child_number = candidate_numeric_path[1] if len(candidate_numeric_path) > 1 else 0
            else:
                current_top_number = None
                current_child_number = 0
            continue

        if current_section is None:
            if intro_section is None:
                section_index += 1
                intro_section = ParsedSection(
                    id="section-intro",
                    title="正文",
                    level=1,
                    order_index=section_index,
                )
                root.children.append(intro_section)
                root.blocks.append({"type": "section", "node": intro_section})
            current_section = intro_section

        current_section.blocks.append({"type": "paragraph", "text": text})

    return root.children


def _render_sections(sections: list[ParsedSection], depth: int = 0) -> list[str]:
    lines: list[str] = []
    for section in sections:
        lines.extend(_render_section(section, depth))
    return lines


def _render_section(section: ParsedSection, depth: int) -> list[str]:
    indent = "  " * depth
    lines: list[str] = []
    lines.append(f"{indent}{section.title}")
    for block in section.blocks:
        if block["type"] == "paragraph":
            lines.append(f"{indent}  {block['text']}")
        elif block["type"] == "section":
            lines.extend(_render_section(block["node"], depth + 1))
    return lines
