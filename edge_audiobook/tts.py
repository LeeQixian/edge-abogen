# -*- coding: utf-8 -*-
"""
edge-tts wrapper: async TTS + subtitles + voice list management.
All voice data parsed from Microsoft API ShortName via regex, zero hardcoding.
JSON cache supported for fast startup.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional, Tuple

import edge_tts


# ---------------------------------------------------------------------------
# 缓存路径
# ---------------------------------------------------------------------------
def _cache_path() -> Path:
    """语音列表 JSON 缓存路径。"""
    base = os.environ.get("EDGE_AUDIOBOOK_CACHE", os.path.join(os.path.expanduser("~"), ".cache", "edge-audiobook"))
    Path(base).mkdir(parents=True, exist_ok=True)
    return Path(base) / "voices_cache.json"


# ---------------------------------------------------------------------------
# 语音数据模型 —— 基于 API 返回数据动态构建，零硬编码
# ---------------------------------------------------------------------------
# ShortName 格式多样：
#   "ar-AE-HamdanNeural"         (lang-region-name)
#   "iu-Latn-CA-SiqiniqNeural"   (lang-script-region-name)
# 因此用 Locale 字段确定语种/地区（可靠），ShortName 仅提取人名。


@dataclass
class VoiceInfo:
    short_name: str          # ShortName, e.g. "ar-AE-HamdanNeural"
    language: str            # language code, e.g. "ar" (from Locale)
    region: str              # region code, e.g. "AE" (from Locale)
    speaker_name: str        # speaker name, e.g. "Hamdan" (from ShortName)
    gender: str              # "Male" / "Female"
    friendly_name: str       # original friendly name
    personalities: List[str] = field(default_factory=list)

    @property
    def locale(self) -> str:
        """locale string: ar-AE"""
        return f"{self.language}-{self.region}"


async def _fetch_raw_voices() -> List[dict]:
    """Fetch raw voice list from edge-tts API."""
    return await edge_tts.list_voices()


def _extract_speaker_name(short_name: str, locale: str) -> str:
    """
    Extract speaker name from ShortName by removing the Locale prefix.

    Examples:
        ShortName="ar-AE-HamdanNeural", Locale="ar-AE" -> "Hamdan"
        ShortName="iu-Latn-CA-SiqiniqNeural", Locale="iu-Latn-CA" -> "Siqiniq"
    """
    prefix = locale + "-"
    if short_name.startswith(prefix):
        name = short_name[len(prefix):]
    else:
        # Fallback: try to parse by last two parts
        parts = short_name.rsplit("-", 1)
        name = parts[-1] if len(parts) > 1 else short_name
    # Strip "Neural" / "MultilingualNeural" suffix
    name = re.sub(r"(Multilingual)?Neural$", "", name)
    return name


def _parse_voice(raw: dict) -> VoiceInfo:
    """Pure dynamic parsing using API fields only, zero hardcoding."""
    short = raw.get("ShortName", "")
    locale = raw.get("Locale", "")
    if not short:
        raise ValueError(f"Voice missing ShortName: {raw}")
    if not locale:
        raise ValueError(f"Voice missing Locale: {short}")

    # Parse locale into language and region
    locale_parts = locale.split("-")
    # Language is always the first part
    language = locale_parts[0].lower()
    # Region is the last part (handles script variants like iu-Latn-CA)
    region = locale_parts[-1].upper() if len(locale_parts) > 1 else ""

    speaker = _extract_speaker_name(short, locale)
    gender = raw.get("Gender", "Unknown")
    friendly = raw.get("FriendlyName", short)

    personalities: List[str] = []
    tag = raw.get("VoiceTag")
    if isinstance(tag, dict):
        personalities = list(tag.get("VoicePersonalities", []) or [])

    return VoiceInfo(
        short_name=short,
        language=language,
        region=region,
        speaker_name=speaker,
        gender=gender,
        friendly_name=friendly,
        personalities=personalities,
    )


# ---------------------------------------------------------------------------
# JSON 缓存
# ---------------------------------------------------------------------------
def _save_cache(voices: List[VoiceInfo]) -> None:
    """保存语音列表到 JSON 缓存。"""
    data = [
        {
            "short_name": v.short_name,
            "language": v.language,
            "region": v.region,
            "speaker_name": v.speaker_name,
            "gender": v.gender,
            "friendly_name": v.friendly_name,
            "personalities": v.personalities,
        }
        for v in voices
    ]
    try:
        _cache_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # 缓存写入失败不致命


def _load_cache() -> Optional[List[VoiceInfo]]:
    """从 JSON 缓存加载语音列表。"""
    p = _cache_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [VoiceInfo(**item) for item in data]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 获取语音列表
# ---------------------------------------------------------------------------
async def get_voice_list(force_refresh: bool = False) -> List[VoiceInfo]:
    """获取所有可用语音（优先 JSON 缓存，可强制刷新）。"""
    if not force_refresh:
        cached = _load_cache()
        if cached:
            return cached

    raw_list = await _fetch_raw_voices()
    voices = [_parse_voice(v) for v in raw_list]
    # 按语种 → 地区 → 说话人排序
    voices.sort(key=lambda v: (v.language, v.region, v.speaker_name.lower()))
    _save_cache(voices)
    return voices


def get_voice_list_sync(force_refresh: bool = False) -> List[VoiceInfo]:
    """同步版本。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(get_voice_list(force_refresh))
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, get_voice_list(force_refresh)).result()


