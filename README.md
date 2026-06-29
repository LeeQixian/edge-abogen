# edge-audiobook 🎧

**轻量级有声书生成器** —— 使用 Microsoft Edge 免费 TTS 服务，**无需 GPU、无需本地 AI 模型**。

## 为什么用这个而不是 abogen？

| | abogen | edge-audiobook |
|---|---|---|
| TTS 引擎 | Kokoro-82M (本地 ONNX) | Microsoft Edge (云端) |
| 需要 GPU？ | ✅ 推荐 | ❌ 不需要 |
| 需要下载模型？ | ~330MB | 0 |
| 内存占用 | 2-4GB+ | ~100MB |
| 安装大小 | 5GB+ (含 PyTorch) | ~50MB |
| 离线使用 | ✅ | ❌ (需要网络) |
| 语音质量 | 合成感 | 自然流畅（神经语音） |
| 多语种 | 有限 | 100+ 种语音 |

**如果你的环境是 4GB 内存、2核 CPU 的虚拟机，edge-audiobook 是唯一可行的选择。**

## 安装

```bash
# 使用 uv (推荐)
uv pip install edge-tts ebooklib beautifulsoup4 PyMuPDF soundfile Markdown charset_normalizer chardet

# 或直接安装本项目
cd edge-audiobook
uv pip install -e .
```

如果需要格式转换 (wav 输出)，还需要安装 FFmpeg：
- Windows: `winget install ffmpeg` 或从 https://ffmpeg.org 下载
- 如果只用 mp3 格式，FFmpeg 是可选的

## 使用方法

```bash
# 基本用法
edge-audiobook book.epub

# 指定语音和语速
edge-audiobook book.epub -v en-US-female -s 1.2

# 中文书
edge-audiobook novel.txt -v zh-CN-female -s 1.0

# 按章节单独保存
edge-audiobook book.epub --chapters

# 输出 wav 格式 (需要 FFmpeg)
edge-audiobook book.epub -f wav

# 不生成字幕
edge-audiobook book.epub --no-subtitles

# 使用代理
edge-audiobook book.epub --proxy http://127.0.0.1:7890

# 查看可用语音
edge-audiobook --list-voices
```

## 可用语音预设

| 预设 | 语音 |
|------|------|
| `zh-CN-female` | 晓晓 (女声) |
| `zh-CN-male` | 云希 (男声) |
| `zh-CN-xiaoyi` | 晓伊 |
| `zh-CN-yunjian` | 云健 |
| `en-US-female` | Jenny |
| `en-US-male` | Guy |
| `en-GB-female` | Sonia |
| `en-GB-male` | Ryan |
| `ja-JP-female` | Nanami |
| `ja-JP-male` | Keita |
| `ko-KR-female` | SunHi |
| `multilingual` | Emma (多语言) |

你也可以直接使用 Microsoft Edge 的任何语音 ShortName，例如：
- `zh-CN-XiaoxiaoNeural`
- `zh-CN-YunxiNeural`
- `en-US-AriaNeural`
- `ja-JP-NanamiNeural`

完整列表见 [Microsoft Edge TTS voices](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts)。

## 支持的输入格式

- **EPUB** (.epub) — 自动提取目录和章节
- **PDF** (.pdf) — 支持有目录和无目录的 PDF
- **Markdown** (.md) — 按标题层级分章
- **纯文本** (.txt) — 自动检测章节标记

## 工作原理

```
电子书 (EPUB/PDF/TXT)
    │
    ▼
书本解析器 ──→ 章节列表 (标题 + 正文)
    │
    ▼
文本分块 (每段 ≤ 2000 字符)
    │
    ▼
edge-tts API ──→ MP3 音频流 + 句子边界元数据
    │
    ▼
合并音频 + 生成 SRT 字幕
    │
    ▼
输出: book.mp3 + book.srt
```

## 限制

- **需要稳定的网络连接**（TTS 是云端服务）
- **每次请求最多 ~2000 字符**（自动分块处理）
- **多语音混合** 不支持（若要切换语音，需分段调用）
- **离线使用** 不支持

## 性能参考

在 4GB RAM / 2核 / Windows 10 虚拟机上：
- 一本 10 万字的书 → 约 20-30 分钟 → ~200MB mp3
- 内存占用：< 200MB
- CPU 占用：< 30%

## License

MIT
