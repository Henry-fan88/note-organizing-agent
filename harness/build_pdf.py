# -*- coding: utf-8 -*-
"""把整个 wiki 合并导出为一个 PDF，章节顺序：index -> concepts -> notes。

- 先把各 md 文件按顺序拼成 combined.md / combined.html（始终生成）。
- 文件间插入分页；并把 md 之间的 ../xxx.md 链接改写成 PDF 内锚点，便于跳转。
- PDF 后端按可用性依次尝试：pandoc(xelatex) -> 无头 Chrome -> weasyprint。
  本机已检测到 Chrome 与 xelatex；若未装 pandoc，则默认走 Chrome。
- 全部失败时，仍可手动打开 combined.html，用浏览器“打印 -> 存为 PDF”。
"""
import os
import posixpath
import re
import shutil
import subprocess

import config

# xelatex 无法渲染的彩色 emoji 等字符（保留普通箭头 → 等）
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000026FF\U00002700-\U000027BF"
    "\U00002B00-\U00002BFF\U0000FE0F]"
)


def _strip_emoji(s: str) -> str:
    return _EMOJI_RE.sub("", s)


def _cjk_font() -> str:
    """挑一个 fontconfig 能找到的中文字体（macOS 上 PingFang.ttc 常不被索引）。"""
    prefer = ["Songti SC", "Heiti SC", "STSong", "Hiragino Sans GB",
              "Noto Sans CJK SC", "Arial Unicode MS"]
    try:
        out = subprocess.run(["fc-list", ":lang=zh", "family"],
                             capture_output=True, text=True, timeout=15).stdout
        fams = set()
        for line in out.splitlines():
            for f in line.split(","):
                fams.add(f.strip())
        for p in prefer:
            if p in fams:
                return p
    except Exception:
        pass
    return "Songti SC"


# 开头的“使用指南”（段落式）
USAGE_GUIDE = """# 使用指南

本手册由《中国历史地理》课程的全部课堂录播转写稿整理而成，目的是提供一份既能系统复习、又能随查随用的资料。全书分为三个部分，彼此呼应又各自独立。

**第一部分 · 主题索引** 是全课程的知识地图（MOC）。它不讲新知识，只把分散的概念与笔记按内在逻辑（从基础到进阶、从原因到结果）重新串联起来，帮助你在脑中建立整门课的框架。其中每个条目都可以点击，跳转到对应的概念词条或课堂笔记。建议在通读前后各看一遍，用来建立并检验自己的知识结构。

**第二部分 · 概念词条** 收录课程中出现的重要术语、框架与机制，逐条给出定义、详细解释以及它在课程中的体现。当你在复习时遇到不熟悉的名词，可以到这里按名称查阅；跨多节课出现的概念已做了合并与补充，集中在同一词条之下。

**第三部分 · 课堂笔记** 按上课顺序，对每一节课的内容做了系统整理，并加入了适度的解读。这是最接近“听课”体验的部分，适合从头到尾顺序阅读、做整体复习。

推荐两种用法：如果你想建立全局认识，建议先看第一部分把握逻辑脉络，再用第二部分查漏补缺，最后用第三部分逐节深入。**如果你不太需要索引和概念这两部分，也完全可以直接跳到第三部分，按课时阅读每一节课对应的笔记**，同样能完成复习。

"""

