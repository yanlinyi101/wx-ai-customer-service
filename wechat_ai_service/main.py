"""
微信小程序 AI 客服 Webhook 服务
FastAPI 主入口

本地启动命令：
    uvicorn main:app --host 0.0.0.0 --port 8000

腾讯云函数（SCF Web函数）启动：
    由 scf_bootstrap 自动启动，端口固定为 9000
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
from chat_logger import append_log, end_session as end_chat_session, get_user_log
from config import (
    WECHAT_ENCODING_AES_KEY,
    WECHAT_TOKEN,
    WECHAT_APP_ID,
    KF_ACCOUNT,
    ADMIN_TOKEN,
    ADMIN_OPENID,
    IMAGE_DIR,
    IMAGE_BASE_URL,
)
from crypto import WeChatCrypto
from human_service import (
    is_human_mode,
    enter_human_mode,
    exit_human_mode,
    push_message,
    get_all_sessions,
    save_pre_history,
)
from wechat_api import (
    download_user_image,
    get_or_upload_media,
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

# 允许 COS 静态网站域名跨域调用 JSON API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
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

crypto = WeChatCrypto(
    token=WECHAT_TOKEN,
    encoding_aes_key=WECHAT_ENCODING_AES_KEY,
    app_id=WECHAT_APP_ID,
)


# ──────────────────────────────────────────
# 管理认证辅助
# ──────────────────────────────────────────

def _check_admin(token: str) -> bool:
    """验证管理令牌（非空且匹配）"""
    return bool(ADMIN_TOKEN) and token == ADMIN_TOKEN


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
                }
                for oid, data in sessions.items()
            ]
        }, ensure_ascii=False)
        queue.put_nowait(payload)
    except Exception as e:
        logger.warning(f"[WS] 入队失败: {e}")


async def _broadcast_sessions() -> None:
    """有新消息/状态变化时推送给所有已连接管理员（通过各自的发送队列）"""
    if not _ws_queues:
        return
    try:
        sessions = await get_all_sessions()
    except Exception as e:
        logger.error(f"[WS broadcast] 获取会话失败: {e}")
        return
    payload = json.dumps({
        "type": "sessions",
        "sessions": [
            {
                "openid": oid,
                "short": oid[:8],
                "messages": data["messages"],
                "pre_history": data["pre_history"],
                "count": len(data["messages"]),
            }
            for oid, data in sessions.items()
        ]
    }, ensure_ascii=False)
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
    return {
        "ok": True,
        "sessions": [
            {
                "openid": oid,
                "short": oid[:8],
                "messages": data["messages"],
                "pre_history": data["pre_history"],
                "count": len(data["messages"]),
            }
            for oid, data in sessions.items()
        ],
    }


@app.post("/admin/reply")
async def admin_reply(request: Request, token: str = Query("")):
    """客服回复用户消息"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    body = await request.json()
    openid = body.get("openid", "").strip()
    message = body.get("message", "").strip()
    if not openid or not message:
        return JSONResponse({"ok": False, "error": "openid 和 message 不能为空"}, status_code=400)
    success = await send_text_message(openid, message)
    if success:
        logger.info(f"[人工回复] openid={openid[:8]}...")
        session_id = _human_sessions.get(openid, f"human_{int(time.time())}")
        await append_log(openid, "agent", message, time.time(), session_id)
        await push_message(openid, message, role="agent")
        await _broadcast_sessions()
        return {"ok": True}
    else:
        return JSONResponse({"ok": False, "error": "发送失败"}, status_code=500)


@app.post("/admin/close")
async def admin_close(request: Request, token: str = Query("")):
    """结束会话，用户恢复 AI 模式"""
    if not _check_admin(token):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    body = await request.json()
    openid = body.get("openid", "").strip()
    if not openid:
        return JSONResponse({"ok": False, "error": "openid 不能为空"}, status_code=400)
    await exit_human_mode(openid)
    await send_text_message(openid, "感谢您的耐心等候，如有其他问题随时告诉我 😊")
    logger.info(f"[关闭会话] openid={openid[:8]}... 恢复 AI 模式")
    # 结束人工会话日志
    session_id = _human_sessions.pop(openid, None)
    if session_id:
        await end_chat_session(openid, session_id)
    return {"ok": True}


@app.post("/admin/reply_image")
async def admin_reply_image(
    token: str = Query(""),
    openid: str = Form(...),
    image: UploadFile = File(...),
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
    await append_log(openid, "agent", "", ts_now, session_id,
                     image_url=accessible_url, msg_type="image")
    await push_message(openid, text="", role="agent",
                       image_url=accessible_url, msg_type="image")
    await _broadcast_sessions()
    logger.info(f"[admin_reply_image] 发送成功 openid={openid[:8]}...")
    return {"ok": True}


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
    return {"ok": True, "openid": openid, "sessions": log.get("sessions", [])}


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

    # 2. 逐张发送图片（最多2张，避免超出消息条数限制）
    for url in image_urls[:2]:
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
