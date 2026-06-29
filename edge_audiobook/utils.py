"""
工具函数 —— 编码检测、文本分块、文件操作等。
"""

from __future__ import annotations

import os
import re
from typing import List, Optional


def detect_encoding(file_path: str) -> str:
    """检测文件编码。"""
    try:
        import charset_normalizer
        result = charset_normalizer.from_path(file_path)
        return result.best().encoding if result.best() else "utf-8"
    except ImportError:
        pass

    try:
        import chardet
        with open(file_path, "rb") as f:
            raw = f.read(100000)
        result = chardet.detect(raw)
        return result.get("encoding", "utf-8") or "utf-8"
    except ImportError:
        pass

    # 最终回退
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            f.read(100)
        return "utf-8"
    except UnicodeDecodeError:
        return "latin-1"


def split_text_into_chunks(
    text: str,
    max_chars: int = 2000,
    prefer_paragraphs: bool = True,
) -> List[str]:
    """
    将长文本分割成小块，适合 TTS API 处理。

    - 优先在段落边界（双换行）分割
    - 如果段落太长，按句子边界分割
    - 如果句子也太长，强制在 max_chars 处截断
    """
    if not text or not text.strip():
        return []

    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []

    if prefer_paragraphs:
        # 按段落分割
        paragraphs = re.split(r"\n\s*\n", text)
        current = ""
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if not current:
                current = para
            elif len(current) + len(para) + 2 <= max_chars:
                current += "\n\n" + para
            else:
                # 当前块满了
                if len(current) > max_chars:
                    chunks.extend(_force_split(current, max_chars))
                else:
                    chunks.append(current)
                current = para

        if current:
            if len(current) > max_chars:
                chunks.extend(_force_split(current, max_chars))
            else:
                chunks.append(current)
    else:
        chunks = _force_split(text, max_chars)

    return [c for c in chunks if c.strip()]


def _force_split(text: str, max_chars: int) -> List[str]:
    """强制按句子边界分割文本。"""
    # 按句子边界分割
    sentences = re.split(r"(?<=[.!?。！？])\s+", text)
    chunks: List[str] = []
    current = ""

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if not current:
            current = sent
        elif len(current) + len(sent) + 1 <= max_chars:
            current += " " + sent
        else:
            if current:
                if len(current) > max_chars:
                    # 单句超过限制，强制截断
                    for i in range(0, len(current), max_chars):
                        chunk = current[i:i + max_chars].strip()
                        if chunk:
                            chunks.append(chunk)
                else:
                    chunks.append(current)
            current = sent

    if current:
        if len(current) > max_chars:
            for i in range(0, len(current), max_chars):
                chunk = current[i:i + max_chars].strip()
                if chunk:
                    chunks.append(chunk)
        else:
            chunks.append(current)

    return chunks


def sanitize_filename(name: str, max_len: int = 80) -> str:
    """清理文件名，去掉非法字符。"""
    # 替换非法字符
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    # 压缩空白
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name or "untitled"


def format_time(seconds: float) -> str:
    """将秒数格式化为可读字符串。"""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m{s}s"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h}h{m}m{s}s"
