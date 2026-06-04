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
