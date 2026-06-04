# -*- coding: utf-8 -*-
"""统一入口 / 编排器。

用法：
  python harness/run.py status            查看各阶段进度
  python harness/run.py dryrun            不调用 API，预估清洗分块数
  python harness/run.py clean   [--force] 阶段一：清洗转写
  python harness/run.py notes   [--force] 阶段二·笔记
  python harness/run.py concepts[--force] 阶段二·概念
  python harness/run.py index             阶段二·索引（始终重建）
  python harness/run.py pdf [--backend auto|pandoc|chrome|weasyprint]
  python harness/run.py all     [--force] 顺序跑完全流程（带阶段门禁）

设计原则：每个阶段都有明确的“完成判据”，编排器在前一阶段未完成时不会进入下一阶段，
确保达成阶段目标后才推进/停止，避免半成品流入后续步骤。
"""
import argparse
import sys

import config
import phase1_clean as p1
import phase2_concepts as p2c
import phase2_index as p2i
import phase2_notes as p2n
import build_pdf


def cmd_status(_args):
    c_done, c_total = p1.status()
    n_done, n_total = p2n.status()
    cc_done, cc_total, cc_reg = p2c.status()
    idx_n = p2i.status()
    print("==== 进度总览 ====")
    print(f"课程：{config.COURSE_NAME}    模型：{config.MODEL}")
    print(f"API Key：{'已设置' if config.API_KEY else '未设置 ❌'}")
    print(f"阶段一 清洗转写 : {c_done}/{c_total}  {'✅' if p1.is_complete() else '…'}")
    print(f"阶段二 课堂笔记 : {n_done}/{n_total}  {'✅' if p2n.is_complete() else '…'}")
    print(f"阶段二 概念词条 : {cc_done}/{cc_total} 节已处理, 共 {cc_reg} 条  {'✅' if p2c.is_complete() else '…'}")
    print(f"阶段二 主题索引 : {idx_n} 个文件  {'✅' if p2i.is_complete() else '…'}")


def cmd_dryrun(_args):
    p1.run(dry_run=True)
    print("\n（dryrun 仅预估清洗分块，不调用 API；其余阶段按文件逐节/逐概念调用。）")


def cmd_clean(args):
    p1.run(force=args.force, only=getattr(args, "only", None))


def cmd_notes(args):
    if not p1.is_complete():
        c, t = p1.status()
        print(f"⛔ 阶段一未完成（{c}/{t}）。请先跑 clean。")
        sys.exit(2)
    p2n.run(force=args.force)


def cmd_concepts(args):
    if not p1.is_complete():
        c, t = p1.status()
        print(f"⛔ 阶段一未完成（{c}/{t}）。请先跑 clean。")
        sys.exit(2)
    p2c.run(force=args.force)


def cmd_index(_args):
    if not (p2n.is_complete() and p2c.is_complete()):
        print("⛔ 笔记或概念尚未全部完成，索引需要在两者就绪后再建。")
        sys.exit(2)
    p2i.run()


def cmd_pdf(args):
    build_pdf.run(backend=args.backend)


def cmd_all(args):
    # 阶段一
    p1.run(force=args.force)
    if not p1.is_complete():
        print("⛔ 阶段一未全部完成，停止。")
        sys.exit(2)
    print(">>> 阶段一已确认全部完成，进入阶段二。\n")
    # 阶段二：笔记 + 概念
    p2n.run(force=args.force)
    p2c.run(force=args.force)
    if not (p2n.is_complete() and p2c.is_complete()):
        print("⛔ 笔记或概念未全部完成，停止（不进入索引）。")
        sys.exit(2)
    print(">>> 笔记与概念已确认完成，构建索引。\n")
    # 阶段二：索引
    p2i.run()
    print(">>> 索引完成，导出 PDF。\n")
    build_pdf.run(backend=args.backend)
    print("\n🎉 全流程结束。")
    cmd_status(args)


def main():
    ap = argparse.ArgumentParser(description="笔记整理 harness（DeepSeek）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("dryrun").set_defaults(func=cmd_dryrun)
    pc = sub.add_parser("clean")
    pc.add_argument("--force", action="store_true", help="忽略已有产物，强制重做")
    pc.add_argument("--only", default=None, help="仅处理文件名包含该字符串的转写，如 2026-03-02")
    pc.set_defaults(func=cmd_clean)
    for name, fn in (("notes", cmd_notes), ("concepts", cmd_concepts)):
        p = sub.add_parser(name)
        p.add_argument("--force", action="store_true", help="忽略已有产物，强制重做")
        p.set_defaults(func=fn)
    sub.add_parser("index").set_defaults(func=cmd_index)
    pp = sub.add_parser("pdf")
    pp.add_argument("--backend", default="auto", choices=["auto", "pandoc", "chrome", "weasyprint"])
    pp.set_defaults(func=cmd_pdf)
    pa = sub.add_parser("all")
    pa.add_argument("--force", action="store_true")
    pa.add_argument("--backend", default="auto", choices=["auto", "pandoc", "chrome", "weasyprint"])
    pa.set_defaults(func=cmd_all)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
