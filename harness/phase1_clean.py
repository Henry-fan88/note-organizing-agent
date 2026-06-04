# -*- coding: utf-8 -*-
"""阶段一：把 raw-transcripts/*.txt 清洗为 clean-transcripts/*.md。

策略：
- 先本地折叠“连续完全相同的行”（去掉 Whisper 卡顿/字幕署名重复），省 token 也更稳。
- 按行边界切成若干块（每块约 CLEAN_CHUNK_CHARS 字），逐块独立清洗：
  每块都是一个干净、范围明确的请求，避免长上下文导致的串台/遗漏。
- 拼接各块结果。最后做“覆盖率自检”，若清洗后字数过少则告警（可能过度删减）。
- 幂等：已生成的 .md 默认跳过，除非 force。
"""
import config
import utils
from deepseek_client import chat_complete
from prompts import CLEAN_SYSTEM, build_clean_user


def list_raw():
    return sorted(config.RAW_DIR.glob("*.txt"))


def clean_path_for(lecture_id: str):
    return config.CLEAN_DIR / f"{config.COURSE_NAME}_{lecture_id}.md"


def clean_one(raw_path, force=False, dry_run=False):
    lid = utils.lecture_id_from_path(raw_path)
    out_path = clean_path_for(lid)
    if out_path.exists() and not force:
        print(f"  跳过(已存在): {out_path.name}")
        return out_path

    lines = utils.collapse_consecutive(utils.read_lines(raw_path))
    chunks = utils.chunk_lines(lines, config.CLEAN_CHUNK_CHARS)
    raw_chars = sum(len(ln) for ln in lines)

    if dry_run:
        print(f"  {raw_path.name}: 去连续重复后 {len(lines)} 行 / {raw_chars} 字 -> {len(chunks)} 个 API 块")
        return None

    cleaned = []
    for i, ch in enumerate(chunks, 1):
        print(f"    清洗块 {i}/{len(chunks)} ...")
        txt = chat_complete(
            CLEAN_SYSTEM,
            build_clean_user("\n".join(ch)),
            temperature=config.TEMP_CLEAN,
            tag=f"clean:{lid}:{i}",
        )
        cleaned.append(txt.strip())

    body = "\n\n".join(p for p in cleaned if p)
    header = (
        f"# {config.COURSE_NAME} 课程整理稿（{lid}）\n\n"
        "> 本文由 Whisper 自动转写稿整理为通顺段落，仅做去重、断句、分段与必要标点，"
        "未增删授课的实质内容。\n\n"
    )
    utils.write_text(out_path, header + body + "\n")

    if len(body) < 0.35 * raw_chars:
        print(f"  [warn] {out_path.name} 清洗后字数明显偏少（{len(body)}/{raw_chars}），可能过度删减，请抽查。")
    print(f"  完成: {out_path.name}（{len(body)} 字）")
    return out_path


def run(force=False, dry_run=False):
    config.ensure_dirs()
    raws = list_raw()
    print(f"=== 阶段一：清洗 {len(raws)} 个转写文件 ===")
    for r in raws:
        print(f"- {r.name}")
        clean_one(r, force=force, dry_run=dry_run)
    if not dry_run:
        print("阶段一完成。")


def is_complete() -> bool:
    raws = list_raw()
    if not raws:
        return False
    return all(clean_path_for(utils.lecture_id_from_path(r)).exists() for r in raws)


def status():
    raws = list_raw()
    done = sum(1 for r in raws if clean_path_for(utils.lecture_id_from_path(r)).exists())
    return done, len(raws)
