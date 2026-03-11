"""
RAG（检索增强生成）模块

功能：
    从 knowledge_base.json 加载 Q&A 知识库，对用户问题进行关键词+字符重叠评分，
    返回最相关的 top-K 条目供 AI 参考回答。

评分策略（_score）：
    1. 关键词命中：每命中一个 keywords 条目 +2 分（权重最高）
    2. question 字段 CJK bi-gram 重叠：每个共同双字 +0.8 分（避免共享前缀导致同分）

retrieve() 返回值：
    (context_str, image_urls, top_score)
    - context_str : 注入提示词的参考文本
    - image_urls  : 命中条目的图片链接列表（可为空）
    - top_score   : 最高相关性分数，供 ai_service 意图路由使用

知识库格式（knowledge_base.json）：
[
  {
    "question": "如何申请退款？",
    "answer":   "支持7天无理由退款，请在订单页面点击申请退款...",
    "keywords": ["退款", "退货", "退钱", "不想要", "七天"],
    "image_url": ""
  },
  ...
]
"""

import json
import logging
from pathlib import Path

from config import RAG_TOP_K, RAG_MIN_SCORE

logger = logging.getLogger(__name__)

KB_PATH = Path(__file__).parent / "knowledge_base.json"

# ──────────────────────────────────────────
# 加载知识库
# ──────────────────────────────────────────

def load_knowledge_base() -> list[dict]:
    if not KB_PATH.exists():
        logger.warning(f"知识库文件不存在：{KB_PATH}")
        return []
    try:
        with open(KB_PATH, "r", encoding="utf-8") as f:
            kb = json.load(f)
        logger.info(f"知识库加载成功，共 {len(kb)} 条")
        return kb
    except Exception as e:
        logger.error(f"知识库加载失败：{e}")
        return []


# 启动时加载一次，避免每次请求都读文件
_kb_cache: list[dict] = []

def get_kb() -> list[dict]:
    global _kb_cache
    if not _kb_cache:
        _kb_cache = load_knowledge_base()
    return _kb_cache

def reload_kb() -> None:
    """手动重新加载知识库（更新知识库后调用）"""
    global _kb_cache
    _kb_cache = load_knowledge_base()


# ──────────────────────────────────────────
# 相关性评分（关键词匹配 + 汉字字符重叠）
# ──────────────────────────────────────────

def _cjk_bigrams(text: str) -> set:
    """提取文本中连续两个汉字组成的 bi-gram 集合"""
    return {
        text[i:i+2]
        for i in range(len(text) - 1)
        if all("\u4e00" <= c <= "\u9fff" for c in text[i:i+2])
    }


def _score(query: str, entry: dict) -> float:
    """
    计算用户问题与知识库条目的相关性分数。
    策略：
    1. 关键词命中：用户问题包含关键词，每命中一个 +2 分
    2. question 字段 CJK bi-gram 重叠：每个共同 2-gram +0.8 分
       （比单字符重叠更能区分语义，避免共享前缀的条目得分相同）
    """
    score = 0.0

    # 1. 关键词匹配（权重最高）
    for kw in entry.get("keywords", []):
        if kw and kw in query:
            score += 2.0

    # 2. question 字段 CJK bi-gram 重叠
    question = entry.get("question", "")
    query_bigrams = _cjk_bigrams(query)
    q_bigrams = _cjk_bigrams(question)
    score += len(query_bigrams & q_bigrams) * 0.8

    return score


# ──────────────────────────────────────────
# 检索入口
# ──────────────────────────────────────────

def retrieve(query: str) -> tuple[str, list[str], float]:
    """
    根据用户问题检索知识库。
    返回：
      - context_text: 注入 AI 提示词的参考内容（str）
      - image_urls:   命中条目中的图片链接列表（list[str]），可为空
      - top_score:    最高相关性分数（float），用于意图路由；无命中时为 0.0
    """
    kb = get_kb()
    if not kb:
        return "", [], 0.0

    scored = [
        (entry, _score(query, entry))
        for entry in kb
    ]
    scored.sort(key=lambda x: x[1], reverse=True)

    top_score = scored[0][1] if scored else 0.0

    scored = [(e, s) for e, s in scored if s >= RAG_MIN_SCORE]
    top = scored[:RAG_TOP_K]
    if not top:
        return "", [], top_score

    lines = []
    image_urls = []
    for entry, score in top:
        lines.append(f"问：{entry['question']}\n答：{entry['answer']}")
        logger.info(f"[RAG] 命中 score={score:.1f} | {entry['question'][:20]}")

    # 只取 top-1 条目的图片（最相关条目），支持多图
    # image_urls 字段（列表）优先；兼容旧 image_url（字符串）
    top_entry = top[0][0]
    urls = top_entry.get("image_urls") or []
    if not urls:
        single = top_entry.get("image_url", "").strip()
        if single:
            urls = [single]
    image_urls.extend(urls)

    return "\n\n---\n\n".join(lines), image_urls, top_score
