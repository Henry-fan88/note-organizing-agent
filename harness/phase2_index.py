# -*- coding: utf-8 -*-
"""阶段二（3）：主题索引（MOC）wiki/index/*.md。

在 notes 与 concepts 全部就绪后运行：
- 收集全部概念（来自 registry）与全部笔记（来自 notes 目录）的标题/摘要。
- 用一次聚类请求，让模型把它们组织成若干主题，每个主题内部按【逻辑链条】排序并分阶段。
- 在本地把聚类结果渲染成 Markdown 索引（只含链接与逻辑结构，不写新知识）。
- 额外生成一个总索引 00-总索引.md 作为检索总入口。
索引是派生产物：每次运行会清空旧的 index/*.md 重新生成。
"""
import re

import config
import utils
from deepseek_client import chat
from phase2_concepts import _load_registry
from prompts import INDEX_CLUSTER_SYSTEM, build_index_cluster_user


def _collect_concepts():
    reg = _load_registry()
    out = []
    for slug, e in reg.items():
        out.append({
            "slug": slug,
            "title": e["title"],
            "summary": e.get("summary", ""),
            "file": e.get("file", f"wiki/concepts/{slug}.md"),
        })
    out.sort(key=lambda x: x["title"])
    return out


def _read_h1(path):
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if ln.startswith("# "):
            return ln[2:].strip()
    return path.stem


def _collect_notes():
    out = []
    for f in sorted(config.NOTES_DIR.glob("*.md")):
        lid = utils.lecture_id_from_path(f)
        out.append({"title": _read_h1(f), "lecture": lid, "file": f, "name": f.name})
    return out


def _cluster(concepts, notes):
    raw, _ = chat(
        [
            {"role": "system", "content": INDEX_CLUSTER_SYSTEM},
            {"role": "user", "content": build_index_cluster_user(config.COURSE_NAME, concepts, notes)},
        ],
        temperature=config.TEMP_INDEX,
        json_mode=True,
        tag="index-cluster",
    )
    return utils.parse_json_loose(raw).get("topics", []) or []


def _resolve(item, concepts, notes):
    """把聚类条目映射到实际文件，返回 (显示名, 相对index目录的链接) 或 None。"""
    title = (item.get("title") or "").strip()
    typ = item.get("type", "")
    if typ == "concept":
        for c in concepts:
            if c["title"] == title:
                return c["title"], f"../concepts/{c['slug']}.md"
        s = utils.sanitize_filename(title)
        for c in concepts:
            if c["slug"] == s:
                return c["title"], f"../concepts/{c['slug']}.md"
        for c in concepts:
            if title and (title in c["title"] or c["title"] in title):
                return c["title"], f"../concepts/{c['slug']}.md"
    else:  # note
        for n in notes:
            if n["title"] == title or n["lecture"] in title or title in n["title"]:
                return f"{n['title']}（{n['lecture']}）", f"../notes/{n['name']}"
    return None


def _render_topic(topic, concepts, notes):
    title = topic.get("title", "未命名主题")
    lines = [f"# 主题索引 · {title}", ""]
    ov = (topic.get("overview") or "").strip()
    if ov:
        lines += [f"> {ov}", ""]
    lines += ["> 本文为内容地图（MOC），仅含链接与逻辑结构，不含新知识。", ""]
    for stage in topic.get("stages", []):
        lines.append(f"## {stage.get('stage', '环节')}")
        lines.append("")
        any_item = False
        for it in stage.get("items", []):
            r = _resolve(it, concepts, notes)
            if not r:
                continue
            any_item = True
            disp, link = r
            role = (it.get("role") or "").strip()
            tag = "概念" if it.get("type") == "concept" else "笔记"
            suffix = f" —— {role}" if role else ""
            lines.append(f"- [{tag}] [{disp}]({link}){suffix}")
        if not any_item:
            lines.append("（暂无可链接条目）")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_master(topics, concepts, notes, topic_files):
    lines = [f"# {config.COURSE_NAME} · 总索引", ""]
    lines += ["> 全课程检索总入口。仅含链接与结构。", "", "## 主题索引", ""]
    for t, fname in topic_files:
        lines.append(f"- [{t.get('title','主题')}]({fname})")
    lines += ["", "## 全部课堂笔记（按课时）", ""]
    for n in notes:
        lines.append(f"- [{n['title']}（{n['lecture']}）](../notes/{n['name']})")
    lines += ["", "## 全部概念词条（按拼音/笔画自然序）", ""]
    for c in concepts:
        summ = f" —— {c['summary']}" if c.get("summary") else ""
        lines.append(f"- [{c['title']}](../concepts/{c['slug']}.md){summ}")
    return "\n".join(lines) + "\n"


def run(force=True):
    config.ensure_dirs()
    concepts = _collect_concepts()
    notes = _collect_notes()
    if not concepts and not notes:
        print("  没有可索引的概念或笔记，跳过。")
        return
    print(f"=== 阶段二·索引：{len(concepts)} 概念 / {len(notes)} 笔记 ===")
    print("  聚类与逻辑链排序 ...")
    topics = _cluster(concepts, notes)
    print(f"  生成 {len(topics)} 个主题索引")

    # 清空旧索引（派生产物）
    for old in config.INDEX_DIR.glob("*.md"):
        old.unlink()

    topic_files = []
    for i, t in enumerate(topics, 1):
        slug = utils.sanitize_filename(t.get("title", f"主题{i}"))
        fname = f"{i:02d}-{slug}.md"
        utils.write_text(config.INDEX_DIR / fname, _render_topic(t, concepts, notes))
        topic_files.append((t, fname))

    utils.write_text(config.INDEX_DIR / "00-总索引.md",
                     _render_master(topics, concepts, notes, topic_files))
    print("阶段二·索引完成。")


def is_complete() -> bool:
    return (config.INDEX_DIR / "00-总索引.md").exists()


def status():
    n = len(list(config.INDEX_DIR.glob("*.md")))
    return n
