"""
AI 服务模块

意图路由：
    根据 RAG top_score 将用户消息分为三类，选择对应提示词：
    - CHAT  (score < INTENT_LOW_THRESHOLD)  ：闲聊，LLM 亲和回复，不返回产品图片
    - VAGUE (score < INTENT_HIGH_THRESHOLD) ：问题模糊，追问用户细节，不返回产品图片
    - CLEAR (score ≥ INTENT_HIGH_THRESHOLD) ：明确产品问题，注入知识库上下文回答

AI 提供商：
    火山方舟 Ark（doubao-seed-2-0-lite-260215），兼容 OpenAI Chat Completions 格式。
    通过 .env 中 AI_BASE_URL / AI_MODEL / AI_API_KEY 切换提供商，无需改代码。

其他功能：
    - 每用户独立对话历史（内存，重启清空），最多保留 MAX_HISTORY_TURNS 轮
    - AI 调用失败自动重试一次；CLEAR 模式下重试失败时用知识库第一条答案兜底
"""

import asyncio
import logging
from collections import defaultdict, deque

import httpx

from config import (
    AI_API_KEY,
    AI_BASE_URL,
    AI_MODEL,
    SYSTEM_PROMPT,
    MAX_HISTORY_TURNS,
    HUMAN_TAKEOVER_KEYWORDS,
    RAG_ENABLED,
    INTENT_LOW_THRESHOLD,
    INTENT_HIGH_THRESHOLD,
    CHAT_SYSTEM_PROMPT,
    VAGUE_SYSTEM_PROMPT,
    CLEAR_SYSTEM_PROMPT,
)
from rag_service import retrieve

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
# 每个用户的对话历史（内存存储，重启清空）
# key: openid, value: deque of {"role": ..., "content": ...}
# ──────────────────────────────────────────
_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY_TURNS * 2))


def needs_human(text: str) -> bool:
    """检测用户是否请求转人工"""
    return any(kw in text for kw in HUMAN_TAKEOVER_KEYWORDS)


def add_to_history(openid: str, role: str, content: str) -> None:
    _history[openid].append({"role": role, "content": content})


def get_history(openid: str) -> list:
    return list(_history[openid])


def clear_history(openid: str) -> None:
    _history[openid].clear()


# ──────────────────────────────────────────
# 调用 AI 生成回复
# ──────────────────────────────────────────

def _classify_intent(top_score: float) -> str:
    """根据 RAG top_score 判断用户意图类型"""
    if top_score < INTENT_LOW_THRESHOLD:
        return "CHAT"
    elif top_score < INTENT_HIGH_THRESHOLD:
        return "VAGUE"
    else:
        return "CLEAR"


async def get_ai_reply(openid: str, user_message: str) -> tuple[str, list[str]]:
    """
    调用 AI 接口获取回复。
    返回：
      - reply_text: AI 生成的文字回复
      - image_urls: 知识库命中条目中附带的图片链接（可为空列表）
    """
    add_to_history(openid, "user", user_message)

    # RAG：检索知识库并路由意图
    image_urls: list[str] = []
    context = ""
    if RAG_ENABLED:
        context, image_urls, top_score = retrieve(user_message)
        intent = _classify_intent(top_score)
        logger.info(f"[Intent] top_score={top_score:.1f} → {intent}")

        if intent == "CLEAR":
            system = CLEAR_SYSTEM_PROMPT.format(context=context)
        elif intent == "VAGUE":
            system = VAGUE_SYSTEM_PROMPT
            image_urls = []
            context = ""  # 非CLEAR意图时清空context，防止兜底逻辑误用知识库
        else:  # CHAT
            system = CHAT_SYSTEM_PROMPT
            image_urls = []
            context = ""  # 非CLEAR意图时清空context，防止兜底逻辑误用知识库
    else:
        system = SYSTEM_PROMPT

    messages = [
        {"role": "system", "content": system},
        *get_history(openid),
    ]

    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0.7,
    }

    reply = ""
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(base_url=AI_BASE_URL, timeout=60) as client:
                resp = await client.post("/chat/completions", json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            reply = data["choices"][0]["message"]["content"].strip()
            break
        except Exception as e:
            if attempt == 0:
                logger.warning(f"[AI] 第1次失败，1s后重试: {type(e).__name__}: {e}")
                await asyncio.sleep(1)
            else:
                logger.error(f"[AI] 调用失败(已重试): {type(e).__name__}: {e}")
                # 有知识库命中时，直接用第一条答案兜底，同时保留图片
                if context:
                    first_entry = context.split("\n\n---\n\n")[0]
                    reply = first_entry.split("答：", 1)[1].strip() if "答：" in first_entry else context
                    logger.info("[AI] 使用知识库答案兜底")
                else:
                    reply = "抱歉，我暂时无法回复，请稍后再试或联系人工客服。"
                    image_urls = []

    add_to_history(openid, "assistant", reply)
    return reply, image_urls
