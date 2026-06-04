# -*- coding: utf-8 -*-
"""把整个 wiki 合并导出为一个 PDF，章节顺序：index -> concepts -> notes。

- 先把各 md 文件按顺序拼成 combined.md / combined.html（始终生成）。
- 文件间插入分页；并把 md 之间的 ../xxx.md 链接改写成 PDF 内锚点，便于跳转。
- PDF 后端按可用性依次尝试：pandoc(xelatex) -> 无头 Chrome -> weasyprint。
  本机已检测到 Chrome 与 xelatex；若未装 pandoc，则默认走 Chrome。
- 全部失败时，仍可手动打开 combined.html，用浏览器“打印 -> 存为 PDF”。
"""
import posixpath
import re
import shutil
import subprocess

import config

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
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[text.find("\n", end + 1) + 1:]
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

    def try_pandoc():
        if not shutil.which("pandoc"):
            return False
        cmd = ["pandoc", str(md_path), "-o", str(out_pdf), "--pdf-engine=xelatex",
               "-V", "CJKmainfont=PingFang SC", "-V", "geometry:margin=2cm", "--toc"]
        print("  使用 pandoc + xelatex 导出 ...")
        return subprocess.run(cmd).returncode == 0

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
