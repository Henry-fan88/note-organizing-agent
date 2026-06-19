# -*- coding: utf-8 -*-
"""阶段二（2）：跨课时的概念词条库 wiki/concepts/<概念>.md。

核心难点是跨课去重与补齐：
- 维护一个 registry（state/concepts.json）记录每个概念的标题、别名、文件、摘要、涉及课时。
- 逐节课处理：把“已有概念清单”塞进抽取请求，让模型对齐到已有概念或判定为全新。
  * 全新概念   -> 直接用模板写一个新词条文件（不额外调用 API）。
  * 已有概念   -> 调用合并请求，把本节新增角度融合进已有词条，去重、不编造。
- 进度记录在 state/concepts_progress.json（按课时），支持中断续跑。
"""
import config
import utils
from deepseek_client import ContentRiskError, chat, chat_complete
from prompts import (
    CONCEPT_EXTRACT_SYSTEM,
    CONCEPT_MERGE_SYSTEM,
    build_concept_extract_user,
    build_concept_merge_user,
    build_new_concept_file,
)

REG_PATH = config.STATE_DIR / "concepts.json"
PROG_PATH = config.STATE_DIR / "concepts_progress.json"


def _load_registry():
    return utils.load_json(REG_PATH, {})


def _save_registry(reg):
    utils.save_json(REG_PATH, reg)


def _find_existing(reg, title):
    """按 slug / 标题 / 别名匹配已有概念，返回 slug 或 None。"""
    slug = utils.sanitize_filename(title)
    if slug in reg:
        return slug
    for s, e in reg.items():
        if title == e.get("title") or title in (e.get("aliases") or []):
            return s
    return None


def _extract(lid, text, reg, max_depth=5):
    """抽取本节概念。被内容审核拦截时按行二分递归、合并各段概念；
    到最小粒度仍被拒则跳过该片段（返回空），不中断流程。下游按标题去重。"""
    existing = [e["title"] for e in reg.values()]

    def _go(seg_lines, depth, sub_tag):
        seg = "\n".join(seg_lines)
        try:
            raw, _ = chat(
                [
                    {"role": "system", "content": CONCEPT_EXTRACT_SYSTEM.replace("{course}", config.COURSE_NAME)},
                    {"role": "user", "content": build_concept_extract_user(config.COURSE_NAME, lid, seg, existing)},
                ],
                temperature=config.TEMP_CONCEPTS,
                json_mode=True,
                tag=sub_tag,
            )
            return utils.parse_json_loose(raw).get("concepts", []) or []
        except ContentRiskError:
            if len(seg_lines) > 1 and depth < max_depth:
                mid = len(seg_lines) // 2
                print(f"    [risk] {sub_tag} 被拦截，拆成 {mid}+{len(seg_lines) - mid} 行分别抽取 ...")
                return _go(seg_lines[:mid], depth + 1, sub_tag + "a") + _go(seg_lines[mid:], depth + 1, sub_tag + "b")
            print(f"    [risk] {sub_tag} 最小粒度仍被拦截，该片段跳过概念抽取。")
            return []

    return _go(text.splitlines(), 0, f"concept-extract:{lid}")


def _concept_file(slug):
    return config.CONCEPTS_DIR / f"{slug}.md"


def _create(title, lid, con, reg):
    slug = utils.sanitize_filename(title)
    # 极少数 slug 撞名（不同标题清理后相同）时加后缀
    base, n = slug, 2
    while slug in reg:
        slug = f"{base}-{n}"
        n += 1
    f = _concept_file(slug)
    utils.write_text(f, build_new_concept_file(config.COURSE_NAME, title, con, lid))
    reg[slug] = {
        "title": title,
        "aliases": con.get("aliases") or [],
        "file": str(f.relative_to(config.ROOT)),
        "summary": (con.get("summary") or "").strip(),
        "lectures": [lid],
    }
    print(f"      + 新建概念: {title}")


def _supplement(slug, lid, con, reg):
    entry = reg[slug]
    if lid in entry.get("lectures", []):
        return  # 这节课已并入过，避免重复合并
    f = config.ROOT / entry["file"]
    if not f.exists():
        f = _concept_file(slug)
    existing_md = f.read_text(encoding="utf-8") if f.exists() else ""
    if not existing_md.strip():
        # 文件意外缺失，退化为新建
        _create(entry["title"], lid, con, reg)
        return
    try:
        merged = chat_complete(
            CONCEPT_MERGE_SYSTEM,
            build_concept_merge_user(entry["title"], existing_md, lid, con),
            temperature=config.TEMP_CONCEPTS,
            tag=f"concept-merge:{slug}:{lid}",
        )
    except ContentRiskError:
        # 合并请求被内容审核拦截：保留原词条不变，仅记下本课时已涉及，不中断。
        print(f"      [risk] 合并 {entry['title']} (+{lid}) 被内容审核拦截，保留原词条不变。")
        entry.setdefault("lectures", []).append(lid)
        return
    utils.write_text(f, merged.strip() + "\n")
    entry.setdefault("lectures", []).append(lid)
    # 别名累积
    for a in con.get("aliases") or []:
        if a and a not in entry["aliases"]:
            entry["aliases"].append(a)
    print(f"      ~ 补充概念: {entry['title']} (+{lid})")


def run(force=False):
    config.ensure_dirs()
    reg = _load_registry()
    progress = utils.load_json(PROG_PATH, {})
    cleans = sorted(config.CLEAN_DIR.glob(f"{config.COURSE_NAME}_*.md"))
    print(f"=== 阶段二·概念：扫描 {len(cleans)} 节课 ===")
    for c in cleans:
        lid = utils.lecture_id_from_path(c)
        if progress.get(lid) and not force:
            print(f"  跳过已处理: {lid}")
            continue
        text = c.read_text(encoding="utf-8")
        print(f"  抽取概念: {lid}")
        concepts = _extract(lid, text, reg)
        print(f"    本节候选概念 {len(concepts)} 个")
        for con in concepts:
            title = (con.get("title") or "").strip()
            if not title:
                continue
            slug = _find_existing(reg, title)
            if slug:
                _supplement(slug, lid, con, reg)
            else:
                _create(title, lid, con, reg)
            _save_registry(reg)  # 每条都落盘，最大限度抗中断
        progress[lid] = True
        utils.save_json(PROG_PATH, progress)
    _save_registry(reg)
    print(f"阶段二·概念完成。共 {len(reg)} 个概念词条。")


def is_complete() -> bool:
    cleans = list(config.CLEAN_DIR.glob(f"{config.COURSE_NAME}_*.md"))
    if not cleans:
        return False
    progress = utils.load_json(PROG_PATH, {})
    return all(progress.get(utils.lecture_id_from_path(c)) for c in cleans)


def status():
    cleans = list(config.CLEAN_DIR.glob(f"{config.COURSE_NAME}_*.md"))
    progress = utils.load_json(PROG_PATH, {})
    done = sum(1 for c in cleans if progress.get(utils.lecture_id_from_path(c)))
    reg = _load_registry()
    return done, len(cleans), len(reg)
