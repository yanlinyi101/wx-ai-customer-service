"""
微信小程序 AI 客服 Webhook 服务
FastAPI 主入口

消息处理流程：
    微信消息 → crypto.py 解密 → 意图路由（ai_service）→ 火山方舟 LLM → 微信 API 回复
    转人工关键词命中时跳过 LLM，直接触发 human_service 转接客服

启动命令：
    uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1

部署：
    python deploy.py   # 打包上传至 VPS 并重启 systemd 服务 wechat-ai
"""

import asyncio
import json
import logging
import os
import time
import uuid

from fastapi import BackgroundTasks, FastAPI, File, Form, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from ai_service import get_ai_reply, needs_human, clear_history, get_history
from chat_logger import append_log, end_session as end_chat_session, get_user_log, list_all_users, update_nickname
import stats_service
from config import (
    WECHAT_ENCODING_AES_KEY,
    WECHAT_TOKEN,
    WECHAT_APP_ID,
    KF_ACCOUNT,
    ADMIN_TOKEN,
    ADMIN_OPENID,
    IMAGE_DIR,
    IMAGE_BASE_URL,
    LOG_DIR,
    load_agents,
    save_agents,
)
from crypto import WeChatCrypto
from human_service import (
    is_human_mode,
    enter_human_mode,
    exit_human_mode,
    push_message,
    get_all_sessions,
    save_pre_history,
    get_idle_openids,
    get_unattended_openids,
    attribute_session,
    get_session_attribution,
    get_session_queue,
    claim_session,
    get_claimer,
    clear_claim,
)
from wechat_api import (
    download_user_image,
    get_or_upload_media,
    get_user_nickname,
    send_image_message,
    send_text_message,
    send_typing_indicator,
)

# ──────────────────────────────────────────
# 初始化
# ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="微信小程序 AI 客服", version="1.0.0")

# 5分钟无交互自动结束人工会话
_IDLE_TIMEOUT_SECONDS = 5 * 60
_UNATTENDED_TIMEOUT_SECONDS = 3 * 60  # 3分钟无客服接入自动转回AI

# 允许 COS 静态网站域名跨域调用 JSON API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)

# MsgId 去重（防微信重试导致消息被处理两次）
_processed_msg_ids: set[str] = set()
_MSG_ID_MAX = 1000

# WebSocket 管理员连接池
_ws_admin_connections: set[WebSocket] = set()
# 每个连接的发送队列（保证同一 WebSocket 的 send 串行，避免并发冲突）
_ws_queues: dict[WebSocket, asyncio.Queue] = {}

# AI 对话 session_id 追踪（openid -> session_id）
_ai_sessions: dict[str, str] = {}
# 人工会话 session_id 追踪（openid -> session_id）
_human_sessions: dict[str, str] = {}
# 账号 CRUD 操作锁
_agents_lock = asyncio.Lock()

crypto = WeChatCrypto(
    token=WECHAT_TOKEN,
    encoding_aes_key=WECHAT_ENCODING_AES_KEY,
    app_id=WECHAT_APP_ID,
)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(_auto_close_idle_sessions())


