# -*- coding: utf-8 -*-
"""
edge-audiobook GUI: lightweight Tkinter interface.
Cascading comboboxes (Language -> Region -> Gender -> Speaker) with type-to-filter.
All data dynamically fetched from edge-tts API, zero hardcoding.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Dict, List, Optional

from .tts import (
    VoiceInfo,
    get_voice_list_sync,
    build_voice_tree,
    get_sorted_languages,
    get_sorted_regions,
    get_sorted_genders,
    get_speakers,
    resolve_voice,
)
from .converter import AudiobookConverter


# ---------------------------------------------------------------------------
# 带自动补全/筛选的 Combobox
# ---------------------------------------------------------------------------
class FilteredCombobox(ttk.Combobox):
    """
    增强型 Combobox：输入文字时自动筛选下拉列表。
    用户可以直接输入，下拉选项会根据输入内容动态过滤。
    """

    def __init__(self, parent, values=None, **kwargs):
        super().__init__(parent, **kwargs)
        self._all_values: List[str] = []
        self._current_filter = ""
        self._filter_timer: Optional[str] = None  # after ID
        if values:
            self.set_values(values)
        # 绑定事件
        self.bind("<KeyRelease>", self._on_keyrelease)
        self.bind("<FocusIn>", self._on_focusin)

    def set_values(self, values: List[str]) -> None:
        """设置完整候选列表。"""
        self._all_values = list(values)
        self._apply_filter("")

    def _on_focusin(self, event=None) -> None:
        """获得焦点时显示所有选项。"""
        self._apply_filter("")

    def _on_keyrelease(self, event=None) -> None:
        """按键后延迟筛选（避免频繁刷新）。"""
        key = event.keysym if event else ""
        # 忽略导航键
        if key in ("Up", "Down", "Left", "Right", "Tab", "Return",
                    "Shift_L", "Shift_R", "Control_L", "Control_R",
                    "Alt_L", "Alt_R", "Escape", "Caps_Lock"):
            return
        if self._filter_timer:
            self.after_cancel(self._filter_timer)
        self._filter_timer = self.after(100, self._do_filter)

    def _do_filter(self) -> None:
        """执行筛选。"""
        current = self.get().strip().lower()
        self._apply_filter(current)

    def _apply_filter(self, filter_text: str) -> None:
        """根据输入文本筛选下拉列表。"""
        self._current_filter = filter_text
        if not filter_text:
            filtered = self._all_values
        else:
            filtered = [v for v in self._all_values
                       if filter_text in v.lower()]
        self["values"] = filtered
        # 如果当前值不在筛选项内，不做强制修改（让用户继续输入）


# ---------------------------------------------------------------------------
# 性别显示映射 (纯动态)
# ---------------------------------------------------------------------------
_GENDER_DISPLAY: Dict[str, str] = {}  # 运行时填充

def _gender_label(g: str) -> str:
    """动态生成性别显示标签。"""
    g = g.strip()
    # 只在第一次遇到时生成映射
    if g not in _GENDER_DISPLAY:
        gl = g.lower()
        if gl == "female":
            _GENDER_DISPLAY[g] = f"Female  (女声)"
        elif gl == "male":
            _GENDER_DISPLAY[g] = f"Male  (男声)"
        else:
            _GENDER_DISPLAY[g] = g
    return _GENDER_DISPLAY[g]


def _gender_key(display: str) -> str:
    """从显示标签反向获取原始 Gender 值。"""
    for raw, disp in _GENDER_DISPLAY.items():
        if disp == display:
            return raw
    # fallback: extract first word
    return display.split()[0] if display else display


# ---------------------------------------------------------------------------
# 后台转换线程
# ---------------------------------------------------------------------------
class ConvertThread(threading.Thread):
    def __init__(self, converter: AudiobookConverter):
        super().__init__(daemon=True)
        self.converter = converter
        self.result_chars = 0
        self.error: Optional[str] = None

    def run(self) -> None:
        try:
            self.result_chars = self.converter.run()
        except Exception as e:
            self.error = str(e)


# ---------------------------------------------------------------------------
# 主 GUI 应用
# ---------------------------------------------------------------------------
class AudiobookApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Edge Audiobook - 有声书生成器")
        self.root.geometry("740x600")
        self.root.minsize(600, 500)

        if sys.platform == "win32":
            try:
                self.root.tk.call("tk", "scaling", 1.5)
            except Exception:
                pass

        # 语音数据（动态）
        self.all_voices: List[VoiceInfo] = []
        self.voice_tree: Dict = {}
        self._voice_loaded = False

        # 转换线程
        self.convert_thread: Optional[ConvertThread] = None

        # Tk 变量
        self.input_path = tk.StringVar()
        self.output_dir = tk.StringVar(value=os.path.expanduser("~\\Desktop"))
        self.speed_var = tk.DoubleVar(value=1.0)
        self.format_var = tk.StringVar(value="mp3")
        self.sub_var = tk.BooleanVar(value=True)
        self.chapters_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="初始化中...")

        # 级联选择变量
        self.lang_var = tk.StringVar()
        self.region_var = tk.StringVar()
        self.gender_var = tk.StringVar()
        self.speaker_var = tk.StringVar()

        # 当前实际选中的 ShortName
        self._selected_shortname: str = ""

        self._build_ui()
        self.root.after(100, self._load_voices)

    # ==================================================================
    # UI 构建
    # ==================================================================
    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding="10")
        main.pack(fill="both", expand=True)

        # ── 文件选择 ──
        frm = ttk.LabelFrame(main, text="输入文件", padding="5")
        frm.pack(fill="x", pady=(0, 8))
        ttk.Entry(frm, textvariable=self.input_path).pack(
            side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(frm, text="浏览...", command=self._browse_file).pack(side="right")

        # ── 语音选择 - 级联 + 筛选 ──
        voice_frame = ttk.LabelFrame(main, text="语音选择 (直接输入即可筛选)", padding="5")
        voice_frame.pack(fill="x", pady=(0, 8))

        grid = ttk.Frame(voice_frame)
        grid.pack(fill="x")

        # Row 0: 语种 | 地区 | 刷新按钮
        ttk.Label(grid, text="语种:", width=6).grid(row=0, column=0, sticky="w", padx=(0, 3))
        self.lang_combo = FilteredCombobox(grid, textvariable=self.lang_var, width=20)
        self.lang_combo.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self.lang_combo.bind("<<ComboboxSelected>>", self._on_lang_change)

        ttk.Label(grid, text="地区:", width=6).grid(row=0, column=2, sticky="w", padx=(0, 3))
        self.region_combo = FilteredCombobox(grid, textvariable=self.region_var, width=14)
        self.region_combo.grid(row=0, column=3, sticky="ew", padx=(0, 6))
        self.region_combo.bind("<<ComboboxSelected>>", self._on_region_change)

        self.refresh_btn = ttk.Button(grid, text="刷新语音列表", command=self._refresh_voices)
        self.refresh_btn.grid(row=0, column=4, sticky="e", padx=(10, 0))

        # Row 1: 性别 | Speaker
        ttk.Label(grid, text="性别:", width=6).grid(row=1, column=0, sticky="w", padx=(0, 3), pady=(5, 0))
        self.gender_combo = FilteredCombobox(grid, textvariable=self.gender_var, width=20)
        self.gender_combo.grid(row=1, column=1, sticky="ew", padx=(0, 6), pady=(5, 0))
        self.gender_combo.bind("<<ComboboxSelected>>", self._on_gender_change)

        ttk.Label(grid, text="Speaker:", width=6).grid(row=1, column=2, sticky="w", padx=(0, 3), pady=(5, 0))
        self.speaker_combo = FilteredCombobox(grid, textvariable=self.speaker_var, width=14)
        self.speaker_combo.grid(row=1, column=3, sticky="ew", pady=(5, 0))
        self.speaker_combo.bind("<<ComboboxSelected>>", self._on_speaker_change)

        # 当前选中的 ShortName 显示
        self.selected_label = ttk.Label(grid, text="", foreground="gray", font=("", 8))
        self.selected_label.grid(row=1, column=4, sticky="w", padx=(10, 0), pady=(5, 0))

        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)

        # ── 参数 ──
        pf = ttk.LabelFrame(main, text="参数", padding="5")
        pf.pack(fill="x", pady=(0, 8))
        row = ttk.Frame(pf)
        row.pack(fill="x")

        ttk.Label(row, text="语速:").pack(side="left", padx=(0, 3))
        scale = ttk.Scale(row, from_=0.5, to=2.0, variable=self.speed_var,
                          orient="horizontal", length=130)
        scale.pack(side="left", padx=(0, 3))
        self.speed_label = ttk.Label(row, text="1.0x")
        self.speed_label.pack(side="left")
        scale.configure(command=lambda v: self.speed_label.configure(text=f"{float(v):.1f}x"))

        ttk.Label(row, text="  格式:").pack(side="left", padx=(10, 3))
        ttk.Combobox(row, textvariable=self.format_var, state="readonly",
                     values=["mp3", "wav"], width=5).pack(side="left")

        ttk.Label(row, text="  输出目录:").pack(side="left", padx=(10, 3))
        ttk.Entry(row, textvariable=self.output_dir, width=22).pack(side="left", padx=(0, 3))
        ttk.Button(row, text="...", width=3, command=self._browse_output).pack(side="left")

        # ── 选项 ──
        opt = ttk.Frame(main)
        opt.pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(opt, text="生成 SRT 字幕", variable=self.sub_var).pack(side="left")
        ttk.Checkbutton(opt, text="按章节单独保存", variable=self.chapters_var).pack(side="left", padx=(15, 0))

        # ── 进度 ──
        self.progress = ttk.Progressbar(main, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 5))
        ttk.Label(main, textvariable=self.status_var, foreground="gray").pack(anchor="w")

        # ── 按钮 ──
        btn = ttk.Frame(main)
        btn.pack(fill="x", pady=(8, 0))
        self.convert_btn = ttk.Button(btn, text="开始转换", command=self._start_conversion)
        self.convert_btn.pack(side="right", padx=(5, 0))
        self.cancel_btn = ttk.Button(btn, text="取消", command=self._cancel_conversion,
                                     state="disabled")
        self.cancel_btn.pack(side="right")

    # ==================================================================
    # 语音加载
    # ==================================================================
    def _load_voices(self) -> None:
        self.status_var.set("正在获取语音列表...")
        self.root.update_idletasks()

        def _fetch():
            try:
                voices = get_voice_list_sync(force_refresh=False)  # 优先缓存
                self.root.after(0, lambda: self._on_loaded(voices, False))
            except Exception as e:
                self.root.after(0, lambda: self._on_error(str(e)))

        threading.Thread(target=_fetch, daemon=True).start()

    def _refresh_voices(self) -> None:
        """强制从 API 刷新语音列表。"""
        self.status_var.set("正在从 Microsoft 获取最新语音列表...")
        self.root.update_idletasks()
        self.refresh_btn.configure(state="disabled", text="刷新中...")

        def _fetch():
            try:
                voices = get_voice_list_sync(force_refresh=True)
                self.root.after(0, lambda: self._on_loaded(voices, True))
            except Exception as e:
                self.root.after(0, lambda: self._on_error(str(e)))
            finally:
                self.root.after(0, lambda: self.refresh_btn.configure(
                    state="normal", text="刷新语音列表"))

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_loaded(self, voices: List[VoiceInfo], refreshed: bool) -> None:
        self.all_voices = voices
        self.voice_tree = build_voice_tree(voices)
        self._voice_loaded = True

        # 填充语种列表
        langs = get_sorted_languages(self.voice_tree)
        self.lang_combo.set_values(langs)
        tag = "[已缓存]" if not refreshed else "[已刷新]"
        self.status_var.set(f"{tag} 共 {len(voices)} 个语音 - 请选择语种")

        if langs:
            self.lang_var.set(langs[0])
            self._on_lang_change()

    def _on_error(self, err: str) -> None:
        self.status_var.set(f"获取失败: {err}")
        self.refresh_btn.configure(state="normal", text="刷新语音列表")
        messagebox.showwarning("网络错误",
                               f"无法获取语音列表:\n{err}\n\n请检查网络后点击刷新按钮。")

    # ==================================================================
    # 级联选择（全动态，零硬编码）
    # ==================================================================
    def _on_lang_change(self, event=None) -> None:
        lang = self.lang_var.get().strip()
        if not lang or lang not in self.voice_tree:
            self._clear_below("region")
            return

        regions = get_sorted_regions(self.voice_tree, lang)
        self.region_combo.set_values(regions)
        if regions:
            self.region_var.set(regions[0])
            self._on_region_change()
        else:
            self._clear_below("region")

    def _on_region_change(self, event=None) -> None:
        lang = self.lang_var.get().strip()
        region = self.region_var.get().strip()
        if not lang or not region:
            self._clear_below("gender")
            return

        genders_raw = get_sorted_genders(self.voice_tree, lang, region)
        genders_display = [_gender_label(g) for g in genders_raw]
        # 保存映射: display → raw
        self._gender_map = dict(zip(genders_display, genders_raw))
        self.gender_combo.set_values(genders_display)
        if genders_display:
            self.gender_var.set(genders_display[0])
            self._on_gender_change()
        else:
            self._clear_below("gender")

    def _on_gender_change(self, event=None) -> None:
        lang = self.lang_var.get().strip()
        region = self.region_var.get().strip()
        gender_display = self.gender_var.get().strip()

        gender_raw = _gender_key(gender_display)
        speakers = get_speakers(self.voice_tree, lang, region, gender_raw)

        # 构建 Speaker 显示: "Name  (ShortName)"
        # 按 speaker_name 排序（已在 get_speakers 中排好）
        speaker_entries = [
            f"{v.speaker_name}  ({v.short_name})"
            for v in speakers
        ]
        self.speaker_combo.set_values(speaker_entries)
        if speaker_entries:
            self.speaker_var.set(speaker_entries[0])
            self._on_speaker_change()
        else:
            self._clear_below("speaker")

    def _on_speaker_change(self, event=None) -> None:
        val = self.speaker_var.get()
        if "(" in val and ")" in val:
            short = val.split("(")[-1].rstrip(")")
            self._selected_shortname = short
            self.selected_label.configure(text=f"已选: {short}")
        else:
            # 尝试解析
            self._selected_shortname = val
            self.selected_label.configure(text=f"已选: {val}")

    def _clear_below(self, level: str) -> None:
        if level in ("region", "gender", "speaker"):
            self.region_combo.set_values([])
            self.region_var.set("")
        if level in ("gender", "speaker"):
            self.gender_combo.set_values([])
            self.gender_var.set("")
        if level == "speaker":
            self.speaker_combo.set_values([])
            self.speaker_var.set("")
            self.selected_label.configure(text="")

    # ==================================================================
    # 文件 & 输出浏览
    # ==================================================================
    def _browse_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择电子书文件",
            filetypes=[
                ("所有支持格式", "*.epub *.pdf *.txt *.md *.markdown"),
                ("EPUB", "*.epub"), ("PDF", "*.pdf"),
                ("文本文件", "*.txt"), ("Markdown", "*.md *.markdown"),
            ],
        )
        if path:
            self.input_path.set(path)
            self.output_dir.set(os.path.dirname(path))

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output_dir.set(path)

    # ==================================================================
    # 转换控制
    # ==================================================================
    def _start_conversion(self) -> None:
        if not self.input_path.get():
            messagebox.showwarning("缺少输入", "请先选择输入文件。")
            return

        voice = self._selected_shortname
        if not voice:
            voice = self.speaker_var.get()
            if "(" in voice:
                voice = voice.split("(")[-1].rstrip(")")
        if not voice:
            # 尝试从语音列表选第一个
            vl = self.all_voices
            voice = vl[0].short_name if vl else "en-US-JennyNeural"

        converter = AudiobookConverter(
            input_path=self.input_path.get(),
            voice=voice,
            speed=self.speed_var.get(),
            output_dir=self.output_dir.get(),
            output_format=self.format_var.get(),
            generate_subtitles=self.sub_var.get(),
            save_chapters_separately=self.chapters_var.get(),
        )

        self._set_ui_state(running=True)
        self.status_var.set("转换中...")
        self.convert_thread = ConvertThread(converter)
        self.convert_thread.start()
        self.root.after(200, self._poll_thread)

    def _cancel_conversion(self) -> None:
        if self.convert_thread and self.convert_thread.is_alive():
            self.status_var.set("取消中...")
            # 通过关闭事件循环来中止异步任务
            self.convert_thread = None

    def _poll_thread(self) -> None:
        if self.convert_thread is None:
            self._set_ui_state(running=False)
            return
        if self.convert_thread.is_alive():
            self.progress.step(1)
            self.root.after(200, self._poll_thread)
        else:
            self.progress.stop()
            if self.convert_thread.error:
                self.status_var.set(f"失败: {self.convert_thread.error}")
                messagebox.showerror("转换失败", self.convert_thread.error)
            else:
                self.status_var.set("转换完成!")
                messagebox.showinfo("完成", "有声书生成成功!")
            self._set_ui_state(running=False)

    def _set_ui_state(self, running: bool) -> None:
        s = "disabled" if running else "normal"
        self.convert_btn.configure(state=s)
        self.cancel_btn.configure(state="normal" if running else "disabled")
        self.lang_combo.configure(state=s)
        self.region_combo.configure(state=s)
        self.gender_combo.configure(state=s)
        self.speaker_combo.configure(state=s)
        self.refresh_btn.configure(state=s)
        if running:
            self.progress.start(10)
        else:
            self.progress.stop()


def run_gui() -> None:
    root = tk.Tk()
    AudiobookApp(root)
    root.mainloop()
