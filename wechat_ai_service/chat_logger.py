"""
对话日志模块 — 将所有对话（AI模式 + 人工模式）写入本地 JSON 文件

文件路径：{LOG_DIR}/{app_id}/{openid}.json
JSON 格式：
{
  "openid": "oXxx...",
  "app_id": "wx111",
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

日志按 app_id 子目录组织，支持多小程序数据隔离：
  {LOG_DIR}/app_a/{openid}.json
  {LOG_DIR}/app_b/{openid}.json
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from config import LOG_DIR

logger = logging.getLogger(__name__)

_log_dir = Path(LOG_DIR)
# 每个 uid（f"{app_id}:{openid}"）一把 asyncio.Lock，防止并发读写同一文件
_file_locks: dict[str, asyncio.Lock] = {}


def _get_lock(app_id: str, openid: str) -> asyncio.Lock:
    uid = f"{app_id}:{openid}"
    if uid not in _file_locks:
        _file_locks[uid] = asyncio.Lock()
    return _file_locks[uid]


def _log_path(app_id: str, openid: str) -> Path:
    return _log_dir / app_id / f"{openid}.json"


def _load_sync(app_id: str, openid: str) -> dict:
    """同步读取用户日志，不存在则返回初始结构"""
    try:
        with open(_log_path(app_id, openid), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"openid": openid, "app_id": app_id, "nickname": "", "sessions": []}


def _save_sync(app_id: str, openid: str, data: dict) -> None:
    """原子写入：先写 .tmp 再 rename，防止写入中途崩溃导致文件损坏"""
    app_dir = _log_dir / app_id
    app_dir.mkdir(parents=True, exist_ok=True)
    path = _log_path(app_id, openid)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ─── 公共异步 API ──────────────────────────────────────────────────────────────

async def append_log(
    app_id: str,
    openid: str,
    role: str,
    text: str,
    ts: float,
    session_id: str,
    image_url: str = "",
    msg_type: str = "text",
    agent_name: str = "",
    agent_online: bool | None = None,
) -> None:
    """
    追加一条记录到本地 JSON 日志（始终生效，无需配置开关）。
    role: 'user' | 'ai' | 'agent'
    agent_name: 回复的客服名称（仅 role='agent' 时有意义）
    agent_online: 首次回复时客服是否在线（仅首条 agent 消息记录，用于历史统计过滤）
    """
    loop = asyncio.get_running_loop()
    async with _get_lock(app_id, openid):
        try:
            data = await loop.run_in_executor(None, _load_sync, app_id, openid)
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
            if agent_name and role == "agent":
                entry["agent_name"] = agent_name
            if agent_online is not None and role == "agent":
                entry["agent_online"] = agent_online
            session["log"].append(entry)
            await loop.run_in_executor(None, _save_sync, app_id, openid, data)
        except Exception as e:
            logger.error(f"[chat_logger] append_log 失败 openid={openid[:8]}: {e}")


async def append_timeout_marker(
    app_id: str,
    openid: str,
    session_id: str,
    online_agents: list[str],
) -> None:
    """
    3分钟超时转回AI时，向会话日志追加超时标记。
    记录当时在线的客服列表，供 compute_stats_for_range() 重算 missed_count。
    """
    loop = asyncio.get_running_loop()
    async with _get_lock(app_id, openid):
        try:
            data = await loop.run_in_executor(None, _load_sync, app_id, openid)
            session = next(
                (s for s in data["sessions"] if s["session_id"] == session_id), None
            )
            if session is not None:
                entry = {"role": "timeout", "ts": time.time(), "online_agents": online_agents}
                session["log"].append(entry)
                await loop.run_in_executor(None, _save_sync, app_id, openid, data)
        except Exception as e:
            logger.error(f"[chat_logger] append_timeout_marker 失败 openid={openid[:8]}: {e}")


async def end_session(app_id: str, openid: str, session_id: str, end_ts: float | None = None) -> None:
    """标记会话结束时间"""
    if end_ts is None:
        end_ts = time.time()
    loop = asyncio.get_running_loop()
    async with _get_lock(app_id, openid):
        try:
            data = await loop.run_in_executor(None, _load_sync, app_id, openid)
            session = next(
                (s for s in data["sessions"] if s["session_id"] == session_id), None
            )
            if session is not None:
                session["end_ts"] = end_ts
                await loop.run_in_executor(None, _save_sync, app_id, openid, data)
        except Exception as e:
            logger.error(f"[chat_logger] end_session 失败 openid={openid[:8]}: {e}")


async def get_user_log(app_id: str, openid: str) -> dict:
    """获取用户全量聊天记录（所有 session，含 AI 和人工）"""
    loop = asyncio.get_running_loop()
    async with _get_lock(app_id, openid):
        try:
            return await loop.run_in_executor(None, _load_sync, app_id, openid)
        except Exception as e:
            logger.error(f"[chat_logger] get_user_log 失败 openid={openid[:8]}: {e}")
            return {"openid": openid, "app_id": app_id, "nickname": "", "sessions": []}


async def update_nickname(app_id: str, openid: str, nickname: str) -> None:
    """更新用户昵称到日志文件"""
    loop = asyncio.get_running_loop()
    async with _get_lock(app_id, openid):
        try:
            data = await loop.run_in_executor(None, _load_sync, app_id, openid)
            new_data = {**data, "nickname": nickname}
            await loop.run_in_executor(None, _save_sync, app_id, openid, new_data)
        except Exception as e:
            logger.error(f"[chat_logger] update_nickname 失败 openid={openid[:8]}: {e}")


def _list_all_users_sync() -> list:
    """扫描 LOG_DIR 下所有 app_id 子目录中的 .json 文件，返回用户基础信息列表"""
    if not _log_dir.exists():
        return []
    users = []
    # 遍历 app_id 子目录
    for app_dir in _log_dir.iterdir():
        if not app_dir.is_dir():
            continue
        app_id = app_dir.name
        for path in app_dir.glob("*.json"):
            if path.suffix == ".tmp":
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                openid = data.get("openid", path.stem)
                nickname = data.get("nickname", "")
                sessions = data.get("sessions", [])
                msg_count = sum(len(s.get("log", [])) for s in sessions)
                last_ts = 0.0
                for s in sessions:
                    for m in s.get("log", []):
                        if m.get("ts", 0) > last_ts:
                            last_ts = m["ts"]
                users.append({
                    "openid": openid,
                    "app_id": app_id,
                    "nickname": nickname,
                    "last_ts": last_ts,
                    "msg_count": msg_count,
                })
            except Exception:
                continue
    return users


async def list_all_users() -> list:
    """获取所有用户基础信息（异步版本）"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _list_all_users_sync)


