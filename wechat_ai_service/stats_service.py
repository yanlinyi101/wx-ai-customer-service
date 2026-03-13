"""
统计服务 — 记录客服会话统计数据

stats.json 结构:
{
  "overall": {
    "total_sessions": 0,        # 总会话数
    "total_users": 0,            # 总咨询用户数（唯一 openid）
    "responded_users": 0,        # 有效应答用户数（至少一次 agent 回复，唯一 openid）
    "total_response_time": 0.0,  # 累计应答时长（秒）
    "responded_count": 0,        # 有应答记录的会话数（用于计算平均）
    "within_3min": 0,            # 3分钟内应答会话数
    "all_openids": [],           # 所有咨询过的 openid（去重用，不对外暴露）
    "responded_openids": []      # 收到过回复的 openid（去重用，不对外暴露）
  },
  "agents": {
    "客服1": {
      "sessions": 0,
      "openids": [],             # 接待过的 openid（去重用）
      "user_messages": 0,
      "agent_messages": 0,
      "total_response_time": 0.0,
      "responded_count": 0
    }
  }
}
"""

import json
import logging
import pathlib
import threading

logger = logging.getLogger(__name__)

STATS_FILE = pathlib.Path(__file__).parent / "stats.json"
_lock = threading.Lock()


def _default_stats() -> dict:
    return {
        "overall": {
            "total_sessions": 0,
            "total_users": 0,
            "responded_users": 0,
            "total_response_time": 0.0,
            "responded_count": 0,
            "within_3min": 0,
            "all_openids": [],
            "responded_openids": [],
        },
        "agents": {},
    }


def _load_sync() -> dict:
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 向前兼容：旧版用 updated_openids，新版用 all_openids
            if "updated_openids" in data.get("overall", {}):
                data["overall"]["all_openids"] = data["overall"].pop("updated_openids")
            if "responded_openids" not in data.get("overall", {}):
                data["overall"]["responded_openids"] = []
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return _default_stats()


def _save_sync(data: dict) -> None:
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(STATS_FILE)


def record_session_close(
    agent_name: str,
    openid: str,
    user_msgs: int,
    agent_msgs: int,
    response_time_sec: float | None,
) -> None:
    """
    会话关闭时更新统计数据（线程安全）。
    agent_name 为空时不计入 agents 统计，但仍更新 overall。
    """
    with _lock:
        try:
            data = _load_sync()
            overall = data["overall"]
            agents = data["agents"]

            # overall 统计
            overall["total_sessions"] += 1

            if openid not in overall["all_openids"]:
                overall["all_openids"].append(openid)
                overall["total_users"] += 1

            if agent_msgs > 0 and openid not in overall["responded_openids"]:
                overall["responded_openids"].append(openid)
                overall["responded_users"] += 1

            if response_time_sec is not None:
                overall["total_response_time"] += response_time_sec
                overall["responded_count"] += 1
                if response_time_sec <= 180:
                    overall["within_3min"] += 1

            # agent 统计
            if agent_name:
                if agent_name not in agents:
                    agents[agent_name] = {
                        "sessions": 0,
                        "openids": [],
                        "user_messages": 0,
                        "agent_messages": 0,
                        "total_response_time": 0.0,
                        "responded_count": 0,
                    }
                ag = agents[agent_name]
                ag["sessions"] += 1
                if openid not in ag["openids"]:
                    ag["openids"].append(openid)
                ag["user_messages"] += user_msgs
                ag["agent_messages"] += agent_msgs
                if response_time_sec is not None:
                    ag["total_response_time"] += response_time_sec
                    ag["responded_count"] += 1

            _save_sync(data)
        except Exception as e:
            logger.error(f"[stats_service] record_session_close 失败: {e}")


