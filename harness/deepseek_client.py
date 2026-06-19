# -*- coding: utf-8 -*-
"""DeepSeek API 封装（OpenAI 兼容协议）。

设计要点：
- 每次调用都是“无状态、按任务自包含”的——我们不维护一个会不断膨胀的长对话，
  而是每个子任务（清洗某一块 / 写某一节笔记 / 合并某个概念）都用一组干净、范围明确的
  messages。需要的上下文（如已有概念清单、已有概念正文）由调用方显式塞进 prompt。
  这样可以避免“上下文窗口被无关历史撑爆 -> 记忆丢失 / 串台 / 重复 / 编造”的问题。
- 内置指数退避重试、超时、用量日志。
- chat_complete 支持在被 max_tokens 截断时自动“接着写”，用于较长的笔记/概念正文。
"""
import csv
import datetime
import time

import config

_client = None


class ContentRiskError(RuntimeError):
    """DeepSeek 输入内容审核拦截（400 Content Exists Risk）。

    这是确定性的、不可重试的错误：同一段文本每次都会被拒。调用方应据此降级
    （如把块拆细重试 / 保留原文），而不是当成临时故障去重试。
    """


def _is_content_risk(e) -> bool:
    """判断异常是否为内容审核类 400（重试无意义）。"""
    msg = str(e)
    low = msg.lower()
    if "content exists risk" in low:
        return True
    status = getattr(e, "status_code", None)
    return status == 400 and ("risk" in low or "审核" in msg)


def _get_client():
    global _client
    if _client is None:
        if not config.API_KEY:
            raise RuntimeError(
                "缺少 DEEPSEEK_API_KEY。请在项目根目录的 .env 里设置，"
                "或 export DEEPSEEK_API_KEY=sk-xxx"
            )
        # 延迟导入，未装 openai 时也能跑 status / dryrun
        from openai import OpenAI  # type: ignore
        _client = OpenAI(
            api_key=config.API_KEY,
            base_url=config.BASE_URL,
            timeout=config.REQUEST_TIMEOUT,
        )
    return _client


def _log_usage(tag, usage):
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        f = config.LOG_DIR / "usage.csv"
        new = not f.exists()
        with open(f, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["time", "tag", "prompt_tokens", "completion_tokens", "total_tokens"])
            w.writerow([
                datetime.datetime.now().isoformat(timespec="seconds"),
                tag,
                getattr(usage, "prompt_tokens", ""),
                getattr(usage, "completion_tokens", ""),
                getattr(usage, "total_tokens", ""),
            ])
    except Exception:
        pass


def chat(messages, temperature=0.3, max_tokens=None, json_mode=False, tag="chat"):
    """单次调用，返回 (内容文本, finish_reason)。带重试。"""
    if not config.API_KEY:
        raise RuntimeError(
            "缺少 DEEPSEEK_API_KEY。请先 cp .env.example .env 并填入密钥，"
            "或 export DEEPSEEK_API_KEY=sk-xxx 后再运行。"
        )
    max_tokens = max_tokens or config.MAX_TOKENS
    kwargs = dict(
        model=config.MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_err = None
    for attempt in range(config.MAX_RETRIES):
        try:
            resp = _get_client().chat.completions.create(**kwargs)
            if getattr(resp, "usage", None):
                _log_usage(tag, resp.usage)
            choice = resp.choices[0]
            return (choice.message.content or ""), choice.finish_reason
        except Exception as e:  # noqa: BLE001
            # 内容审核拦截是确定性错误，重试只会反复失败、白白浪费退避时间——立即抛出专用异常。
            if _is_content_risk(e):
                print(f"    [risk] 内容审核拦截 tag={tag}: {e} — 不重试，交由上层降级处理")
                raise ContentRiskError(str(e)) from e
            last_err = e
            wait = min(60, 2 ** attempt)
            print(f"    [warn] API 失败 ({attempt + 1}/{config.MAX_RETRIES}) tag={tag}: {e} — {wait}s 后重试")
            time.sleep(wait)
    raise RuntimeError(f"API 调用多次失败 (tag={tag}): {last_err}")


def chat_complete(system, user, temperature=0.3, max_tokens=None, tag="chat", max_continue=4):
    """单轮提问 + 截断自动续写，返回完整文本。"""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    out, reason = chat(messages, temperature, max_tokens, tag=tag)
    full = out
    cont = 0
    while reason == "length" and cont < max_continue:
        messages.append({"role": "assistant", "content": out})
        messages.append({
            "role": "user",
            "content": "请从上次中断处继续输出，保持原有格式与编号连贯，"
                       "不要重复已经写过的内容，也不要重新开头或加任何说明。",
        })
        out, reason = chat(messages, temperature, max_tokens, tag=tag + "-cont")
        full += out
        cont += 1
    return full


def complete_split_on_risk(system, build_user, lines, on_blocked,
                           temperature=0.3, tag="chat", max_depth=5, join="\n\n"):
    """对一段文本调用 chat_complete；若被内容审核拦截（ContentRiskError），
    按行二分递归重试，各段结果用 join 拼接；到最小粒度仍被拒时调用 on_blocked(text) 兜底。

    适用于“整段输入里只有一小部分触发审核”的场景（清洗/笔记/概念抽取都是）：
    拆细后绝大部分仍能正常处理，不丢内容、不中断流程。
    - build_user(text): 把一段文本拼成完整的 user prompt。
    - on_blocked(text): 该段到最小粒度仍被拒时的兜底返回（如保留原文/占位）。
    """
    def _go(seg_lines, depth, sub_tag):
        text = "\n".join(seg_lines)
        try:
            return chat_complete(system, build_user(text), temperature=temperature, tag=sub_tag).strip()
        except ContentRiskError:
            if len(seg_lines) > 1 and depth < max_depth:
                mid = len(seg_lines) // 2
                print(f"    [risk] {sub_tag} 被拦截，拆成 {mid}+{len(seg_lines) - mid} 行分别重试 ...")
                left = _go(seg_lines[:mid], depth + 1, sub_tag + "a")
                right = _go(seg_lines[mid:], depth + 1, sub_tag + "b")
                return (left + join + right).strip()
            print(f"    [risk] {sub_tag} 最小粒度仍被拦截，触发兜底。")
            return on_blocked(text)

    return _go(list(lines), 0, tag)