# 注入 LaTeX 前导（高亮分部标题、紧凑小标题、目录行距与缩进）
LATEX_HEADER = r"""
\usepackage{xcolor}
\usepackage[explicit]{titlesec}
\usepackage{tocloft}
\definecolor{partbg}{HTML}{2C3E50}

% —— 分部标题（\section）：整行深色高亮，另起新页由正文中的 \clearpage 控制 ——
\titleformat{\section}[block]
  {\normalfont}{}{0pt}
  {\colorbox{partbg}{\parbox[c]{\dimexpr\textwidth-2\fboxsep\relax}{\color{white}\Large\bfseries #1\strut}}}
\titlespacing*{\section}{0pt}{6pt}{14pt}

% —— 条目标题（概念名 / 主题名 / 课时标题，\subsection）——
\titleformat{\subsection}
  {\normalfont\large\bfseries\color{partbg}}{}{0pt}{#1}
\titlespacing*{\subsection}{0pt}{10pt}{4pt}

% —— 条目内部小标题（定义 / 详细解释 …，\subsubsection）：紧凑 ——
\titleformat{\subsubsection}
  {\normalfont\bfseries}{}{0pt}{#1}
\titlespacing*{\subsubsection}{0pt}{4pt}{1pt}

% —— 目录：标题、行距、次级缩进 ——
\renewcommand{\contentsname}{目录}
\setlength{\cftbeforesecskip}{3pt}
\setlength{\cftbeforesubsecskip}{1pt}
\setlength{\cftsubsecindent}{2.4em}
\renewcommand{\cftsecfont}{\bfseries}
\renewcommand{\cftsecpagefont}{\bfseries}
"""


def _demote(md: str, levels: int = 1) -> str:
    """把一篇文章里的所有标题降一级（# -> ##），跳过代码块。"""
    out, in_code = [], False
    for ln in md.split("\n"):
        if ln.lstrip().startswith("```"):
            in_code = not in_code
            out.append(ln)
            continue
        if not in_code:
            m = re.match(r"^(#{1,6})(\s.*)$", ln)
            if m:
                ln = "#" * min(6, len(m.group(1)) + levels) + m.group(2)
        out.append(ln)
    return "\n".join(out)


def _rewrite_links_anchor(text: str, part: str, anchor_map: dict) -> str:
    """把指向其它 md 文件的链接改写成 #锚点（pandoc 内部跳转）。"""
    def repl(m):
        label, target = m.group(1), m.group(2)
        t = target.split("#")[0]
        if not t.endswith(".md"):
            return m.group(0)
        key = posixpath.normpath(posixpath.join(part, t))
        a = anchor_map.get(key)
        return f"[{label}](#{a})" if a else label
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl, text)


def _raw(latex: str) -> str:
    return "```{=latex}\n" + latex + "\n```\n"


def build_pandoc_source():
    """生成专供 pandoc/xelatex 使用的合并 Markdown（含使用指南、分部、锚点）。"""
    config.ensure_dirs()
    files = _ordered_files()
    anchor_map = {f"{part}/{p.name}": f"sec{i}" for i, (part, p) in enumerate(files, 1)}
    part_titles = {"index": "第一部分 · 主题索引",
                   "concepts": "第二部分 · 概念词条",
                   "notes": "第三部分 · 课堂笔记"}
    part_intro = {
        "index": "本部分为全课程的主题索引（内容地图），仅含链接与逻辑结构。",
        "concepts": "本部分为课程重要概念的详细词条，按名称排列。",
        "notes": "本部分为每一节课的系统整理笔记，按上课顺序排列。",
    }
    out = [USAGE_GUIDE,
           _raw("\\clearpage\n\\setcounter{tocdepth}{2}\n\\tableofcontents")]
    last = None
    for idx, (part, p) in enumerate(files):
        if part != last:
            if last == "concepts":            # 离开概念部分，结束紧凑分组
                out.append(_raw("\\endgroup"))
            out.append(_raw("\\clearpage"))    # 每部分另起新页
            out.append(f"# {part_titles[part]}\n\n{part_intro[part]}\n")
            if part == "concepts":            # 概念部分整体收紧行距
                out.append(_raw("\\begingroup\\setlength{\\parskip}{2pt}\\linespread{0.97}\\selectfont"))
            last = part
        body = _strip_emoji(_strip_frontmatter(p.read_text(encoding="utf-8", errors="ignore")))
        body = _demote(body, 1)
        anchor = anchor_map[f"{part}/{p.name}"]
        body = re.sub(r"^(##\s+.+?)\s*$", r"\1 {#" + anchor + "}", body, count=1, flags=re.M)
        body = _rewrite_links_anchor(body, part, anchor_map)
        out.append(body)
        nxt = files[idx + 1] if idx + 1 < len(files) else None
        if part == "concepts" and nxt and nxt[0] == "concepts":
            out.append("\n\n------\n\n")     # 概念之间的分割线
    if last == "concepts":
        out.append(_raw("\\endgroup"))
    src = config.BUILD_DIR / "combined_pandoc.md"
    src.write_text("\n\n".join(out), encoding="utf-8")
    header = config.BUILD_DIR / "pandoc_header.tex"
    header.write_text(LATEX_HEADER, encoding="utf-8")
    return src, header

CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: "PingFang SC","Songti SC","Heiti SC","Microsoft YaHei",serif;
       font-size: 11.5pt; line-height: 1.7; color:#1a1a1a; }
h1 { font-size: 20pt; border-bottom: 2px solid #333; padding-bottom: 4px; }
h2 { font-size: 15.5pt; margin-top: 1.2em; border-bottom: 1px solid #ccc; }
h3 { font-size: 13pt; }
code { background:#f3f3f3; padding:1px 4px; border-radius:3px; }
blockquote { color:#555; border-left:3px solid #ccc; margin:0.6em 0; padding:0.2em 0.9em; background:#fafafa; }
a { color:#0b5; text-decoration:none; }
ul,ol { margin:0.3em 0 0.6em 1.4em; }
.section { page-break-before: always; }
.part-cover { page-break-before: always; text-align:center; margin-top:35vh; }
.part-cover h1 { font-size:30pt; border:none; }
"""


def _ordered_files():
    files = []
    idx = sorted(config.INDEX_DIR.glob("*.md"))
    idx.sort(key=lambda p: (not p.name.startswith("00-"), p.name))  # 总索引置顶
    files += [("index", p) for p in idx]
    files += [("concepts", p) for p in sorted(config.CONCEPTS_DIR.glob("*.md"))]
    files += [("notes", p) for p in sorted(config.NOTES_DIR.glob("*.md"))]
    return files


def _anchor_for(part, path):
    rel = f"{part}/{path.name}"
    return "sec-" + re.sub(r"[^0-9A-Za-z一-鿿]+", "-", rel).strip("-")


def _strip_frontmatter(text):
    t = text.lstrip("﻿").lstrip("\n")
    # 1) 标准 YAML frontmatter：--- ... ---（概念文件）
    if t.startswith("---"):
        end = t.find("\n---", 3)
        if end != -1:
            nl = t.find("\n", end + 1)
            return t[nl + 1:] if nl != -1 else ""
    # 2) 被代码围栏包裹的 frontmatter：```yaml ... ```（LLM 生成的笔记文件）
    m = re.match(r"^```[A-Za-z]*\s*\n.*?\n```[ \t]*\n", t, flags=re.S)
    if m and any(k in t[:m.end()] for k in ("title:", "lecture:", "type:")):
        return t[m.end():]
    return text


def _rewrite_links(text, part, anchor_map):
    """把指向其它 md 文件的相对链接改写成 #锚点。"""

    def repl(m):
        label, target = m.group(1), m.group(2)
        t = target.split("#")[0]
        if not t.endswith(".md"):
            return m.group(0)
        # 相对当前文件所在部分（part 目录）解析，并折叠 ../
        key = posixpath.normpath(posixpath.join(part, t))
        anchor = anchor_map.get(key)
        return f"[{label}](#{anchor})" if anchor else f"[{label}]"

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl, text)


def build_combined():
    config.ensure_dirs()
    files = _ordered_files()
    if not files:
        print("  wiki 为空，无可导出内容。")
        return None, None

    anchor_map = {}
    for part, p in files:
        anchor_map[f"{part}/{p.name}"] = _anchor_for(part, p)

    part_titles = {"index": "第一部分 · 主题索引",
                   "concepts": "第二部分 · 概念词条",
                   "notes": "第三部分 · 课堂笔记"}
    md_parts = [f"# {config.COURSE_NAME} · 复习合订本\n"]
    last_part = None
    for part, p in files:
        if part != last_part:
            md_parts.append(f'\n<div class="part-cover">\n\n# {part_titles[part]}\n\n</div>\n')
            last_part = part
        body = _strip_frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
        body = _rewrite_links(body, part, anchor_map)
        anchor = anchor_map[f"{part}/{p.name}"]
        md_parts.append(f'\n<div class="section" id="{anchor}"></div>\n\n{body}\n')

    combined_md = "\n".join(md_parts)
    md_path = config.BUILD_DIR / "combined.md"
    md_path.write_text(combined_md, encoding="utf-8")

    html_body = _md_to_html(combined_md)
    html = (f'<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            f"<title>{config.COURSE_NAME} 复习合订本</title><style>{CSS}</style></head>"
            f"<body>{html_body}</body></html>")
    html_path = config.BUILD_DIR / "combined.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"  已生成 {md_path.name} 与 {html_path.name}（共 {len(files)} 个文件）")
    return md_path, html_path


def _md_to_html(md_text):
    try:
        import markdown  # type: ignore
        return markdown.markdown(md_text, extensions=["extra", "sane_lists", "nl2br"])
    except Exception:
        # 兜底：未装 markdown 库时，原样放进 <pre>（仍可打印，只是不美观）
        import html as _h
        return f"<pre style='white-space:pre-wrap'>{_h.escape(md_text)}</pre>"


def _chrome_bin():
    for c in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ):
        if shutil.which(c) or __import__("os").path.exists(c):
            return c
    return None


def to_pdf(md_path, html_path, backend="auto"):
    out_pdf = config.BUILD_DIR / f"{config.COURSE_NAME}_复习合订本.pdf"
    try:
        out_pdf.unlink()  # 删除旧产物，避免失败时误报成功
    except FileNotFoundError:
        pass

    def try_pandoc():
        if not shutil.which("pandoc"):
            return False
        font = _cjk_font()
        src, header = build_pandoc_source()  # 结构化源（含使用指南/分部/锚点）+ LaTeX 前导
        env = dict(os.environ)
        tinytex = os.path.expanduser("~/Library/TinyTeX/bin/universal-darwin")
        if os.path.isdir(tinytex):  # 确保 xelatex 在 PATH 上
            env["PATH"] = tinytex + os.pathsep + env.get("PATH", "")
        cmd = ["pandoc", str(src), "-o", str(out_pdf), "--pdf-engine=xelatex",
               "-V", f"CJKmainfont={font}", "-V", "CJKoptions=AutoFakeBold=3",
               "-V", "geometry:margin=2cm", "-V", "linkcolor=blue",
               "-V", "title=中国历史地理 · 复习合订本",
               f"--include-in-header={header}"]
        print(f"  使用 pandoc + xelatex 导出（CJK 字体：{font}）...")
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if r.returncode != 0:
            print("  [warn] pandoc 失败：", (r.stderr or r.stdout)[-600:])
        return r.returncode == 0 and out_pdf.exists()

    def try_chrome():
        b = _chrome_bin()
        if not b:
            return False
        print("  使用无头 Chrome 导出 ...")
        cmd = [b, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
               f"--print-to-pdf={out_pdf}", html_path.as_uri()]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:  # 老版本 Chrome 参数兼容
            cmd[1] = "--headless"
            r = subprocess.run(cmd, capture_output=True)
        return out_pdf.exists()

    def try_weasy():
        try:
            from weasyprint import HTML  # type: ignore
        except Exception:
            return False
        print("  使用 weasyprint 导出 ...")
        HTML(string=html_path.read_text(encoding="utf-8")).write_pdf(str(out_pdf))
        return out_pdf.exists()

    order = {"pandoc": [try_pandoc], "chrome": [try_chrome], "weasyprint": [try_weasy],
             "auto": [try_pandoc, try_chrome, try_weasy]}[backend]
    for fn in order:
        try:
            if fn():
                print(f"  ✅ PDF 已生成：{out_pdf}")
                return out_pdf
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] 该后端失败：{e}")
    print("  ⚠️ 未能自动生成 PDF。请手动打开以下文件，用浏览器“打印 → 存为 PDF”：")
    print(f"     {html_path}")
    return None


def run(backend="auto"):
    print("=== 导出 PDF（顺序：索引 → 概念 → 笔记）===")
    md_path, html_path = build_combined()
    if md_path:
        to_pdf(md_path, html_path, backend=backend)
