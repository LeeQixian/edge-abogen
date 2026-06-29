# edge-audiobook

> # 🤖 本项目由 AI 生成 (Vibe Coding)
>
> **本项目完全由 AI（Claude）辅助编写，是一个 Vibe Coding 项目。**
> 旨在提供一个极轻量、零 GPU 依赖的有声书生成工具。
> 通过自然语言描述需求迭代而成，代码结构以实用优先。
>
> 如遇到问题欢迎提 Issue，但请理解这是 AI 生成代码的探索性项目。

---

## TODO

- [ ] 多角色对话支持（Speaker-based multi-voice dialogue）
- [ ] 语音标记语法（如 `[speaker:name]text[/speaker]`）
- [ ] 批量转换队列
- [ ] 断点续传（长文本中断后恢复）

---

## 致谢

本项目基于以下优秀开源项目构建：

| 项目 | 说明 |
|------|------|
| [abogen](https://github.com/denizsafak/abogen) | 原始有声书生成器，本项目的灵感来源和书本解析逻辑参考 |
| [edge-tts](https://github.com/rany2/edge-tts) | Microsoft Edge TTS Python 接口，本项目的核心 TTS 引擎 |

感谢上述项目的作者们！

---

## 原理

abogen 使用本地 ONNX 模型 (Kokoro-82M) 做 TTS，需要 PyTorch + GPU。
本项目将其 TTS 引擎替换为 edge-tts —— 通过 WebSocket 调用 Microsoft Edge 的免费云端 TTS 服务。
书本解析（EPUB/PDF/Markdown/TXT）、章节检测、字幕生成等逻辑保留，但去掉了所有 GPU 依赖。

> 核心思路：**用云端 API 换掉本地模型，让老旧硬件也能跑。**

## 安装

```bash
# 直接安装本项目
cd edge-audiobook
uv pip install -e .
```

**不需要安装 FFmpeg**！音频拼接使用纯 Python 二进制合并。

## 使用方法

### CLI

```bash
# 基本用法
edge-audiobook book.epub

# 指定语音和语速
edge-audiobook book.epub -v en-US-JennyNeural -s 1.2

# 中文书
edge-audiobook novel.txt -v zh-CN-XiaoxiaoNeural

# 按章节单独保存
edge-audiobook book.epub --chapters

# 不生成字幕
edge-audiobook book.epub --no-subtitles

# 查询可用语音（支持筛选）
edge-audiobook -L zh        # 所有中文语音
edge-audiobook -L en-us-f   # en-US 女声
edge-audiobook -L ja-m      # 日语男声
```

### GUI

```bash
edge-audiobook --gui
```

支持语种 → 地区 → 性别 → Speaker 四级级联选择，输入文字自动筛选。

## 语音筛选语法

```
edge-audiobook -L <语种>[-<地区>][-<性别>]

示例:
  -L zh          → 所有中文语音
  -L en-us       → 所有 en-US 语音
  -L en-us-f     → en-US 女声
  -L ja-m        → 日语男声
  -L jenny       → 名称包含 "jenny" 的语音
```

## 支持的输入格式

- **EPUB** (.epub) — 自动提取目录和章节
- **PDF** (.pdf) — 支持有目录和无目录的 PDF
- **Markdown** (.md) — 按标题层级分章
- **纯文本** (.txt) — 自动检测章节标记

## License

MIT