def get_stats() -> dict:
    """读取并计算衍生指标（不暴露内部 openid 列表）"""
    with _lock:
        data = _load_sync()

    overall = data["overall"]
    total_sessions = overall.get("total_sessions", 0)
    total_users = overall.get("total_users", 0)
    responded_users = overall.get("responded_users", 0)
    responded_count = overall.get("responded_count", 0)
    total_response_time = overall.get("total_response_time", 0.0)
    within_3min = overall.get("within_3min", 0)

    avg_response_time = (total_response_time / responded_count) if responded_count > 0 else None
    effective_rate = (responded_users / total_users) if total_users > 0 else None
    within_3min_rate = (within_3min / total_sessions) if total_sessions > 0 else None

    agents_out = {}
    for name, ag in data.get("agents", {}).items():
        ag_responded_count = ag.get("responded_count", 0)
        ag_total_rt = ag.get("total_response_time", 0.0)
        agents_out[name] = {
            "sessions": ag.get("sessions", 0),
            "unique_users": len(ag.get("openids", [])),
            "user_messages": ag.get("user_messages", 0),
            "agent_messages": ag.get("agent_messages", 0),
            "avg_response_time": (ag_total_rt / ag_responded_count) if ag_responded_count > 0 else None,
        }

    return {
        "overall": {
            "total_sessions": total_sessions,
            "total_users": total_users,
            "responded_users": responded_users,
            "avg_response_time": avg_response_time,
            "effective_rate": effective_rate,
            "within_3min_rate": within_3min_rate,
        },
        "agents": agents_out,
    }


def get_agent_served_openids(agent_name: str) -> list:
    """Return list of openids this agent has served (from stats.json agents data)."""
    with _lock:
        data = _load_sync()
        return list(data.get("agents", {}).get(agent_name, {}).get("openids", []))


def compute_stats_for_range(log_dir: str, start_ts: float, end_ts: float) -> dict:
    """Scan all log files, include only human_ sessions with start_ts in [start_ts, end_ts]."""
    log_path = pathlib.Path(log_dir)
    if not log_path.exists():
        return {
            "overall": {
                "total_sessions": 0, "total_users": 0, "responded_users": 0,
                "avg_response_time": None, "effective_rate": None, "within_3min_rate": None,
            },
            "agents": {},
        }

    all_openids: list = []
    responded_openids: list = []
    total_sessions = 0
    total_response_time = 0.0
    responded_count = 0
    within_3min = 0
    agents: dict = {}

    for file in log_path.glob("*.json"):
        try:
            with open(file, "r", encoding="utf-8") as f:
                user_data = json.load(f)
        except Exception:
            continue

        openid = user_data.get("openid", file.stem)

        for session in user_data.get("sessions", []):
            if not session.get("session_id", "").startswith("human_"):
                continue
            s_start_ts = session.get("start_ts")
            if s_start_ts is None or not (start_ts <= s_start_ts <= end_ts):
                continue

            log = session.get("log", [])
            user_msgs = sum(1 for m in log if m.get("role") == "user")
            agent_msgs = sum(1 for m in log if m.get("role") == "agent")

            agent_name = ""
            for m in log:
                if m.get("role") == "agent" and m.get("agent_name"):
                    agent_name = m["agent_name"]
                    break

            response_time = None
            if s_start_ts:
                for m in log:
                    if m.get("role") == "agent":
                        response_time = m["ts"] - s_start_ts
                        break

            total_sessions += 1
            if openid not in all_openids:
                all_openids.append(openid)
            if agent_msgs > 0 and openid not in responded_openids:
                responded_openids.append(openid)
            if response_time is not None:
                total_response_time += response_time
                responded_count += 1
                if response_time <= 180:
                    within_3min += 1

            if agent_name:
                if agent_name not in agents:
                    agents[agent_name] = {
                        "sessions": 0, "openids": [], "user_messages": 0,
                        "agent_messages": 0, "total_response_time": 0.0, "responded_count": 0,
                    }
                ag = agents[agent_name]
                ag["sessions"] += 1
                if openid not in ag["openids"]:
                    ag["openids"].append(openid)
                ag["user_messages"] += user_msgs
                ag["agent_messages"] += agent_msgs
                if response_time is not None:
                    ag["total_response_time"] += response_time
                    ag["responded_count"] += 1

    total_users = len(all_openids)
    responded_users = len(responded_openids)
    avg_rt = (total_response_time / responded_count) if responded_count > 0 else None
    effective_rate = (responded_users / total_users) if total_users > 0 else None
    within_3min_rate = (within_3min / total_sessions) if total_sessions > 0 else None

    agents_out = {}
    for name, ag in agents.items():
        ag_rc = ag.get("responded_count", 0)
        ag_rt = ag.get("total_response_time", 0.0)
        agents_out[name] = {
            "sessions": ag.get("sessions", 0),
            "unique_users": len(ag.get("openids", [])),
            "user_messages": ag.get("user_messages", 0),
            "agent_messages": ag.get("agent_messages", 0),
            "avg_response_time": (ag_rt / ag_rc) if ag_rc > 0 else None,
        }

    return {
        "overall": {
            "total_sessions": total_sessions,
            "total_users": total_users,
            "responded_users": responded_users,
            "avg_response_time": avg_rt,
            "effective_rate": effective_rate,
            "within_3min_rate": within_3min_rate,
        },
        "agents": agents_out,
    }


