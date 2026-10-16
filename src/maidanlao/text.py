"""将模型常见 Markdown 输出转换为 QQ 可读的纯文本。"""

import json
import re


def strip_internal_metadata(text: str) -> str:
    """删除误复制的内部来源JSON（含旧历史）；不删除正常的“来源元数据”说明文字。"""
    lines = plain_text(text).splitlines()
    kept = []
    index = 0
    while index < len(lines):
        match = re.match(r"^\s*来源元数据\s*[:：]\s*(.*)$", lines[index])
        if match:
            payload = match.group(1)
            end = index
            while end + 1 < len(lines) and (not payload.strip() or payload.lstrip().startswith(("[", "{"))):
                try:
                    _, offset = json.JSONDecoder().raw_decode(payload.lstrip())
                    if payload.lstrip()[offset:].strip():
                        break
                    index = end + 1
                    break
                except json.JSONDecodeError:
                    end += 1
                    payload += "\n" + lines[end]
            else:
                try:
                    json.loads(payload)
                    index = end + 1
                except json.JSONDecodeError:
                    kept.append(lines[index])
                    index += 1
                continue
            if index == end + 1:
                continue
        kept.append(lines[index])
        index += 1
    return "\n".join(kept).strip()


def with_references(text: str, sources: list[dict]) -> str:
    """来源统一由服务端生成；移除模型尾部来源段落并按标题去重。

    正文中的《文档标题》引用保留，只识别独立的来源标题行。
    没有实际来源时也移除模型生成的来源，防止凭空列出文档。
    """
    body = strip_internal_metadata(text)
    footer = re.search(r"(?m)^\s*(?:参考来源|参考资料|资料来源|来源)(?:\s*[:：].*|\s*)$", body)
    if footer:
        body = body[: footer.start()].rstrip()
    titles = list(
        dict.fromkeys(str(source["source_title"]).strip() for source in sources if source.get("source_title"))
    )
    if titles:
        body += "\n\n参考来源：\n" + "\n".join(titles)
    return body.strip()


def plain_text(text: str) -> str:
    lines = []
    fenced = False
    for line in text.splitlines():
        if re.match(r"^\s*(`{3,}|~{3,})", line):
            fenced = not fenced
            continue
        if fenced:
            lines.append(line)
            continue
        line = re.sub(r"!?(\[([^\]\n]+)\])\(([^\s)]+)\)", r"\2（\3）", line)
        line = re.sub(r"`([^`\n]+)`", r"\1", line)
        for marker in (r"\*\*", "__", "~~"):
            line = re.sub(marker + r"(.+?)" + marker, r"\1", line)
        line = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", line)
        line = re.sub(r"(?<![A-Za-z0-9_])_([^_\n]+)_(?![A-Za-z0-9_])", r"\1", line)
        line = re.sub(r"^\s{0,3}#{1,6}\s+(.+?)(?:\s+#+)?$", r"\1", line)
        line = re.sub(r"^\s*>\s?", "", line)
        line = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", line)
        line = re.sub(r"^(\s*)(\d+)[.)]\s+", r"\1\2）", line)
        if re.fullmatch(r"\s*(?:[-*_]\s*){3,}", line):
            continue
        if line.strip().startswith("|") and line.strip().endswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            line = "；".join(cells)
        lines.append(line)
    return "\n".join(lines).strip()
