# -*- coding: utf-8 -*-
"""阶段二（1）：为每节课生成复习笔记 wiki/notes/NN-课程_日期.md。

- 输入是阶段一产出的整篇讲稿（一节课一篇，约一万多字，单次请求可容纳）。
- 输出截断时自动续写（chat_complete）。
- 幂等：已存在的笔记默认跳过。
"""
import config
import utils
from deepseek_client import chat_complete
from prompts import NOTES_SYSTEM, build_notes_user


def list_clean():
    return sorted(config.CLEAN_DIR.glob(f"{config.COURSE_NAME}_*.md"))


def notes_path(idx: int, lid: str):
    return config.NOTES_DIR / f"{idx:02d}-{config.COURSE_NAME}_{lid}.md"


def existing_for(lid: str):
    hits = list(config.NOTES_DIR.glob(f"*{lid}*.md"))
    return hits[0] if hits else None


def run(force=False):
    config.ensure_dirs()
    cleans = list_clean()
    print(f"=== 阶段二·笔记：处理 {len(cleans)} 节课 ===")
    for idx, c in enumerate(cleans, 1):
        lid = utils.lecture_id_from_path(c)
        out = notes_path(idx, lid)
        prev = existing_for(lid)
        if prev and not force:
            print(f"  跳过(已存在): {prev.name}")
            continue
        text = c.read_text(encoding="utf-8")
        print(f"  生成笔记: {out.name}")
        md = chat_complete(
            NOTES_SYSTEM.replace("{course}", config.COURSE_NAME),
            build_notes_user(config.COURSE_NAME, lid, text),
            temperature=config.TEMP_NOTES,
            tag=f"notes:{lid}",
        )
        utils.write_text(out, md.strip() + "\n")
    print("阶段二·笔记完成。")


def is_complete() -> bool:
    cleans = list_clean()
    if not cleans:
        return False
    return all(existing_for(utils.lecture_id_from_path(c)) for c in cleans)


def status():
    cleans = list_clean()
    done = sum(1 for c in cleans if existing_for(utils.lecture_id_from_path(c)))
    return done, len(cleans)