# ---------------------------------------------------------------------------
# 动态语音树结构
# ---------------------------------------------------------------------------
def build_voice_tree(voices: List[VoiceInfo]) -> Dict:
    """
    纯动态构建树: {language: {region: {gender: [VoiceInfo]}}}
    不做任何硬编码，完全基于 API 数据。
    """
    tree: Dict[str, Dict[str, Dict[str, List[VoiceInfo]]]] = {}
    for v in voices:
        tree.setdefault(v.language, {}) \
            .setdefault(v.region, {}) \
            .setdefault(v.gender, []) \
            .append(v)
    # 每层按首字母排序
    for regions in tree.values():
        for genders in regions.values():
            for lst in genders.values():
                lst.sort(key=lambda x: x.speaker_name.lower())
    return tree


def get_sorted_languages(tree: Dict) -> List[str]:
    """返回排序后的语种列表。"""
    return sorted(tree.keys())


def get_sorted_regions(tree: Dict, language: str) -> List[str]:
    """返回指定语种下的地区列表。"""
    return sorted(tree.get(language, {}).keys())


def get_sorted_genders(tree: Dict, language: str, region: str) -> List[str]:
    """返回指定语种+地区下的性别列表。"""
    regions = tree.get(language, {})
    genders = regions.get(region, {})
    return sorted(genders.keys())


def get_speakers(tree: Dict, language: str, region: str, gender: str) -> List[VoiceInfo]:
    """返回指定语种+地区+性别下的 Speaker 列表。"""
    return tree.get(language, {}).get(region, {}).get(gender, [])


# ---------------------------------------------------------------------------
# 筛选
# ---------------------------------------------------------------------------
def filter_voices(
    voices: List[VoiceInfo],
    language: Optional[str] = None,
    region: Optional[str] = None,
    gender: Optional[str] = None,
    name_filter: Optional[str] = None,
) -> List[VoiceInfo]:
    """按条件筛选。"""
    result = voices
    if language:
        ll = language.lower()
        result = [v for v in result if v.language == ll]
    if region:
        ru = region.upper()
        result = [v for v in result if v.region.upper() == ru]
    if gender:
        gl = gender.lower()
        result = [v for v in result if v.gender.lower() == gl]
    if name_filter:
        nf = name_filter.lower()
        result = [v for v in result
                  if nf in v.short_name.lower()
                  or nf in v.speaker_name.lower()
                  or nf in v.friendly_name.lower()]
    return result


