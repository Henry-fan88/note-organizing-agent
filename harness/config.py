# -*- coding: utf-8 -*-
"""全局配置：路径、模型、分块、温度等。所有取值都可用环境变量覆盖。"""
import os
from pathlib import Path

# 可选加载 .env（项目根目录或 harness/ 下都会尝试）
try:
    from dotenv import load_dotenv  # type: ignore
    _ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(_ROOT / ".env")
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent

# ============ DeepSeek API ============
API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# 注意：请按 DeepSeek 官方文档把 DEEPSEEK_MODEL 设为你要用的“V4 Pro”模型 ID。
# deepseek-chat 是指向最新通用对话模型的别名，可作默认值兜底。
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
REQUEST_TIMEOUT = int(os.getenv("DEEPSEEK_TIMEOUT", "600"))
MAX_RETRIES = int(os.getenv("DEEPSEEK_MAX_RETRIES", "5"))
MAX_TOKENS = int(os.getenv("DEEPSEEK_MAX_TOKENS", "8192"))

# ============ 路径 ============
RAW_DIR = ROOT / "raw-transcripts"
CLEAN_DIR = ROOT / "clean-transcripts"
WIKI_DIR = ROOT / "wiki"
NOTES_DIR = WIKI_DIR / "notes"
CONCEPTS_DIR = WIKI_DIR / "concepts"
INDEX_DIR = WIKI_DIR / "index"
BUILD_DIR = WIKI_DIR / "_build"
STATE_DIR = HARNESS / "state"
LOG_DIR = HARNESS / "logs"

# ============ 分块 ============
# 第一阶段清洗时每个 API 块的目标字符数（按行边界切，不切断句子内部）
CLEAN_CHUNK_CHARS = int(os.getenv("CLEAN_CHUNK_CHARS", "3500"))

# ============ 采样温度（越低越忠实）============
TEMP_CLEAN = float(os.getenv("TEMP_CLEAN", "0.2"))     # 清洗：高度忠实
TEMP_NOTES = float(os.getenv("TEMP_NOTES", "0.4"))     # 笔记：允许适度解读
TEMP_CONCEPTS = float(os.getenv("TEMP_CONCEPTS", "0.35"))
TEMP_INDEX = float(os.getenv("TEMP_INDEX", "0.3"))

# ============ 课程信息 ============
COURSE_NAME = os.getenv("COURSE_NAME", "中国历史地理")


def ensure_dirs():
    for d in (CLEAN_DIR, NOTES_DIR, CONCEPTS_DIR, INDEX_DIR, BUILD_DIR, STATE_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