def rebuild_from_logs(log_dir: str) -> None:
    """扫描全量日志重建统计（管理员触发，阻塞操作）"""
    log_path = pathlib.Path(log_dir)
    if not log_path.exists():
        logger.warning(f"[stats_service] 日志目录不存在: {log_dir}")
        return

    new_data = _default_stats()
    overall = new_data["overall"]
    agents = new_data["agents"]

    for file in log_path.glob("*.json"):
        try:
            with open(file, "r", encoding="utf-8") as f:
                user_data = json.load(f)
        except Exception:
            continue

        openid = user_data.get("openid", file.stem)

        for session in user_data.get("sessions", []):
            if not session.get("session_id", "").startswith("human_"):
                continue

            log = session.get("log", [])
            user_msgs = sum(1 for m in log if m.get("role") == "user")
            agent_msgs = sum(1 for m in log if m.get("role") == "agent")

            # 取第一条 agent 消息的 agent_name
            agent_name = ""
            for m in log:
                if m.get("role") == "agent" and m.get("agent_name"):
                    agent_name = m["agent_name"]
                    break

            # 计算应答时长：session start_ts 到第一条 agent 消息的时间差
            response_time = None
            start_ts = session.get("start_ts")
            if start_ts:
                for m in log:
                    if m.get("role") == "agent":
                        response_time = m["ts"] - start_ts
                        break

            # 更新 overall
            overall["total_sessions"] += 1
            if openid not in overall["all_openids"]:
                overall["all_openids"].append(openid)
                overall["total_users"] += 1
            if agent_msgs > 0 and openid not in overall["responded_openids"]:
                overall["responded_openids"].append(openid)
                overall["responded_users"] += 1
            if response_time is not None:
                overall["total_response_time"] += response_time
                overall["responded_count"] += 1
                if response_time <= 180:
                    overall["within_3min"] += 1

            # 更新 agent
            if agent_name:
                if agent_name not in agents:
                    agents[agent_name] = {
                        "sessions": 0,
                        "openids": [],
                        "user_messages": 0,
                        "agent_messages": 0,
                        "total_response_time": 0.0,
                        "responded_count": 0,
                    }
                ag = agents[agent_name]
                ag["sessions"] += 1
                if openid not in ag["openids"]:
                    ag["openids"].append(openid)
                ag["user_messages"] += user_msgs
                ag["agent_messages"] += agent_msgs
                if response_time is not None:
                    ag["total_response_time"] += response_time
                    ag["responded_count"] += 1

    with _lock:
        _save_sync(new_data)
    logger.info(f"[stats_service] 重建统计完成: {overall['total_sessions']} 会话")
