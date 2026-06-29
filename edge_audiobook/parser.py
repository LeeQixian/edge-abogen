"""
书本解析器 —— 从 abogen 精简而来。
支持 EPUB、PDF、Markdown、TXT。
移除了 spaCy、LLM 等重量级依赖，仅保留核心解析逻辑。
"""

from __future__ import annotations

import os
import re
import logging
import textwrap
import urllib.parse
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, NavigableString
import fitz  # PyMuPDF
import markdown

from .utils import detect_encoding

# ---------------------------------------------------------------------------
# 文本清理工具
# ---------------------------------------------------------------------------
_PUNCTUATION_STRIP_RE = re.compile(r"[.!?。！？,，、；;：:\"\"''「」『』【】（）\(\)\[\]{}《》<>]")


def clean_text(text: str) -> str:
    """基础文本清理：去掉控制字符，统一空白。"""
    if not text:
        return ""
    # 删除控制字符（保留换行和制表符）
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)
    # 统一空白
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    # 统一换行
    cleaned = re.sub(r"\r\n?", "\n", cleaned)
    # 压缩多余换行
    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)
    return cleaned.strip()


def calculate_text_length(text: str) -> int:
    """计算文本的字符数（用于进度估算）。"""
    return len(text)


# 预编译正则
_BRACKETED_NUMBERS = re.compile(r"\[\s*\d+\s*\]")
_STANDALONE_PAGE_NUMBERS = re.compile(r"^\s*\d+\s*$", re.MULTILINE)
_PAGE_NUMBERS_AT_END = re.compile(r"\s+\d+\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# 章节数据结构
# ---------------------------------------------------------------------------
class Chapter:
    """简单的章节数据类。"""
    __slots__ = ("title", "text", "index")

    def __init__(self, title: str, text: str, index: int = 0):
        self.title = title
        self.text = text
        self.index = index

    def __repr__(self):
        return f"Chapter(title={self.title!r}, chars={len(self.text)})"


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------
class BaseBookParser(ABC):
    def __init__(self, book_path: str):
        self.book_path = os.path.normpath(os.path.abspath(book_path))
        self.chapters: List[Chapter] = []
        self.metadata: Dict[str, str] = {}

    @abstractmethod
    def parse(self) -> None:
        """解析书本，填充 self.chapters 和 self.metadata。"""
        ...

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ---------------------------------------------------------------------------
# EPUB 解析器
# ---------------------------------------------------------------------------
class EpubParser(BaseBookParser):
    def parse(self) -> None:
        try:
            book = epub.read_epub(self.book_path)
        except KeyError:
            # 尝试修补缺失文件
            self._patch_and_retry()
            book = epub.read_epub(self.book_path)

        self.metadata = self._extract_metadata(book)
        spine_docs = self._get_spine(book)
        doc_content = self._load_documents(book, spine_docs)

        # 尝试从 nav 获取结构，失败则用 spine 顺序
        nav_entries = self._try_parse_nav(book, spine_docs, doc_content)
        if nav_entries:
            self.chapters = self._build_chapters_from_nav(nav_entries, doc_content, spine_docs)
        else:
            self.chapters = self._build_chapters_from_spine(spine_docs, doc_content)

        # 如果没有章节，整个当一章
        if not self.chapters:
            all_text = "\n\n".join(
                self._html_to_text(html) for html in doc_content.values() if html
            )
            if all_text.strip():
                self.chapters = [Chapter(title="Content", text=all_text, index=0)]

    def _patch_and_retry(self) -> None:
        import types
        from ebooklib import epub as _epub_module
        reader_class = _epub_module.EpubReader
        orig = reader_class.read_file

        def safe_read(self, name):
            try:
                return orig(self, name)
            except KeyError:
                logging.warning(f"Missing file in EPUB: {name}")
                return b""

        reader_class.read_file = safe_read
        try:
            epub.read_epub(self.book_path)
        finally:
            reader_class.read_file = orig

    def _extract_metadata(self, book) -> Dict[str, str]:
        meta: Dict[str, str] = {}
        try:
            meta["title"] = book.get_metadata("DC", "title")[0][0]
        except Exception:
            meta["title"] = os.path.splitext(os.path.basename(self.book_path))[0]
        try:
            meta["author"] = book.get_metadata("DC", "creator")[0][0]
        except Exception:
            meta["author"] = "Unknown"
        try:
            meta["language"] = book.get_metadata("DC", "language")[0][0]
        except Exception:
            meta["language"] = "en"
        return meta

    def _get_spine(self, book) -> List[str]:
        docs = []
        for item_id, _ in book.spine:
            item = book.get_item_with_id(item_id)
            if item:
                docs.append(item.get_name())
        return docs

    def _load_documents(self, book, spine_docs: List[str]) -> Dict[str, str]:
        content: Dict[str, str] = {}
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            href = item.get_name()
            if href in spine_docs:
                try:
                    content[href] = item.get_content().decode("utf-8", errors="ignore")
                except Exception:
                    content[href] = ""
        return content

    def _try_parse_nav(self, book, spine_docs, doc_content) -> Optional[List[dict]]:
        """尝试从导航文件中提取章节结构。"""
        # 查找 nav 文件
        nav_item = None
        for item in book.get_items_of_type(ebooklib.ITEM_NAVIGATION):
            name = item.get_name().lower()
            if name.endswith((".xhtml", ".html", ".htm")):
                nav_item = item
                break
        if not nav_item:
            for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                try:
                    html = item.get_content().decode("utf-8", errors="ignore")
                    if "<nav" in html and 'epub:type="toc"' in html:
                        nav_item = item
                        break
                except Exception:
                    continue
        if not nav_item:
            return None

        try:
            nav_html = nav_item.get_content().decode("utf-8", errors="ignore")
            soup = BeautifulSoup(nav_html, "html.parser")
        except Exception:
            return None

        # 查找 toc nav
        toc_nav = soup.find("nav", attrs={"epub:type": "toc"})
        if not toc_nav:
            for nav in soup.find_all("nav"):
                if nav.find("ol"):
                    toc_nav = nav
                    break
        if not toc_nav:
            return None

        entries: List[dict] = []
        ol = toc_nav.find("ol")
        if not ol:
            return None

        for li in ol.find_all("li", recursive=False):
            self._parse_nav_li(li, entries)

        return entries if entries else None

    def _parse_nav_li(self, li, entries: List[dict]) -> None:
        a_tag = li.find("a")
        if a_tag and a_tag.get("href"):
            href = a_tag["href"]
            title = a_tag.get_text(strip=True) or "Untitled"
            entries.append({"title": title, "src": href})
        # 递归子列表
        child_ol = li.find("ol")
        if child_ol:
            for child_li in child_ol.find_all("li", recursive=False):
                self._parse_nav_li(child_li, entries)

    def _build_chapters_from_nav(
        self, nav_entries, doc_content, spine_docs
    ) -> List[Chapter]:
        # 构建 doc_order 映射
        doc_order = {href: i for i, href in enumerate(spine_docs)}

        # 解析每个 entry 的 href
        resolved: List[Tuple[str, str, int]] = []  # (title, doc_href, position)
        for entry in nav_entries:
            src = entry["src"]
            base_href, _, fragment = src.partition("#")
            # 查找文档
            doc_href = self._find_doc(base_href, doc_content, doc_order)
            if not doc_href:
                continue
            position = self._find_position(doc_content.get(doc_href, ""), fragment)
            resolved.append((entry["title"], doc_href, position))

        if not resolved:
            return []

        # 按文档顺序 + 位置排序
        resolved.sort(key=lambda x: (doc_order.get(x[1], 9999), x[2]))

        chapters: List[Chapter] = []
        for idx, (title, doc_href, pos) in enumerate(resolved):
            html = doc_content.get(doc_href, "")
            slice_html = html[pos:]
            text = self._html_to_text(slice_html)
            if text.strip():
                chapters.append(Chapter(title=title, text=text, index=idx))

        return chapters

    def _build_chapters_from_spine(
        self, spine_docs, doc_content
    ) -> List[Chapter]:
        chapters: List[Chapter] = []
        for idx, doc_href in enumerate(spine_docs):
            html = doc_content.get(doc_href, "")
            text = self._html_to_text(html)
            if text.strip():
                # 尝试用第一个标题作为章节名
                title_match = re.search(r"^(.+?)(?:\n|$)", text)
                title = title_match.group(1).strip() if title_match else f"Section {idx + 1}"
                chapters.append(Chapter(title=title, text=text, index=idx))
        return chapters

    def _find_doc(self, base_href, doc_content, doc_order) -> Optional[str]:
        """查找文档路径（处理 URL 编码等变体）。"""
        candidates = [base_href, urllib.parse.unquote(base_href)]
        base_lower = os.path.basename(base_href).lower()
        for href in doc_content:
            if os.path.basename(href).lower() == base_lower:
                candidates.append(href)
        for c in candidates:
            if c in doc_content:
                return c
        return None

    def _find_position(self, html: str, fragment: str) -> int:
        if not fragment:
            return 0
        patterns = [
            f'id="{re.escape(fragment)}"',
            f'name="{re.escape(fragment)}"',
            f"id='{re.escape(fragment)}'",
            f"name='{re.escape(fragment)}'",
        ]
        for pat in patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                # 找到包含该 id 的标签开始位置
                pos = html.rfind("<", 0, m.start())
                return pos if pos >= 0 else m.start()
        return 0

    def _html_to_text(self, html: str) -> str:
        if not html:
            return ""
        soup = BeautifulSoup(html, "html.parser")
        # 删除不需要的元素
        for tag in soup.find_all(["script", "style", "nav", "head"]):
            tag.decompose()
        # 在块级元素后添加换行
        for tag in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br"]):
            tag.append("\n")
        text = soup.get_text()
        return clean_text(text)


# ---------------------------------------------------------------------------
# PDF 解析器
# ---------------------------------------------------------------------------
class PdfParser(BaseBookParser):
    def __init__(self, book_path: str):
        super().__init__(book_path)
        self._doc = None

    def parse(self) -> None:
        self._doc = fitz.open(self.book_path)
        # 提取元数据
        meta = self._doc.metadata
        self.metadata["title"] = meta.get("title") or os.path.splitext(os.path.basename(self.book_path))[0]
        self.metadata["author"] = meta.get("author") or "Unknown"

        # 尝试从目录结构提取章节
        toc = self._doc.get_toc()
        if toc and len(toc) > 1:
            self._parse_from_toc(toc)
        else:
            self._parse_all_pages()

    def close(self) -> None:
        if self._doc:
            self._doc.close()
            self._doc = None

    def _parse_from_toc(self, toc) -> None:
        """从 PDF 目录提取章节。"""
        entries: List[Tuple[int, str, int]] = []  # (level, title, page)
        for item in toc:
            level = item[0]
            title = item[1]
            page = item[2] if isinstance(item[2], int) else -1
            if page > 0:
                entries.append((level, title, page - 1))  # 0-indexed

        if not entries:
            self._parse_all_pages()
            return

        total_pages = len(self._doc)
        for idx, (level, title, start_page) in enumerate(entries):
            end_page = entries[idx + 1][2] if idx + 1 < len(entries) else total_pages
            text_parts = []
            for p in range(start_page, min(end_page, total_pages)):
                page_text = self._doc[p].get_text()
                page_text = _BRACKETED_NUMBERS.sub("", page_text)
                page_text = _STANDALONE_PAGE_NUMBERS.sub("", page_text)
                page_text = _PAGE_NUMBERS_AT_END.sub("", page_text)
                text_parts.append(page_text)
            full_text = clean_text("\n".join(text_parts))
            if full_text.strip():
                self.chapters.append(Chapter(title=title, text=full_text, index=idx))

    def _parse_all_pages(self) -> None:
        """没有目录时，按页处理。"""
        total_pages = len(self._doc)
        # 每 20 页合并为一章
        chunk_size = 20
        for start in range(0, total_pages, chunk_size):
            end = min(start + chunk_size, total_pages)
            text_parts = []
            for p in range(start, end):
                page_text = self._doc[p].get_text()
                page_text = _BRACKETED_NUMBERS.sub("", page_text)
                page_text = _STANDALONE_PAGE_NUMBERS.sub("", page_text)
                page_text = _PAGE_NUMBERS_AT_END.sub("", page_text)
                text_parts.append(page_text)
            full_text = clean_text("\n".join(text_parts))
            if full_text.strip():
                idx = start // chunk_size
                self.chapters.append(
                    Chapter(
                        title=f"Pages {start + 1}–{end}",
                        text=full_text,
                        index=idx,
                    )
                )


# ---------------------------------------------------------------------------
# Markdown 解析器
# ---------------------------------------------------------------------------
class MarkdownParser(BaseBookParser):
    def parse(self) -> None:
        encoding = detect_encoding(self.book_path)
        with open(self.book_path, "r", encoding=encoding, errors="replace") as f:
            raw = f.read()

        md = markdown.Markdown(extensions=["toc", "fenced_code"])
        html = md.convert(raw)
        toc_tokens = md.toc_tokens

        self.metadata["title"] = os.path.splitext(os.path.basename(self.book_path))[0]

        if toc_tokens and len(toc_tokens) >= 1:
            self._parse_from_toc(html, toc_tokens)
        else:
            text = clean_text(raw)
            if text.strip():
                self.chapters = [Chapter(title="Content", text=text, index=0)]

    def _parse_from_toc(self, html: str, toc_tokens) -> None:
        soup = BeautifulSoup(html, "html.parser")

        def flatten(nodes, result):
            for node in nodes:
                result.append({"id": node["id"], "name": node["name"]})
                flatten(node.get("children", []), result)

        headers: List[dict] = []
        flatten(toc_tokens, headers)

        # 找到每个 header 在 HTML 中的位置
        positions: List[Tuple[int, str, str]] = []
        for h in headers:
            header_id = h["id"]
            id_attr = f'id="{header_id}"'
            pos = html.find(id_attr)
            if pos >= 0:
                tag_start = html.rfind("<", 0, pos)
                positions.append((tag_start, header_id, h["name"]))

        positions.sort(key=lambda x: x[0])

        for i, (start, hid, name) in enumerate(positions):
            end = positions[i + 1][0] if i + 1 < len(positions) else len(html)
            section_html = html[start:end]
            section_soup = BeautifulSoup(section_html, "html.parser")
            # 移除标题标签自身
            header_tag = section_soup.find(attrs={"id": hid})
            if header_tag:
                header_tag.decompose()
            text = clean_text(section_soup.get_text())
            if text.strip():
                self.chapters.append(
                    Chapter(title=name, text=f"{name}\n\n{text}", index=i)
                )


# ---------------------------------------------------------------------------
# TXT 解析器
# ---------------------------------------------------------------------------
class TxtParser(BaseBookParser):
    # 章节检测模式
    CHAPTER_PATTERNS = [
        re.compile(r"^[#]+\s*(.+)$", re.MULTILINE),           # Markdown 标题
        re.compile(r"^(第[零一二三四五六七八九十百千]+[章节回部篇集卷].*)$", re.MULTILINE),  # 中文
        re.compile(r"^(Chapter\s+\d+.*)$", re.MULTILINE | re.IGNORECASE),  # 英文
        re.compile(r"^(CHAPTER\s+[IVXLCDM]+.*)$", re.MULTILINE),          # 罗马数字
        re.compile(r"^[=\-]{3,}\s*$", re.MULTILINE),          # 分隔线
    ]

    def parse(self) -> None:
        encoding = detect_encoding(self.book_path)
        with open(self.book_path, "r", encoding=encoding, errors="replace") as f:
            raw = f.read()

        text = clean_text(raw)
        if not text:
            return

        self.metadata["title"] = os.path.splitext(os.path.basename(self.book_path))[0]

        # 尝试按章节分割
        chapters = self._split_by_chapters(text)
        if len(chapters) <= 1:
            # 没有检测到章节，整个作为一章
            self.chapters = [Chapter(title="Content", text=text, index=0)]
        else:
            self.chapters = chapters

    def _split_by_chapters(self, text: str) -> List[Chapter]:
        """尝试用多种模式检测章节。"""
        # 先尝试 Markdown 标题
        lines = text.split("\n")
        splits = []

        for i, line in enumerate(lines):
            line_stripped = line.strip()
            for pat in self.CHAPTER_PATTERNS:
                m = pat.match(line_stripped)
                if m:
                    title = m.group(1) if pat is self.CHAPTER_PATTERNS[0] else line_stripped
                    splits.append((i, title))
                    break

        if not splits:
            return []

        # 提取章节内容
        chapters: List[Chapter] = []
        for idx, (start_line, title) in enumerate(splits):
            end_line = splits[idx + 1][0] if idx + 1 < len(splits) else len(lines)
            body = "\n".join(lines[start_line + 1:end_line])
            full = "\n".join(lines[start_line:end_line])
            if body.strip():
                chapters.append(Chapter(title=title, text=clean_text(full), index=idx))

        return chapters


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------
def get_parser(book_path: str, file_type: Optional[str] = None) -> BaseBookParser:
    """根据扩展名返回合适的解析器。"""
    if not file_type:
        ext = os.path.splitext(book_path)[1].lower()
        if ext == ".epub":
            file_type = "epub"
        elif ext == ".pdf":
            file_type = "pdf"
        elif ext in (".md", ".markdown"):
            file_type = "markdown"
        elif ext == ".txt":
            file_type = "txt"
        else:
            file_type = "txt"  # 默认按文本处理

    parsers = {
        "epub": EpubParser,
        "pdf": PdfParser,
        "markdown": MarkdownParser,
        "txt": TxtParser,
    }
    cls = parsers.get(file_type, TxtParser)
    return cls(book_path)
