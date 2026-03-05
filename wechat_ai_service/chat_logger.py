"""
对话日志模块 — 将所有对话（AI模式 + 人工模式）写入本地 JSON 文件

文件路径：{LOG_DIR}/{openid}.json
JSON 格式：
{
  "openid": "oXxx...",
  "sessions": [
    {
      "session_id": "ai_1700000000",
      "start_ts": 1700000000,
      "end_ts": null,
      "log": [
        {"role": "user",  "text": "你好",             "ts": 1700000001},
        {"role": "ai",    "text": "您好，有什么帮您？", "ts": 1700000002},
        {"role": "agent", "text": "我是人工客服",       "ts": 1700000100}
      ]
    }
  ]
}
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from config import LOG_DIR

logger = logging.getLogger(__name__)

_log_dir = Path(LOG_DIR)
# 每个 openid 一把 asyncio.Lock，防止并发读写同一文件
_file_locks: dict[str, asyncio.Lock] = {}


def _get_lock(openid: str) -> asyncio.Lock:
    if openid not in _file_locks:
        _file_locks[openid] = asyncio.Lock()
    return _file_locks[openid]


def _log_path(openid: str) -> Path:
    return _log_dir / f"{openid}.json"


def _load_sync(openid: str) -> dict:
    """同步读取用户日志，不存在则返回初始结构"""
    try:
        with open(_log_path(openid), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"openid": openid, "sessions": []}


def _save_sync(openid: str, data: dict) -> None:
    """原子写入：先写 .tmp 再 rename，防止写入中途崩溃导致文件损坏"""
    _log_dir.mkdir(parents=True, exist_ok=True)
    path = _log_path(openid)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ─── 公共异步 API ──────────────────────────────────────────────────────────────

async def append_log(
    openid: str,
    role: str,
    text: str,
    ts: float,
    session_id: str,
    image_url: str = "",
    msg_type: str = "text",
) -> None:
    """
    追加一条记录到本地 JSON 日志（始终生效，无需配置开关）。
    role: 'user' | 'ai' | 'agent'
    """
    loop = asyncio.get_running_loop()
    async with _get_lock(openid):
        try:
            data = await loop.run_in_executor(None, _load_sync, openid)
            sessions = data["sessions"]
            session = next((s for s in sessions if s["session_id"] == session_id), None)
            if session is None:
                session = {
                    "session_id": session_id,
                    "start_ts": ts,
                    "end_ts": None,
                    "log": [],
                }
                sessions.append(session)
            entry: dict = {"role": role, "text": text, "ts": ts, "msg_type": msg_type}
            if image_url:
                entry["image_url"] = image_url
            session["log"].append(entry)
            await loop.run_in_executor(None, _save_sync, openid, data)
        except Exception as e:
            logger.error(f"[chat_logger] append_log 失败 openid={openid[:8]}: {e}")


async def end_session(openid: str, session_id: str, end_ts: float | None = None) -> None:
    """标记会话结束时间"""
    if end_ts is None:
        end_ts = time.time()
    loop = asyncio.get_running_loop()
    async with _get_lock(openid):
        try:
            data = await loop.run_in_executor(None, _load_sync, openid)
            session = next(
                (s for s in data["sessions"] if s["session_id"] == session_id), None
            )
            if session is not None:
                session["end_ts"] = end_ts
                await loop.run_in_executor(None, _save_sync, openid, data)
        except Exception as e:
            logger.error(f"[chat_logger] end_session 失败 openid={openid[:8]}: {e}")


async def get_user_log(openid: str) -> dict:
    """获取用户全量聊天记录（所有 session，含 AI 和人工）"""
    loop = asyncio.get_running_loop()
    async with _get_lock(openid):
        try:
            return await loop.run_in_executor(None, _load_sync, openid)
        except Exception as e:
            logger.error(f"[chat_logger] get_user_log 失败 openid={openid[:8]}: {e}")
            return {"openid": openid, "sessions": []}
