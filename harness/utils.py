# -*- coding: utf-8 -*-
"""通用工具：文件读写、分块、去连续重复、JSON 存取、课时 ID 解析。"""
import json
import re
from pathlib import Path

_ILLEGAL = r'[\\/:*?"<>|\n\r\t]'


def sanitize_filename(name: str) -> str:
    """把概念/主题标题转成安全的文件名（保留中文）。"""
    name = re.sub(_ILLEGAL, "", name or "").strip()
    name = re.sub(r"\s+", "", name)
    name = name.strip(".")
    return name[:80] if name else "untitled"


def read_lines(path) -> list:
    """读取转写文件，按行去空白、丢弃空行。"""
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]


def collapse_consecutive(lines: list) -> list:
    """折叠连续完全相同的行（Whisper 卡顿/字幕署名造成的重复）。"""
    out = []
    for ln in lines:
        if out and out[-1] == ln:
            continue
        out.append(ln)
    return out


def chunk_lines(lines: list, max_chars: int) -> list:
    """按行边界把行列表切成多块，每块总字符数不超过 max_chars。"""
    chunks, cur, n = [], [], 0
    for ln in lines:
        if cur and n + len(ln) > max_chars:
            chunks.append(cur)
            cur, n = [], 0
        cur.append(ln)
        n += len(ln) + 1
    if cur:
        chunks.append(cur)
    return chunks


def write_text(path, text: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")


def load_json(path, default):
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def lecture_id_from_path(path) -> str:
    """从文件名提取课时 ID，如 中国经济思想史_2026-03-02.txt -> 2026-03-02。"""
    stem = Path(path).stem
    m = re.search(r"(\d{4}-\d{2}-\d{2})", stem)
    return m.group(1) if m else stem


def parse_json_loose(s: str) -> dict:
    """尽量从模型输出里抠出一个 JSON 对象。"""
    s = (s or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl != -1 and s[:nl].lower().startswith(("json", "")):
            s = s[nl + 1:]
    try:
        return json.loads(s)
    except Exception:
        i, j = s.find("{"), s.rfind("}")
        if i != -1 and j != -1 and j > i:
            return json.loads(s[i:j + 1])
        raise