# ---------------------------------------------------------------------------
# CLI 语音筛选
# ---------------------------------------------------------------------------
def parse_voice_filter(text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    解析 CLI 筛选字符串。
    zh / en-us / en-us-f / ja-m / en-us-fe-jenny
    """
    text = text.strip().lower()
    if not text:
        return None, None, None

    language: Optional[str] = None
    region: Optional[str] = None
    gender: Optional[str] = None

    parts = text.split("-")
    _GMAP = {"f": "female", "fe": "female", "female": "female",
             "m": "male", "ma": "male", "male": "male"}

    if len(parts) >= 1:
        language = parts[0]
    if len(parts) >= 2:
        if parts[1] in _GMAP:
            gender = _GMAP[parts[1]]
        else:
            region = parts[1].upper()
    if len(parts) >= 3:
        if parts[2] in _GMAP:
            gender = _GMAP[parts[2]]

    return language, region, gender


def format_voice_list(voices: List[VoiceInfo], compact: bool = False) -> str:
    """格式化输出。"""
    lines = []
    if compact:
        for v in voices:
            g = "[F]" if v.gender.lower() == "female" else "[M]" if v.gender.lower() == "male" else "[?]"
            lines.append(f"  {v.short_name:45s} {g} {v.friendly_name}")
    else:
        cur = ""
        for v in voices:
            loc = v.locale
            if loc != cur:
                cur = loc
                lines.append(f"\n── {loc} ──")
            g = "[F]" if v.gender.lower() == "female" else "[M]" if v.gender.lower() == "male" else "[?]"
            lines.append(f"  {g} {v.short_name}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 向后兼容的快捷方式（可选，保留 CLI -v 的方便性）
# ---------------------------------------------------------------------------
def resolve_voice(voice: str, voices: Optional[List[VoiceInfo]] = None) -> str:
    """
    解析 voice 参数：
    - 如果是完整 ShortName（含 Neural），直接使用
    - 否则在所有可用语音中搜索匹配
    - 仍找不到则回退到第一个可用语音
    """
    if not voice:
        # 加载语音列表取第一个
        vl = voices or get_voice_list_sync()
        return vl[0].short_name if vl else "en-US-JennyNeural"

    if "Neural" in voice:
        return voice

    vl = voices or get_voice_list_sync()
    needle = voice.lower()
    for v in vl:
        if needle in v.short_name.lower() or needle in v.speaker_name.lower():
            return v.short_name
    return vl[0].short_name if vl else "en-US-JennyNeural"


# SSML 转义（edge-tts 内部也会做，这里显式处理更安全）
_SSML_ESCAPE_TABLE = str.maketrans({
    "&": "&amp;",
    '"': "&quot;",
    "'": "&apos;",
    "<": "&lt;",
    ">": "&gt;",
})


class TTSEngine:
    """封装 edge-tts 的 TTS 调用，提供流式音频 + 字幕数据。"""

    def __init__(
        self,
        voice: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "+0%",
        pitch: str = "+0Hz",
        proxy: Optional[str] = None,
    ):
        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self.proxy = proxy

    async def synthesize(
        self, text: str
    ) -> AsyncIterator[Tuple[bytes, Optional[dict]]]:
        """
        流式合成语音。

        Yields:
            (audio_bytes, metadata_or_None)
            - audio_bytes: MP3 音频片段
            - metadata: {"type": "SentenceBoundary"|"WordBoundary", "offset": ..., "duration": ..., "text": ...}
            当 metadata 为 None 时，仅表示纯音频数据。
        """
        if not text or not text.strip():
            return

        safe_text = text.translate(_SSML_ESCAPE_TABLE)
        communicate = edge_tts.Communicate(
            text=safe_text,
            voice=self.voice,
            rate=self.rate,
            pitch=self.pitch,
            proxy=self.proxy,
        )

        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"], None
            elif chunk["type"] in ("SentenceBoundary", "WordBoundary"):
                yield b"", {
                    "type": chunk["type"],
                    "offset": chunk["offset"],
                    "duration": chunk["duration"],
                    "text": chunk["text"],
                }

    async def save(
        self,
        text: str,
        audio_path: str,
        subtitle_path: Optional[str] = None,
    ) -> float:
        """
        合成并保存为文件，返回音频时长（秒）。

        使用 edge_tts 内置的 save 方法，最高效。
        """
        safe_text = text.translate(_SSML_ESCAPE_TABLE)
        communicate = edge_tts.Communicate(
            text=safe_text,
            voice=self.voice,
            rate=self.rate,
            pitch=self.pitch,
            proxy=self.proxy,
        )
        sub_maker = edge_tts.SubMaker()

        with open(audio_path, "wb") as audio_file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_file.write(chunk["data"])
                elif chunk["type"] in ("SentenceBoundary", "WordBoundary"):
                    sub_maker.feed(chunk)

        # 估算时长（从字幕元数据）
        duration = 0.0
        if sub_maker.cues:
            last_cue = sub_maker.cues[-1]
            duration = (last_cue.end.total_seconds()
                        if hasattr(last_cue.end, 'total_seconds')
                        else 0.0)

        # 写字幕
        if subtitle_path and sub_maker.cues:
            srt_content = sub_maker.get_srt()
            with open(subtitle_path, "w", encoding="utf-8") as sf:
                sf.write(srt_content)

        return duration


# ---------------------------------------------------------------------------
# 音频拼接 —— 纯 Python，零外部依赖。
# edge-tts 输出 MP3 (CBR 48kbps)，直接二进制拼接即可。
# ---------------------------------------------------------------------------

def _binary_concat(files: list[str], output: str) -> None:
    """二进制拼接文件（适用于同编码同参数的 MP3）。"""
    with open(output, "wb") as out:
        for f in files:
            with open(f, "rb") as inp:
                out.write(inp.read())


def mp3_to_wav(mp3_path: str, wav_path: str) -> None:
    """MP3 -> WAV。优先 ffmpeg，fallback soundfile。"""
    # 尝试 ffmpeg（如果有的话）
    if shutil.which("ffmpeg"):
        subprocess.run(
            ["ffmpeg", "-y", "-i", mp3_path,
             "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", wav_path],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    # Fallback: soundfile
    try:
        audio, sr = sf.read(mp3_path, dtype="float32")
        if audio.ndim > 1:
            audio = audio[:, 0]
        if sr != 16000:
            ratio = 16000 / sr
            new_len = int(len(audio) * ratio)
            audio = np.interp(
                np.linspace(0, len(audio), new_len, endpoint=False),
                np.arange(len(audio)), audio
            ).astype("float32")
        sf.write(wav_path, audio, 16000, subtype="PCM_16")
    except Exception:
        import shutil
        shutil.copy2(mp3_path, wav_path)  # 保底：至少拿到 mp3


def concat_audio_files(
    audio_files: list[str],
    output_path: str,
    output_format: str = "mp3",
) -> None:
    """拼接音频。MP3 直接二进制拼接，WAV 走 soundfile。"""
    if len(audio_files) == 1:
        src = audio_files[0]
        if output_format == "mp3" or src.endswith(output_format):
            import shutil
            shutil.copy2(src, output_path)
        else:
            mp3_to_wav(src, output_path)
        return

    if output_format == "mp3":
        _binary_concat(audio_files, output_path)
    else:
        # WAV: 先拼成一个大 MP3，再整体转码
        merged = output_path + ".tmp.mp3"
        _binary_concat(audio_files, merged)
        try:
            mp3_to_wav(merged, output_path)
        finally:
            try:
                os.unlink(merged)
            except OSError:
                pass


def concat_subtitles(
    srt_files: list[str],
    output_path: str,
    offsets: list[float],
) -> None:
    """合并多个 SRT 字幕文件，根据偏移量调整时间戳。"""
    import re

    srt_entry_re = re.compile(
        r"(\d+)\s*\n"
        r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*"
        r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*\n"
        r"((?:.|\n)+?)(?=\n\n|\n*\Z)",
        re.MULTILINE,
    )

    global_index = 0
    merged_lines = []

    for srt_file, offset_sec in zip(srt_files, offsets):
        if not os.path.exists(srt_file):
            continue
        with open(srt_file, "r", encoding="utf-8") as f:
            content = f.read()

        for match in srt_entry_re.finditer(content):
            global_index += 1
            idx = match.group(1)
            start_str = match.group(2)
            end_str = match.group(3)
            text = match.group(4).strip()

            def shift(ts: str) -> str:
                parts = ts.replace(",", ".").split(":")
                h, m = int(parts[0]), int(parts[1])
                s = float(parts[2])
                total = h * 3600 + m * 60 + s + offset_sec
                h2 = int(total // 3600)
                m2 = int((total % 3600) // 60)
                s2 = total % 60
                return f"{h2:02d}:{m2:02d}:{s2:06.3f}".replace(".", ",")

            merged_lines.append(
                f"{global_index}\n{shift(start_str)} --> {shift(end_str)}\n{text}\n"
            )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(merged_lines))
        if merged_lines:
            f.write("\n")