async def _auto_close_idle_sessions() -> None:
    """后台任务：定期检查空闲/无人接入会话"""
    while True:
        await asyncio.sleep(60)  # 每60秒检查一次
        try:
            unattended_oids = get_unattended_openids(_UNATTENDED_TIMEOUT_SECONDS)
            idle_oids = get_idle_openids(_IDLE_TIMEOUT_SECONDS)
            processed = set()
            changed = False

            # 优先处理：3分钟无客服接入，转回AI
            for openid in unattended_oids:
                processed.add(openid)
                logger.info(f"[无人接入] 3分钟无客服接入，转回AI openid={openid[:8]}")
                messages = get_session_queue(openid)
                user_msgs = sum(1 for m in messages if m.get("role") == "user")
                asyncio.create_task(asyncio.to_thread(
                    stats_service.record_session_close,
                    "", openid, user_msgs, 0, None,
                ))
                await exit_human_mode(openid)
                await send_text_message(
                    openid,
                    "抱歉，当前客服繁忙暂时无法接入，已为您转回智能助手处理。\n"
                    "如需人工客服，请再次发送\"人工\"。",
                )
                session_id = _human_sessions.pop(openid, None)
                if session_id:
                    await end_chat_session(openid, session_id)
                changed = True

            # 其次处理：5分钟无交互（已有接入但用户停止发消息）
            for openid in idle_oids:
                if openid in processed:
                    continue
                logger.info(f"[超时关闭] 5分钟无交互，自动结束 openid={openid[:8]}")
                messages = get_session_queue(openid)
                user_msgs = sum(1 for m in messages if m.get("role") == "user")
                agent_msgs_count = sum(1 for m in messages if m.get("role") == "agent")
                attribution = get_session_attribution(openid)
                asyncio.create_task(asyncio.to_thread(
                    stats_service.record_session_close,
                    attribution.get("agent_name") or "",
                    openid, user_msgs, agent_msgs_count,
                    attribution.get("response_time"),
                ))
                await exit_human_mode(openid)
                await send_text_message(
                    openid,
                    "您已超过5分钟未发送消息，客服已自动离线。如需继续咨询请重新发送消息 😊",
                )
                session_id = _human_sessions.pop(openid, None)
                if session_id:
                    await end_chat_session(openid, session_id)
                changed = True

            if changed:
                await _broadcast_sessions()
        except Exception as e:
            logger.error(f"[自动关闭] 检查异常: {e}")


# ──────────────────────────────────────────
# 管理认证辅助
# ──────────────────────────────────────────

def _check_admin(token: str) -> bool:
    """验证管理令牌（非空且匹配）"""
    return bool(ADMIN_TOKEN) and token == ADMIN_TOKEN


@app.post("/admin/login")
async def admin_login(request: Request):
    """客服账号登录，返回 agent_name 和 token"""
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not username or not password:
        return JSONResponse({"ok": False, "error": "用户名和密码不能为空"}, status_code=400)
    agents = load_agents()
    for agent in agents:
        if agent.get("username") == username and agent.get("password") == password:
            return {"ok": True, "agent_name": username, "token": ADMIN_TOKEN, "is_admin": agent.get("is_admin", False)}
    return JSONResponse({"ok": False, "error": "用户名或密码错误"}, status_code=401)


