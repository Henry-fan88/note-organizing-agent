# -*- coding: utf-8 -*-
"""阶段二（3）：主题索引（MOC）wiki/index/*.md。

在 notes 与 concepts 全部就绪后运行：
- 收集全部概念（来自 registry）与全部笔记（来自 notes 目录），各自编号 C1.../N1...。
- 用一次聚类请求把它们组织成若干主题；条目用编号引用以控制输出体量。
  返回为空/解析失败时自动重试；多次仍失败则用“按课时分组”的确定性兜底，保证索引必出。
- 在本地把聚类结果渲染成 Markdown 索引（只含链接与逻辑结构，不写新知识）。
- 额外生成总索引 00-总索引.md 作为检索总入口。
索引是派生产物：每次运行会清空旧的 index/*.md 重新生成。
"""
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
            "lectures": e.get("lectures", []),
        })
    out.sort(key=lambda x: x["title"])
    for i, c in enumerate(out, 1):
        c["id"] = f"C{i}"
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
    for i, n in enumerate(out, 1):
        n["id"] = f"N{i}"
    return out


def _cluster(concepts, notes):
    """带重试的聚类；返回 topics 列表（可能为空，交由上层兜底）。"""
    for attempt in range(3):
        raw, _ = chat(
            [
                {"role": "system", "content": INDEX_CLUSTER_SYSTEM},
                {"role": "user", "content": build_index_cluster_user(config.COURSE_NAME, concepts, notes)},
            ],
            temperature=config.TEMP_INDEX,
            max_tokens=config.MAX_TOKENS,
            json_mode=True,
            tag=f"index-cluster:{attempt + 1}",
        )
        raw = (raw or "").strip()
        if not raw:
            print(f"  [warn] 聚类返回为空（第 {attempt + 1}/3 次），重试 ...")
            continue
        try:
            topics = utils.parse_json_loose(raw).get("topics", []) or []
            if topics:
                return topics
            print(f"  [warn] 聚类未给出主题（第 {attempt + 1}/3 次），重试 ...")
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] 聚类 JSON 解析失败（第 {attempt + 1}/3 次）：{e}，重试 ...")
    return []


def _fallback_topics(concepts, notes):
    """确定性兜底：按课时把概念与笔记分组，概念归入其首次出现的课时。"""
    print("  使用按课时分组的确定性兜底索引。")
    by_lid = {}
    for c in concepts:
        lid = (c.get("lectures") or ["未归类"])[0]
        by_lid.setdefault(lid, {"concepts": [], "note": None})["concepts"].append(c)
    for n in notes:
        by_lid.setdefault(n["lecture"], {"concepts": [], "note": None})["note"] = n
    topics = []
    for lid in sorted(by_lid):
        g = by_lid[lid]
        stages = []
        if g["note"]:
            stages.append({"stage": "本节笔记", "items": [{"id": g["note"]["id"], "role": "整节内容"}]})
        if g["concepts"]:
            stages.append({"stage": "本节概念",
                           "items": [{"id": c["id"], "role": ""} for c in g["concepts"]]})
        topics.append({"title": f"第 {lid} 节", "overview": f"{lid} 这节课涉及的笔记与概念。", "stages": stages})
    return topics


def _resolve(item, cmap, nmap, concepts, notes):
    """把聚类条目映射到实际文件，返回 (类型, 显示名, 相对链接) 或 None。"""
    rid = (item.get("id") or "").strip()
    if rid in cmap:
        c = cmap[rid]
        return "concept", c["title"], f"../concepts/{c['slug']}.md"
    if rid in nmap:
        n = nmap[rid]
        return "note", f"{n['title']}（{n['lecture']}）", f"../notes/{n['name']}"
    # 兜底：模型若用了标题而非编号
    title = (item.get("title") or "").strip()
    if title:
        for c in concepts:
            if title == c["title"] or utils.sanitize_filename(title) == c["slug"]:
                return "concept", c["title"], f"../concepts/{c['slug']}.md"
        for n in notes:
            if title == n["title"] or n["lecture"] in title:
                return "note", f"{n['title']}（{n['lecture']}）", f"../notes/{n['name']}"
    return None


def _render_topic(topic, cmap, nmap, concepts, notes):
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
            r = _resolve(it, cmap, nmap, concepts, notes)
            if not r:
                continue
            any_item = True
            typ, disp, link = r
            role = (it.get("role") or "").strip()
            tag = "概念" if typ == "concept" else "笔记"
            suffix = f" —— {role}" if role else ""
            lines.append(f"- [{tag}] [{disp}]({link}){suffix}")
        if not any_item:
            lines.append("（暂无可链接条目）")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_master(concepts, notes, topic_files):
    lines = [f"# {config.COURSE_NAME} · 总索引", ""]
    lines += ["> 全课程检索总入口。仅含链接与结构。", "", "## 主题索引", ""]
    for t, fname in topic_files:
        lines.append(f"- [{t.get('title', '主题')}]({fname})")
    lines += ["", "## 全部课堂笔记（按课时）", ""]
    for n in notes:
        lines.append(f"- [{n['title']}（{n['lecture']}）](../notes/{n['name']})")
    lines += ["", "## 全部概念词条（按标题自然序）", ""]
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
    cmap = {c["id"]: c for c in concepts}
    nmap = {n["id"]: n for n in notes}

    print(f"=== 阶段二·索引：{len(concepts)} 概念 / {len(notes)} 笔记 ===")
    print("  聚类与逻辑链排序 ...")
    topics = _cluster(concepts, notes)
    if not topics:
        topics = _fallback_topics(concepts, notes)
    print(f"  生成 {len(topics)} 个主题索引")

    # 清空旧索引（派生产物）
    for old in config.INDEX_DIR.glob("*.md"):
        old.unlink()

    topic_files = []
    for i, t in enumerate(topics, 1):
        slug = utils.sanitize_filename(t.get("title", f"主题{i}"))
        fname = f"{i:02d}-{slug}.md"
        utils.write_text(config.INDEX_DIR / fname, _render_topic(t, cmap, nmap, concepts, notes))
        topic_files.append((t, fname))

    utils.write_text(config.INDEX_DIR / "00-总索引.md",
                     _render_master(concepts, notes, topic_files))
    print("阶段二·索引完成。")


def is_complete() -> bool:
    return (config.INDEX_DIR / "00-总索引.md").exists()


def status():
    return len(list(config.INDEX_DIR.glob("*.md")))
