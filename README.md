# 中国历史地理 · 笔记整理 Harness

把本地 Whisper 转写稿，分两阶段整理成一套可复习的中文 wiki，并导出为一个 PDF。
全流程接入 **DeepSeek API**，由 `harness/` 下的脚本编排。

---

## 0. 目录结构

```
笔记整理agent/
├── raw-transcripts/        # 输入：Whisper 转写稿（已存在，13 个 .txt）
├── clean-transcripts/      # 阶段一产出：清洗后的讲稿 .md
├── wiki/
│   ├── notes/              # 阶段二·笔记：每节课一篇
│   ├── concepts/           # 阶段二·概念：跨课时去重/补齐的词条
│   ├── index/              # 阶段二·索引：主题 MOC + 00-总索引
│   └── _build/             # 合并产物：combined.md / combined.html / 最终 PDF
├── harness/                # 全部代码
│   ├── config.py           # 配置（路径/模型/温度/分块），读 .env
│   ├── deepseek_client.py  # API 封装（重试/超时/续写/用量日志）
│   ├── prompts.py          # 所有提示词与模板（质量控制核心）
│   ├── phase1_clean.py     # 阶段一
│   ├── phase2_notes.py     # 阶段二·笔记
│   ├── phase2_concepts.py  # 阶段二·概念
│   ├── phase2_index.py     # 阶段二·索引
│   ├── build_pdf.py        # 合并导出 PDF
│   ├── run.py              # 统一入口（编排+门禁）
│   ├── state/              # 进度与概念库（concepts.json / *_progress.json）
│   ├── logs/               # usage.csv（token 用量）
│   └── requirements.txt
├── .env.example            # 配置样例
└── README.md
```

---

## 1. 接入 API（三步）

```bash
# (1) 安装依赖（建议用虚拟环境）
cd "/Users/henryfan/Local/笔记整理agent"
python3 -m venv .venv && source .venv/bin/activate
pip install -r harness/requirements.txt

# (2) 配置密钥与模型
cp .env.example .env
#   编辑 .env，填入 DEEPSEEK_API_KEY；
#   把 DEEPSEEK_MODEL 设为 DeepSeek 文档中“V4 Pro”的模型 ID
#   （不确定时先用默认的 deepseek-chat 兜底）

# (3) 自检（不花钱）
python harness/run.py status     # 看进度 + 是否已读到 Key
python harness/run.py dryrun     # 预估清洗分块数，不调用 API
```

> DeepSeek 用的是 OpenAI 兼容协议，base_url 默认 `https://api.deepseek.com`，
> 已在 `.env.example` 写好，无需改动。

---

## 2. 启动（推荐分阶段，便于抽查）

```bash
python harness/run.py clean       # 阶段一：清洗 13 个转写 -> clean-transcripts/
#   ⬆ 跑完后请抽查几篇 clean-transcripts/*.md，确认忠实、无遗漏，再继续

python harness/run.py notes       # 阶段二·笔记（门禁：阶段一须全部完成）
python harness/run.py concepts    # 阶段二·概念（跨课时去重/补齐）
python harness/run.py index       # 阶段二·索引（门禁：笔记+概念须全部完成）
python harness/run.py pdf         # 导出 PDF（顺序：索引→概念→笔记）
```

一键全流程（带阶段门禁，前一阶段没达标不会进入下一阶段）：

```bash
python harness/run.py all
```

**幂等可续跑**：任何阶段中断后，重跑会自动跳过已完成的文件/课时，只补未完成的。
想重做某阶段，加 `--force`（如 `python harness/run.py clean --force`）。

---

## 3. 导出 PDF

- 默认后端探测顺序：`pandoc(xelatex) → 无头 Chrome → weasyprint`。
- 本机已检测到 **Chrome** 和 **xelatex**：未装 pandoc 时会自动用 Chrome 直出 PDF。
- 想要更精致排版可 `brew install pandoc`，本机已有 TinyTeX(xelatex)，pandoc 会优先被使用。
- 指定后端：`python harness/run.py pdf --backend chrome`。
- 兜底：任何后端都失败时，手动打开 `wiki/_build/combined.html`，浏览器“打印 → 存为 PDF”。
- 产物：`wiki/_build/中国历史地理_复习合订本.pdf`，章节顺序 **索引 → 概念 → 笔记**，
  且文件间交叉链接已改写为 PDF 内部可点击锚点。

---

## 4. 设计如何防“记忆丢失 / 重复 / 编造”

- **按任务自包含调用，不养长对话**：每个子任务（清洗某块、写某篇笔记、合并某个概念、
  做索引聚类）都用一组干净、范围明确的 messages，需要的上下文显式塞进 prompt，
  不让无关历史撑爆上下文窗口。
- **阶段一忠实优先**：本地先折叠连续重复行 → 按行边界分块（约 3500 字/块）逐块清洗；
  系统提示词强制“只整理不增添、去重去噪、保全全部实质内容”；温度 0.2；
  清洗后字数过少会告警，提示抽查是否过度删减。
- **概念跨课去重/补齐**：维护 `state/concepts.json` 概念库；抽取时把“已有概念清单”
  喂给模型做对齐——已存在则走“合并”请求（融合新角度、去重、不编造、登记课时），
  全新才建新词条。
- **索引只做地图**：在笔记+概念全部就绪后，一次聚类把条目按**逻辑链条**（基础→进阶 /
  因→果）分主题、分阶段排列；只输出链接与结构，不写新知识。
- **明确的完成判据 + 门禁**：每阶段都有 `is_complete()`；`run.py` 在前一阶段未达标时
  拒绝进入下一阶段，确保达成目标才推进或停止。
- **可观测**：`harness/logs/usage.csv` 记录每次调用的 token 用量。

---

## 5. 常见调整

- 换模型 / 调温度 / 改分块大小：编辑 `.env`（见 `.env.example` 注释）。
- 改提示词风格（笔记结构、概念详略、索引粒度）：编辑 `harness/prompts.py`。
- 换一门别的课：把转写放进 `raw-transcripts/`，在 `.env` 设 `COURSE_NAME`，重跑即可
  （文件名需含 `YYYY-MM-DD` 作为课时号）。
```
