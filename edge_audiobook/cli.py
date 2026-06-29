# -*- coding: utf-8 -*-
"""
edge-audiobook CLI -- lightweight audiobook generator.
Uses Microsoft Edge free TTS, no GPU required.
Supports --list-voices <filter> for language/region/gender filtering.
All voice data dynamically fetched from Microsoft API, zero hardcoding.
"""

from __future__ import annotations

import argparse
import io
import sys

from .converter import AudiobookConverter
from .tts import (
    get_voice_list_sync,
    filter_voices,
    parse_voice_filter,
    format_voice_list,
    resolve_voice,
)


def main() -> None:
    # Fix Windows console encoding
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace"
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace"
        )

    parser = argparse.ArgumentParser(
        prog="edge-audiobook",
        description="Convert ebooks to audiobooks using Microsoft Edge free TTS (no GPU)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  edge-audiobook book.epub
  edge-audiobook book.epub -v en-US-JennyNeural -s 1.2
  edge-audiobook book.pdf -v zh-CN-XiaoxiaoNeural --chapters
  edge-audiobook story.txt -v en-GB-SoniaNeural -f wav --no-subtitles
  edge-audiobook book.epub --proxy http://127.0.0.1:7890

Voice query (-L <filter>):
  edge-audiobook -L           # all (compact mode)
  edge-audiobook -L zh        # all Chinese voices
  edge-audiobook -L en-us     # all en-US voices
  edge-audiobook -L en-us-f   # en-US female
  edge-audiobook -L ja-m      # all Japanese male

Voice (-v):
  Pass ShortName directly (e.g. zh-CN-XiaoxiaoNeural)
  Or pass keyword (e.g. Jenny) for auto-search
  If omitted, the first available voice is used
""",
    )

    parser.add_argument(
        "input", nargs="?",
        help="Input file path (epub/pdf/txt/md)",
    )
    parser.add_argument(
        "-v", "--voice", default=None,
        help="Voice ShortName or keyword (auto-select first if omitted)",
    )
    parser.add_argument(
        "-s", "--speed", type=float, default=1.0,
        help="Speed multiplier 0.5~2.0 (default: 1.0)",
    )
    parser.add_argument(
        "-o", "--output-dir", default=None,
        help="Output directory (default: same as input)",
    )
    parser.add_argument(
        "-f", "--format", dest="output_format",
        default="mp3", choices=["mp3", "wav"],
        help="Output format (default: mp3)",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=2000,
        help="Max chars per TTS request (default: 2000)",
    )
    parser.add_argument(
        "--chapters", dest="save_chapters_separately",
        action="store_true",
        help="Save each chapter as separate file",
    )
    parser.add_argument(
        "--no-subtitles", dest="generate_subtitles",
        action="store_false",
        help="Do not generate SRT subtitles",
    )
    parser.add_argument(
        "--proxy", default=None,
        help="HTTP proxy (e.g. http://127.0.0.1:7890)",
    )
    parser.add_argument(
        "-L", "--list-voices", nargs="?", const="__all__", default=None,
        help="List available voices. Optional filter: zh, en-us, en-us-f, ja-m, etc.",
    )
    parser.add_argument(
        "--gui", action="store_true",
        help="Launch graphical interface",
    )

    args = parser.parse_args()

    # GUI mode
    if args.gui:
        from .gui import run_gui
        run_gui()
        return

    # Voice list mode
    if args.list_voices is not None:
        _handle_list_voices(args.list_voices)
        return

    # Need input file
    if not args.input:
        parser.error("Input file required, or use --list-voices / --gui")

    converter = AudiobookConverter(
        input_path=args.input,
        voice=args.voice,
        speed=args.speed,
        output_dir=args.output_dir,
        output_format=args.output_format,
        max_chunk_chars=args.chunk_size,
        generate_subtitles=args.generate_subtitles,
        save_chapters_separately=args.save_chapters_separately,
        proxy=args.proxy,
    )

    try:
        chars = converter.run()
        if chars == 0:
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n[WARN] Cancelled by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


def _handle_list_voices(filter_str: str) -> None:
    """Handle --list-voices command."""
    print("Fetching voice list...", end="", flush=True)
    try:
        all_voices = get_voice_list_sync()
    except Exception as e:
        print(f"\n[ERROR] Failed to fetch voices: {e}")
        sys.exit(1)
    print(f"  {len(all_voices)} voices available")

    if filter_str == "__all__":
        print(format_voice_list(all_voices, compact=True))
        return

    lang, region, gender = parse_voice_filter(filter_str)
    matched = filter_voices(all_voices, language=lang, region=region, gender=gender)

    if not matched:
        matched = filter_voices(all_voices, name_filter=filter_str)
        if not matched:
            print(f"No voices matching '{filter_str}'")
            print("Hint: try --list-voices zh or --list-voices en-us")
            return

    parts = []
    if lang:
        parts.append(f"lang={lang}")
    if region:
        parts.append(f"region={region}")
    if gender:
        parts.append(f"gender={gender}")
    desc = ", ".join(parts) if parts else f"name contains '{filter_str}'"
    print(f"Filter: {desc}  ->  {len(matched)} result(s)\n")
    print(format_voice_list(matched))


if __name__ == "__main__":
    main()
