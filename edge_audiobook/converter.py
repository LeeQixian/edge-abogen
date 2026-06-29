"""
核心转换器 —— 将书本转换为有声书。
流程：解析 → 分块 → TTS → 拼接 → 字幕
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import time
import tempfile
from typing import List, Optional

from .parser import get_parser, Chapter
from .tts import (
    TTSEngine,
    resolve_voice,
    concat_audio_files,
    concat_subtitles,
    mp3_to_wav,
)
from .utils import (
    split_text_into_chunks,
    sanitize_filename,
    format_time,
)


class AudiobookConverter:
    """有声书转换器。"""

    def __init__(
        self,
        input_path: str,
        *,
        voice: str = "zh-CN-female",
        speed: float = 1.0,
        output_dir: Optional[str] = None,
        output_format: str = "mp3",
        max_chunk_chars: int = 2000,
        generate_subtitles: bool = True,
        save_chapters_separately: bool = False,
        proxy: Optional[str] = None,
    ):
        """
        Args:
            input_path: 输入文件路径
            voice: 语音预设名或完整 Voice Name
            speed: 语速倍率 (0.5 ~ 2.0)
            output_dir: 输出目录（默认输入文件同目录）
            output_format: 输出格式 (mp3 / wav)
            max_chunk_chars: 每次 TTS 的最大字符数
            generate_subtitles: 是否生成 SRT 字幕
            save_chapters_separately: 是否按章节单独保存
            proxy: HTTP 代理（如 http://127.0.0.1:7890）
        """
        self.input_path = os.path.abspath(input_path)
        self.voice = resolve_voice(voice)
        self.speed = max(0.5, min(2.0, speed))
        self.output_dir = output_dir or os.path.dirname(self.input_path)
        self.output_format = output_format.lower()
        self.max_chunk_chars = max_chunk_chars
        self.generate_subtitles = generate_subtitles
        self.save_chapters_separately = save_chapters_separately
        self.proxy = proxy

        # 语速映射到 edge-tts 的 rate 参数
        rate_val = int((self.speed - 1.0) * 100)
        sign = "+" if rate_val >= 0 else ""
        self._rate_str = f"{sign}{rate_val}%"

        os.makedirs(self.output_dir, exist_ok=True)

    def run(self) -> int:
        """同步入口，返回处理的总字符数。"""
        return asyncio.run(self._run())

    async def _run(self) -> int:
        # 1. 解析
        print(f"[Parse] 解析: {os.path.basename(self.input_path)}")
        parser = get_parser(self.input_path)
        try:
            parser.parse()
        finally:
            parser.close()

        chapters = parser.chapters
        if not chapters:
            print("[ERROR] 未能提取到文本内容")
            return 0

        print(f"   检测到 {len(chapters)} 个章节")
        print(f"   语音: {self.voice}")
        print(f"   语速: {self.speed}x")
        print()

        # 2. 基础名称
        base_name = sanitize_filename(
            os.path.splitext(os.path.basename(self.input_path))[0]
        )

        # 3. TTS 引擎
        engine = TTSEngine(
            voice=self.voice,
            rate=self._rate_str,
            proxy=self.proxy,
        )

        # 4. 逐章处理
        temp_dir = tempfile.mkdtemp(prefix="edge_ab_")
        chapter_audio_files: List[str] = []
        chapter_srt_files: List[str] = []
        chapter_offsets: List[float] = []
        total_chars = 0
        total_start = time.time()

        try:
            for i, chapter in enumerate(chapters):
                print(f"[TTS]  [{i + 1}/{len(chapters)}] {chapter.title}")
                print(f"   字符数: {len(chapter.text):,}")

                chapter_audio, chapter_srt, duration = await self._synthesize_chapter(
                    engine, chapter, i, temp_dir
                )

                if chapter_audio:
                    if self.save_chapters_separately:
                        # 保存单独章节
                        ch_name = sanitize_filename(f"{i + 1:02d}_{chapter.title}")
                        ch_output = os.path.join(
                            self.output_dir, f"{base_name}_{ch_name}.{self.output_format}"
                        )
                        if self.output_format == "wav":
                            mp3_to_wav(chapter_audio, ch_output)
                        else:
                            import shutil
                            shutil.copy2(chapter_audio, ch_output)
                        print(f"   [OK] 已保存: {os.path.basename(ch_output)}")

                    chapter_audio_files.append(chapter_audio)
                    chapter_offsets.append(0.0)  # 累积偏移在合并时计算

                if chapter_srt:
                    chapter_srt_files.append(chapter_srt)

                total_chars += len(chapter.text)
                elapsed = time.time() - total_start
                if duration > 0:
                    print(f"   音频时长: {format_time(duration)}  |  "
                          f"已用时间: {format_time(elapsed)}")
                print()

            # 5. 合并输出
            if not chapter_audio_files:
                print("[ERROR] 没有生成任何音频")
                return 0

            if len(chapter_audio_files) == 1 and not self.save_chapters_separately:
                # 单章节，直接复制
                output_path = os.path.join(
                    self.output_dir, f"{base_name}.{self.output_format}"
                )
                if self.output_format == "wav":
                    mp3_to_wav(chapter_audio_files[0], output_path)
                else:
                    import shutil
                    shutil.copy2(chapter_audio_files[0], output_path)
                print(f"[OK] 有声书已保存: {output_path}")
            elif not self.save_chapters_separately:
                # 多章节合并
                output_path = os.path.join(
                    self.output_dir, f"{base_name}.{self.output_format}"
                )
                print(f"[Merge] 合并 {len(chapter_audio_files)} 个章节...")
                concat_audio_files(chapter_audio_files, output_path, self.output_format)
                print(f"[OK] 有声书已保存: {output_path}")

            # 6. 合并字幕
            if self.generate_subtitles and chapter_srt_files:
                srt_output = os.path.join(
                    self.output_dir, f"{base_name}.srt"
                )
                # 计算累积偏移
                cumulative = 0.0
                real_offsets: List[float] = []
                for audio_file in chapter_audio_files[:-1]:
                    dur = self._get_mp3_duration(audio_file)
                    real_offsets.append(cumulative)
                    cumulative += dur
                real_offsets.append(cumulative)

                concat_subtitles(chapter_srt_files, srt_output, real_offsets)
                print(f"[Sub] 字幕已保存: {srt_output}")

            # 7. 总结
            total_elapsed = time.time() - total_start
            print()
            print(f"[DONE] 完成！共处理 {total_chars:,} 字符，耗时 {format_time(total_elapsed)}")

        finally:
            # 清理临时文件
            import shutil
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

        return total_chars

    async def _synthesize_chapter(
        self,
        engine: TTSEngine,
        chapter: Chapter,
        idx: int,
        temp_dir: str,
    ):
        """合成单个章节的音频。返回 (audio_path, srt_path, duration)。"""
        # 分块
        chunks = split_text_into_chunks(chapter.text, self.max_chunk_chars)
        if not chunks:
            return None, None, 0.0

        print(f"   分 {len(chunks)} 段合成...", end="", flush=True)

        chunk_audio_files: List[str] = []
        chunk_srt_files: List[str] = []
        total_duration = 0.0

        for ci, chunk in enumerate(chunks):
            if len(chunks) > 1:
                sys.stdout.write(f"\r   分 {len(chunks)} 段合成... {ci + 1}/{len(chunks)}")
                sys.stdout.flush()

            # 临时文件
            audio_tmp = os.path.join(temp_dir, f"ch{idx}_seg{ci}.mp3")
            srt_tmp = os.path.join(temp_dir, f"ch{idx}_seg{ci}.srt") if self.generate_subtitles else None

            try:
                duration = await engine.save(chunk, audio_tmp, srt_tmp)
                chunk_audio_files.append(audio_tmp)
                if srt_tmp and os.path.exists(srt_tmp):
                    chunk_srt_files.append(srt_tmp)
                total_duration += duration
            except Exception as e:
                print(f"\n   [WARN] 分段 {ci + 1} 合成失败: {e}")
                continue

        if len(chunks) > 1:
            print()  # 换行

        if not chunk_audio_files:
            return None, None, 0.0

        # 合并本章的各段
        chapter_audio = os.path.join(temp_dir, f"chapter_{idx}.mp3")
        concat_audio_files(chunk_audio_files, chapter_audio, "mp3")

        # 合并本章字幕
        chapter_srt = None
        if chunk_srt_files:
            chapter_srt = os.path.join(temp_dir, f"chapter_{idx}.srt")
            # 计算偏移
            offsets = [0.0]
            cumulative = 0.0
            for af in chunk_audio_files[:-1]:
                cumulative += self._get_mp3_duration(af)
                offsets.append(cumulative)
            concat_subtitles(chunk_srt_files, chapter_srt, offsets)

        return chapter_audio, chapter_srt, total_duration

    @staticmethod
    def _get_mp3_duration(path: str) -> float:
        """获取 MP3 文件时长（秒）。"""
        try:
            import subprocess
            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet",
                    "-show_entries", "format=duration",
                    "-of", "csv=p=0",
                    path,
                ],
                capture_output=True,
                text=True,
            )
            return float(result.stdout.strip() or 0)
        except Exception:
            # 回退：根据文件大小粗略估算
            try:
                size = os.path.getsize(path)
                # MP3 ~16KB/s at 128kbps, edge-tts uses 48kbps
                return size / (48000 / 8)
            except Exception:
                return 0.0