@app.websocket("/ws/admin")
async def ws_admin(websocket: WebSocket, token: str = Query("")):
    """管理员 WebSocket 连接：实时推送会话状态"""
    if not _check_admin(token):
        await websocket.close(code=4001)
        return
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue()
    _ws_admin_connections.add(websocket)
    _ws_queues[websocket] = queue
    logger.info(f"[WS] 管理员连接，当前连接数={len(_ws_admin_connections)}")

    async def receiver():
        """接收客户端消息（ping 回 pong，放入发送队列）"""
        while True:
            try:
                msg = await websocket.receive_text()
                if msg == "ping":
                    queue.put_nowait("pong")
            except Exception:
                break

    async def sender():
        """从队列中取出消息逐个发送，保证串行"""
        while True:
            data = await queue.get()
            try:
                await websocket.send_text(data)
            except Exception:
                break

    recv_task = asyncio.create_task(receiver())
    send_task = asyncio.create_task(sender())

    # 连接成功后立即推送一次当前会话列表
    await _push_sessions_to_queue(queue)

    try:
        done, pending = await asyncio.wait(
            {recv_task, send_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        for task in done:
            if not task.cancelled():
                exc = task.exception()
                if exc and not isinstance(exc, WebSocketDisconnect):
                    logger.warning(f"[WS] 连接异常: {exc}")
    except Exception as e:
        logger.warning(f"[WS] 意外错误: {e}")
        recv_task.cancel()
        send_task.cancel()
    finally:
        _ws_admin_connections.discard(websocket)
        _ws_queues.pop(websocket, None)
        logger.info(f"[WS] 管理员断开，当前连接数={len(_ws_admin_connections)}")


async def _push_sessions_to_queue(queue: asyncio.Queue) -> None:
    """将当前会话列表放入指定连接的发送队列"""
    try:
        sessions = await get_all_sessions()
        payload = json.dumps({
            "type": "sessions",
            "sessions": [
                {
                    "openid": oid,
                    "short": oid[:8],
                    "messages": data["messages"],
                    "pre_history": data["pre_history"],
                    "count": len(data["messages"]),
                    "claimed_by": data.get("claimed_by", ""),
                }
                for oid, data in sessions.items()
            ]
        }, ensure_ascii=False)
        queue.put_nowait(payload)
    except Exception as e:
        logger.warning(f"[WS] 入队失败: {e}")


async def _get_nickname(openid: str) -> str:
    """获取用户昵称：优先读本地缓存，失败返回空串（不缓存 openid 前缀）"""
    try:
        log = await get_user_log(openid)
        nick = log.get("nickname", "")
        # 只有真实昵称（非 openid 前缀）才返回
        if nick and nick != openid[:8]:
            return nick
    except Exception:
        pass
    nick = await get_user_nickname(openid)
    # 只缓存真实昵称，跳过 openid 前缀回退值
    if nick and nick != openid[:8]:
        await update_nickname(openid, nick)
        return nick
    return ""


async def _broadcast_sessions() -> None:
    """有新消息/状态变化时推送给所有已连接管理员（通过各自的发送队列）"""
    if not _ws_queues:
        return
    try:
        sessions = await get_all_sessions()
    except Exception as e:
        logger.error(f"[WS broadcast] 获取会话失败: {e}")
        return

    async def _item(oid, data):
        nick = await _get_nickname(oid)
        return {
            "openid": oid,
            "short": oid[:8],
            "nickname": nick,
            "messages": data["messages"],
            "pre_history": data["pre_history"],
            "count": len(data["messages"]),
            "claimed_by": data.get("claimed_by", ""),
        }

    items = await asyncio.gather(*[_item(oid, data) for oid, data in sessions.items()])
    payload = json.dumps({"type": "sessions", "sessions": list(items)}, ensure_ascii=False)
    for ws, q in list(_ws_queues.items()):
        q.put_nowait(payload)


def _get_or_create_ai_session(openid: str) -> str:
    """获取或新建该 openid 的 AI 会话 session_id"""
    if openid not in _ai_sessions:
        _ai_sessions[openid] = f"ai_{int(time.time())}"
    return _ai_sessions[openid]


def _reset_ai_session(openid: str) -> str:
    """重置 AI 会话（用户转人工后，再回到AI时新建会话），返回旧 session_id"""
    old = _ai_sessions.pop(openid, None)
    return old or ""


# ──────────────────────────────────────────
# GET /webhook — 服务器验证（配置时微信调用一次）
# ──────────────────────────────────────────

@app.get("/webhook")
async def verify_server(
    signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """
    微信服务器配置验证
    验证成功原样返回 echostr，微信确认服务器有效
    """
    if crypto.verify_get(signature, timestamp, nonce):
        logger.info("服务器验证成功")
        return PlainTextResponse(echostr)
    else:
        logger.warning("服务器验证失败，签名不匹配")
        return PlainTextResponse("forbidden", status_code=403)


# ──────────────────────────────────────────
# POST /webhook — 接收用户消息
# ──────────────────────────────────────────

@app.post("/webhook")
async def receive_message(
    request: Request,
    background_tasks: BackgroundTasks,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
):
    """
    接收微信推送的客服消息
    处理流程：
    1. 立刻返回 "success"（必须5秒内响应）
    2. BackgroundTasks 在响应发出后继续执行：
       解密 → AI 生成回复 → 调用微信发送 API

    使用 FastAPI BackgroundTasks 而非 asyncio.create_task，
    确保在 SCF 云函数环境中任务能可靠完成。
    """
    body = await request.body()
    body_xml = body.decode("utf-8")

    # 解密并验证消息
    msg = crypto.decrypt_and_parse(body_xml, msg_signature, timestamp, nonce)

    if msg is None:
        logger.warning("消息验签失败，忽略本次请求")
        return PlainTextResponse("success")

    msg_id = msg.get("MsgId", "")
    if msg_id:
        if msg_id in _processed_msg_ids:
            logger.info(f"重复消息忽略 MsgId={msg_id}")
            return PlainTextResponse("success")
        if len(_processed_msg_ids) >= _MSG_ID_MAX:
            _processed_msg_ids.clear()
        _processed_msg_ids.add(msg_id)

    msg_type = msg.get("MsgType", "")
    openid = msg.get("FromUserName", "")

    logger.info(f"收到消息 | openid={openid[:8]}... | type={msg_type}")

    if msg_type == "text":
        user_text = msg.get("Content", "").strip()

        # 已在人工模式：缓冲消息，回复等待提示
        if await is_human_mode(openid):
            background_tasks.add_task(_handle_human_queue, openid, user_text)
            logger.info(f"[人工模式] 缓冲消息 openid={openid[:8]}...")
            return PlainTextResponse("success")

        # 用户主动请求转人工
        if needs_human(user_text):
            background_tasks.add_task(_do_enter_human, openid, user_text)
            return PlainTextResponse("success")

        # 普通消息 → AI 处理
        background_tasks.add_task(_handle_text, openid, user_text)

    elif msg_type == "event":
        event = msg.get("Event", "")
        if event == "user_enter_tempsession":
            background_tasks.add_task(_send_welcome, openid)

    elif msg_type == "image":
        pic_url = msg.get("PicUrl", "")
        if await is_human_mode(openid):
            # 人工模式：下载图片并推送给客服后台
            background_tasks.add_task(_handle_human_image, openid, pic_url)
        else:
            # AI 模式：暂不支持图片
            background_tasks.add_task(
                send_text_message, openid, "您好，目前仅支持文字消息，请用文字描述您的问题 😊"
            )

    else:
        # 语音等其他类型暂不支持，友好提示
        background_tasks.add_task(
            send_text_message, openid, "您好，目前仅支持文字消息，请用文字描述您的问题 😊"
        )

    # 必须立刻返回 "success"，否则微信会重试3次
    return PlainTextResponse("success")


# ──────────────────────────────────────────
# 管理 API（供 COS 静态网站 admin.html 调用）
# ──────────────────────────────────────────

@app.get("/admin/sessions")
async def admin_sessions(token: str = Query("")):
    """返回所有人工模式会话的 JSON 列表"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    try:
        sessions = await get_all_sessions()
    except Exception as e:
        logger.error(f"[admin_sessions] 获取会话失败: {e}")
        sessions = {}   # 降级：返回空列表而非 500
    async def _item(oid, data):
        nick = await _get_nickname(oid)
        return {
            "openid": oid,
            "short": oid[:8],
            "nickname": nick,
            "messages": data["messages"],
            "pre_history": data["pre_history"],
            "count": len(data["messages"]),
            "claimed_by": data.get("claimed_by", ""),
        }

    items = await asyncio.gather(*[_item(oid, data) for oid, data in sessions.items()])
    return {"ok": True, "sessions": list(items)}


@app.post("/admin/reply")
async def admin_reply(request: Request, token: str = Query("")):
    """客服回复用户消息"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    body = await request.json()
    openid = body.get("openid", "").strip()
    message = body.get("message", "").strip()
    agent_name = body.get("agent_name", "").strip()
    if not openid or not message:
        return JSONResponse({"ok": False, "error": "openid 和 message 不能为空"}, status_code=400)
    claimer = get_claimer(openid)
    if claimer and claimer != agent_name:
        return JSONResponse({"ok": False, "error": f"该会话已被 {claimer} 接入"}, status_code=403)
    success = await send_text_message(openid, message)
    if success:
        logger.info(f"[人工回复] openid={openid[:8]}... agent={agent_name or '未知'}")
        # 记录会话归属（首个回复者获得归属）
        attribute_session(openid, agent_name)
        session_id = _human_sessions.get(openid, f"human_{int(time.time())}")
        await append_log(openid, "agent", message, time.time(), session_id, agent_name=agent_name)
        await push_message(openid, message, role="agent")
        await _broadcast_sessions()
        return {"ok": True}
    else:
        return JSONResponse({"ok": False, "error": "发送失败"}, status_code=500)


@app.post("/admin/claim")
async def admin_claim(request: Request, token: str = Query("")):
    """客服认领会话（独占接入，其他客服只读）"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    body = await request.json()
    openid = body.get("openid", "").strip()
    agent_name = body.get("agent_name", "").strip()
    if not openid or not agent_name:
        return JSONResponse({"ok": False, "error": "openid 和 agent_name 不能为空"}, status_code=400)
    success = claim_session(openid, agent_name)
    if success:
        await _broadcast_sessions()
        return {"ok": True}
    else:
        claimer = get_claimer(openid)
        return JSONResponse({"ok": False, "error": f"该会话已被 {claimer} 接入"}, status_code=409)


@app.post("/admin/close")
async def admin_close(request: Request, token: str = Query("")):
    """结束会话，用户恢复 AI 模式"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    body = await request.json()
    openid = body.get("openid", "").strip()
    agent_name = body.get("agent_name", "").strip()
    if not openid:
        return JSONResponse({"ok": False, "error": "openid 不能为空"}, status_code=400)

    # 关闭前收集统计数据
    messages = get_session_queue(openid)
    user_msgs = sum(1 for m in messages if m.get("role") == "user")
    agent_msgs_count = sum(1 for m in messages if m.get("role") == "agent")
    attribution = get_session_attribution(openid)
    final_agent_name = attribution.get("agent_name") or agent_name
    response_time = attribution.get("response_time")

    await exit_human_mode(openid)
    await send_text_message(openid, "感谢您的耐心等候，如有其他问题随时告诉我 😊")
    logger.info(f"[关闭会话] openid={openid[:8]}... 恢复 AI 模式 agent={final_agent_name or '未知'}")

    # 写入统计（异步线程，不阻塞响应）
    asyncio.create_task(asyncio.to_thread(
        stats_service.record_session_close,
        final_agent_name, openid, user_msgs, agent_msgs_count, response_time,
    ))

    # 结束人工会话日志
    session_id = _human_sessions.pop(openid, None)
    if session_id:
        await end_chat_session(openid, session_id)
    await _broadcast_sessions()  # 立即推送最新会话列表，避免已关闭会话在前端复现
    return {"ok": True}


@app.post("/admin/reply_image")
async def admin_reply_image(
    token: str = Query(""),
    openid: str = Form(...),
    image: UploadFile = File(...),
    agent_name: str = Form(""),
):
    """客服发送图片消息（multipart/form-data）"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    if not openid:
        return JSONResponse({"ok": False, "error": "openid 不能为空"}, status_code=400)
    if not IMAGE_BASE_URL:
        return JSONResponse({"ok": False, "error": "IMAGE_BASE_URL 未配置"}, status_code=500)

    # 推断扩展名
    ext = "jpg"
    if image.filename:
        suffix = image.filename.rsplit(".", 1)[-1].lower()
        if suffix in ("jpg", "jpeg", "png", "gif", "webp"):
            ext = suffix

    filename = f"agent_{openid[:8]}_{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(IMAGE_DIR, filename)
    os.makedirs(IMAGE_DIR, exist_ok=True)

    try:
        content = await image.read()
        with open(save_path, "wb") as f:
            f.write(content)
    except Exception as e:
        logger.error(f"[admin_reply_image] 保存失败: {e}")
        return JSONResponse({"ok": False, "error": "图片保存失败"}, status_code=500)

    accessible_url = f"{IMAGE_BASE_URL.rstrip('/')}/{filename}"

    # 上传微信临时素材并发送给用户
    media_id = await get_or_upload_media(accessible_url)
    if not media_id:
        return JSONResponse({"ok": False, "error": "微信素材上传失败"}, status_code=500)

    success = await send_image_message(openid, media_id)
    if not success:
        return JSONResponse({"ok": False, "error": "微信图片发送失败"}, status_code=500)

    session_id = _human_sessions.get(openid, f"human_{int(time.time())}")
    ts_now = time.time()
    # 记录会话归属（首个图片回复也算接入）
    attribute_session(openid, agent_name)
    await append_log(openid, "agent", "", ts_now, session_id,
                     image_url=accessible_url, msg_type="image", agent_name=agent_name)
    await push_message(openid, text="", role="agent",
                       image_url=accessible_url, msg_type="image")
    await _broadcast_sessions()
    logger.info(f"[admin_reply_image] 发送成功 openid={openid[:8]}... agent={agent_name or '未知'}")
    return {"ok": True}


@app.get("/admin/all_users")
async def admin_all_users(token: str = Query("")):
    """返回所有用户基础信息（昵称、最后消息时间、消息总数），按最近活跃降序"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    try:
        users = await list_all_users()
    except Exception as e:
        logger.error(f"[admin_all_users] 获取失败: {e}")
        return JSONResponse({"ok": False, "error": "获取失败"}, status_code=500)

    async def fill_nickname(user: dict) -> dict:
        if not user["nickname"]:
            nick = await get_user_nickname(user["openid"])
            new_user = {**user, "nickname": nick}
            await update_nickname(user["openid"], nick)
            return new_user
        return user

    filled = await asyncio.gather(*[fill_nickname(u) for u in users])
    sorted_users = sorted(filled, key=lambda u: u["last_ts"], reverse=True)
    return {"ok": True, "users": list(sorted_users)}


@app.get("/admin/history/{openid}")
async def admin_history(openid: str, token: str = Query("")):
    """返回指定用户的全量聊天记录（AI + 人工，从本地文件读取）"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    try:
        log = await get_user_log(openid)
    except Exception as e:
        logger.error(f"[admin_history] 读取失败 openid={openid[:8]}: {e}")
        return JSONResponse({"ok": False, "error": "读取失败"}, status_code=500)
    return {
        "ok": True,
        "openid": openid,
        "nickname": log.get("nickname", ""),
        "sessions": log.get("sessions", []),
    }


@app.get("/admin/stats")
async def admin_stats(token: str = Query("")):
    """返回整体和客服维度的统计数据"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    try:
        data = await asyncio.to_thread(stats_service.get_stats)
        return {"ok": True, **data}
    except Exception as e:
        logger.error(f"[admin_stats] 获取统计失败: {e}")
        return JSONResponse({"ok": False, "error": "获取统计失败"}, status_code=500)


@app.post("/admin/rebuild_stats")
async def admin_rebuild_stats(token: str = Query("")):
    """重建统计数据（扫描全量日志，管理员触发）"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    try:
        await asyncio.to_thread(stats_service.rebuild_from_logs, LOG_DIR)
        return {"ok": True, "message": "统计数据重建完成"}
    except Exception as e:
        logger.error(f"[admin_rebuild_stats] 重建失败: {e}")
        return JSONResponse({"ok": False, "error": "重建失败"}, status_code=500)


@app.get("/admin/agents")
async def admin_list_agents(token: str = Query("")):
    """返回客服账号列表（不含密码）及其统计数据"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    agents = load_agents()
    stats = await asyncio.to_thread(stats_service.get_stats)
    agent_stats = stats.get("agents", {})
    result = []
    for a in agents:
        name = a.get("username", "")
        s = agent_stats.get(name, {})
        result.append({
            "username": name,
            "is_admin": a.get("is_admin", False),
            "online": a.get("online", False),
            "sessions": s.get("sessions", 0),
            "unique_users": s.get("unique_users", 0),
            "avg_response_time": s.get("avg_response_time"),
        })
    return {"ok": True, "agents": result}


@app.post("/admin/agents")
async def admin_add_agent(request: Request, token: str = Query("")):
    """新增客服账号"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()
    is_admin = bool(body.get("is_admin", False))
    if not username or not password:
        return JSONResponse({"ok": False, "error": "用户名和密码不能为空"}, status_code=400)
    async with _agents_lock:
        agents = await asyncio.to_thread(load_agents)
        if any(a.get("username") == username for a in agents):
            return JSONResponse({"ok": False, "error": "用户名已存在"}, status_code=400)
        new_agents = agents + [{"username": username, "password": password, "is_admin": is_admin}]
        await asyncio.to_thread(save_agents, new_agents)
    return {"ok": True}


@app.put("/admin/agents/{username}")
async def admin_update_agent(username: str, request: Request, token: str = Query("")):
    """修改客服账号密码或管理员状态"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    body = await request.json()
    new_password = body.get("password", "").strip()
    new_is_admin = body.get("is_admin")  # None means don't change
    if not new_password and new_is_admin is None:
        return JSONResponse({"ok": False, "error": "没有要修改的内容"}, status_code=400)
    async with _agents_lock:
        agents = await asyncio.to_thread(load_agents)
        new_agents = []
        found = False
        for a in agents:
            if a.get("username") == username:
                found = True
                updated = {**a}
                if new_password:
                    updated["password"] = new_password
                if new_is_admin is not None:
                    updated["is_admin"] = bool(new_is_admin)
                new_agents.append(updated)
            else:
                new_agents.append(a)
        if not found:
            return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=404)
        await asyncio.to_thread(save_agents, new_agents)
    return {"ok": True}


@app.put("/admin/agents/{username}/status")
async def admin_set_agent_status(username: str, request: Request, token: str = Query("")):
    """设置客服在线/离线状态（客服自己调用，无需管理员权限）"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    body = await request.json()
    online = bool(body.get("online", False))
    async with _agents_lock:
        agents = await asyncio.to_thread(load_agents)
        new_agents = []
        found = False
        for a in agents:
            if a.get("username") == username:
                found = True
                new_agents.append({**a, "online": online})
            else:
                new_agents.append(a)
        if not found:
            return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=404)
        await asyncio.to_thread(save_agents, new_agents)
    logger.info(f"[agent_status] {username} → {'在线' if online else '离线'}")
    return {"ok": True, "online": online}


@app.delete("/admin/agents/{username}")
async def admin_delete_agent(username: str, token: str = Query("")):
    """删除客服账号"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    async with _agents_lock:
        agents = await asyncio.to_thread(load_agents)
        if len(agents) <= 1:
            return JSONResponse({"ok": False, "error": "至少保留一个账号"}, status_code=400)
        new_agents = [a for a in agents if a.get("username") != username]
        if len(new_agents) == len(agents):
            return JSONResponse({"ok": False, "error": "账号不存在"}, status_code=404)
        await asyncio.to_thread(save_agents, new_agents)
    return {"ok": True}


@app.get("/admin/gray")
async def admin_gray_get(token: str = Query("")):
    """获取灰度测试配置"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    from gray_service import get_config
    return {"ok": True, **get_config()}


@app.post("/admin/gray")
async def admin_gray_set(request: Request, token: str = Query("")):
    """更新灰度测试配置"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    body = await request.json()
    enabled  = bool(body.get("enabled", False))
    ai_ratio = float(body.get("ai_ratio", 0.2))
    from gray_service import update_config, clear_all
    update_config(enabled, ai_ratio)
    clear_all()
    return {"ok": True}


# ──────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────

def _build_transfer_xml(openid: str, timestamp: str, kf_account: str = "") -> str:
    """构建 transfer_customer_service 被动回复的内层 XML"""
    trans_info = ""
    if kf_account:
        trans_info = f"<TransInfo><KfAccount><![CDATA[{kf_account}]]></KfAccount></TransInfo>"
    return (
        f"<xml>"
        f"<ToUserName><![CDATA[{openid}]]></ToUserName>"
        f"<FromUserName><![CDATA[{WECHAT_APP_ID}]]></FromUserName>"
        f"<CreateTime>{timestamp}</CreateTime>"
        f"<MsgType><![CDATA[transfer_customer_service]]></MsgType>"
        f"{trans_info}"
        f"</xml>"
    )


# ──────────────────────────────────────────
# 异步处理逻辑
# ──────────────────────────────────────────

async def _do_enter_human(openid: str, user_text: str) -> None:
    """用户请求转人工：更新状态、通知用户、可选通知客服"""
    pre_hist = get_history(openid)        # 清除前先取历史
    await enter_human_mode(openid)
    save_pre_history(openid, pre_hist)    # 保存转人工前对话
    await push_message(openid, user_text) # 保存触发词本身
    clear_history(openid)

    # 结束 AI 会话日志，开始人工会话日志
    old_ai_session = _reset_ai_session(openid)
    if old_ai_session:
        await end_chat_session(openid, old_ai_session)
    human_session_id = f"human_{int(time.time())}"
    _human_sessions[openid] = human_session_id
    ts_now = time.time()
    await append_log(openid, "user", user_text, ts_now, human_session_id)
    await send_text_message(
        openid,
        "好的，正在为您转接人工客服，请稍候。\n"
        "如暂无客服在线，我们会在工作时间（9:00-18:00）尽快联系您。",
    )
    if ADMIN_OPENID:
        await send_text_message(
            ADMIN_OPENID,
            f"🔔 新用户请求人工\n"
            f"用户：{openid[:8]}...\n"
            f"最新消息：「{user_text[:50]}」\n\n"
            f"请登录管理页处理。",
        )
    else:
        logger.warning("[转人工] ADMIN_OPENID 未配置，管理员无法收到微信通知")
    logger.info(f"[转人工] openid={openid[:8]}...")
    await _broadcast_sessions()


async def _handle_human_queue(openid: str, text: str) -> None:
    """人工模式下用户发消息：入队 + 记录日志"""
    await push_message(openid, text)
    session_id = _human_sessions.get(openid, f"human_{int(time.time())}")
    await append_log(openid, "user", text, time.time(), session_id)
    await _broadcast_sessions()


async def _handle_human_image(openid: str, pic_url: str) -> None:
    """人工模式下用户发图片：下载到本地 → 推送后台"""
    if not IMAGE_BASE_URL or not pic_url:
        logger.warning(f"[human_image] IMAGE_BASE_URL 未配置或 pic_url 为空，跳过")
        return

    ext = "jpg"
    if "." in pic_url.split("?")[0].split("/")[-1]:
        suffix = pic_url.split("?")[0].rsplit(".", 1)[-1].lower()
        if suffix in ("jpg", "jpeg", "png", "gif", "webp"):
            ext = suffix

    filename = f"{openid[:8]}_{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(IMAGE_DIR, filename)
    os.makedirs(IMAGE_DIR, exist_ok=True)

    ok = await download_user_image(pic_url, save_path)
    if not ok:
        logger.error(f"[human_image] 下载失败 openid={openid[:8]}")
        return

    accessible_url = f"{IMAGE_BASE_URL.rstrip('/')}/{filename}"
    session_id = _human_sessions.get(openid, f"human_{int(time.time())}")
    ts_now = time.time()
    await push_message(openid, text="", role="user",
                       image_url=accessible_url, msg_type="image")
    await append_log(openid, "user", "", ts_now, session_id,
                     image_url=accessible_url, msg_type="image")
    await _broadcast_sessions()
    logger.info(f"[human_image] 已处理用户图片 openid={openid[:8]} url={accessible_url}")


async def _handle_text(openid: str, text: str) -> None:
    """处理用户文本消息"""

    # ── 灰度分组：human 组静默进入人工队列 ──────────────────────────────────
    from gray_service import get_or_assign
    if get_or_assign(openid) == "human":
        await enter_human_mode(openid)
        await push_message(openid, text, "user")
        await _broadcast_sessions()
        logger.info(f"[GRAY] {openid[:8]} → human queue")
        return
    # ────────────────────────────────────────────────────────────────────────

    # 发送"正在输入"提示（让用户知道消息已收到）
    await send_typing_indicator(openid)

    # 调用 AI 生成回复（同时返回知识库命中的图片链接）
    reply, image_urls = await get_ai_reply(openid, text)

    # 写入结构化对话日志（同一 AI 对话共享同一 session_id）
    session_id = _get_or_create_ai_session(openid)
    ts_now = time.time()
    await append_log(openid, "user", text, ts_now, session_id)
    await append_log(openid, "ai", reply, ts_now + 0.001, session_id)

    # 1. 先发文字回复
    success = await send_text_message(openid, reply)
    if success:
        logger.info(f"[文字回复成功] openid={openid[:8]}...")
    else:
        logger.error(f"[文字回复失败] openid={openid[:8]}...")

    # 2. 逐张发送图片（最多10张，支持多图知识库条目）
    # 以下场景不发图：
    # ① 回复含"转人工"升级提示
    # ② AI 告知"没有相关信息，建议联系人工客服"（RAG误命中其他产品条目时的回退措辞）
    # ③ 用户已进入人工模式（竞态：AI请求处理中用户转人工）
    if "转人工" in reply or "人工客服" in reply or await is_human_mode(openid):
        image_urls = []
    for url in image_urls[:10]:
        try:
            media_id = await get_or_upload_media(url)
            if media_id:
                await send_image_message(openid, media_id)
                logger.info(f"[图片发送成功] openid={openid[:8]}...")
        except Exception as e:
            logger.error(f"[图片发送失败] {url} | {e}")


async def _send_welcome(openid: str) -> None:
    """用户进入客服对话时发送欢迎语"""
    await send_text_message(
        openid,
        "您好！我是 AI 智能客服，很高兴为您服务 😊\n请问有什么可以帮助您的？"
    )


# ──────────────────────────────────────────
# 健康检查（用于确认服务正常运行）
# ──────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "微信小程序AI客服"}