def _search_logs_sync(
    q: str,
    date_from_ts: float,
    date_to_ts: float,
    allowed_openids: set | None,
) -> list:
    """
    同步搜索所有日志文件。
    - q: 关键词（空则不过滤内容）
    - date_from_ts/date_to_ts: 时间戳范围过滤（按 session start_ts）
    - allowed_openids: 允许的 openid 集合（None 表示不限制）
    返回匹配的 session 摘要列表。
    """
    if not _log_dir.exists():
        return []
    results = []
    for app_dir in _log_dir.iterdir():
        if not app_dir.is_dir():
            continue
        app_id = app_dir.name
        for path in app_dir.glob("*.json"):
            if path.suffix == ".tmp":
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                openid = data.get("openid", path.stem)
                if allowed_openids is not None and openid not in allowed_openids:
                    continue
                nickname = data.get("nickname", "")
                for session in data.get("sessions", []):
                    start_ts = session.get("start_ts", 0)
                    if start_ts < date_from_ts or start_ts > date_to_ts:
                        continue
                    # 关键词过滤：检查 session 中任意消息文本
                    if q:
                        texts = [m.get("text", "") for m in session.get("log", [])]
                        if not any(q in t for t in texts):
                            continue
                    # 取第一条/最后一条消息预览
                    log = session.get("log", [])
                    first_text = log[0].get("text", "") if log else ""
                    results.append({
                        "openid": openid,
                        "app_id": app_id,
                        "nickname": nickname,
                        "session_id": session.get("session_id", ""),
                        "start_ts": start_ts,
                        "end_ts": session.get("end_ts"),
                        "preview": first_text[:80],
                        "msg_count": len(log),
                    })
            except Exception:
                continue
    # 按时间倒序
    results.sort(key=lambda r: r["start_ts"], reverse=True)
    return results


async def search_logs(
    q: str = "",
    date_from_ts: float = 0.0,
    date_to_ts: float = float("inf"),
    allowed_openids: set | None = None,
) -> list:
    """搜索聊天日志（异步版本）"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _search_logs_sync, q, date_from_ts, date_to_ts, allowed_openids
    )
