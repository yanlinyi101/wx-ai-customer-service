# 微信AI客服 Webhook — 运维维护手册

> 面向**客服负责人**，涵盖日常运维操作。
> 初次部署请参阅 `deploy/setup.sh` 和 `DEPLOY.md`。

---

## 目录

1. [服务器与基础信息](#第一章服务器与基础信息)
2. [日常部署（代码更新后）](#第二章日常部署代码更新后)
3. [服务管理与日志](#第三章服务管理与日志)
4. [AI API 管理](#第四章ai-api-管理)
5. [系统提示词修改](#第五章系统提示词修改)
6. [知识库日常维护](#第六章知识库日常维护)
7. [聊天记录查阅](#第七章聊天记录查阅)
8. [管理后台 admin.html](#第八章管理后台-adminhtml)
9. [转人工机制说明](#第九章转人工机制说明)
10. [完整环境变量清单](#第十章完整环境变量清单)
11. [常见问题排查](#第十一章常见问题排查)

---

## 第一章：服务器与基础信息

| 项目 | 值 |
|------|----|
| 服务器 IP | `YOUR_SERVER_IP` |
| 登录用户 | `root` |
| SSH 密钥 | `D:/小程序ai客服webhook/zm_pc1.pem` |
| 代码目录 | `/opt/wechat-ai/` |
| 聊天日志目录 | `/opt/wechat_chat_logs/` |
| 服务名称 | `wechat-ai`（systemd） |
| 服务端口 | `8000`（内部，Nginx 反向代理） |
| GitHub 仓库 | https://github.com/yanlinyi101/wx-ai-customer-service |

**SSH 快速连接：**

```bash
ssh -i "D:/小程序ai客服webhook/zm_pc1.pem" root@YOUR_SERVER_IP
```

---

## 第二章：日常部署（代码更新后）

本地修改代码后，执行以下三步：

### 第一步：上传改动文件

```bash
scp -i "D:/小程序ai客服webhook/zm_pc1.pem" -o StrictHostKeyChecking=no \
  "D:/小程序ai客服webhook/wechat_ai_service/main.py" \
  "D:/小程序ai客服webhook/wechat_ai_service/human_service.py" \
  "D:/小程序ai客服webhook/wechat_ai_service/chat_logger.py" \
  "D:/小程序ai客服webhook/wechat_ai_service/ai_service.py" \
  "D:/小程序ai客服webhook/wechat_ai_service/config.py" \
  "D:/小程序ai客服webhook/wechat_ai_service/wechat_api.py" \
  "D:/小程序ai客服webhook/wechat_ai_service/rag_service.py" \
  "D:/小程序ai客服webhook/wechat_ai_service/knowledge_base.json" \
  "D:/小程序ai客服webhook/wechat_ai_service/admin.html" \
  root@YOUR_SERVER_IP:/opt/wechat-ai/
```

### 第二步：重启服务

```bash
ssh -i "D:/小程序ai客服webhook/zm_pc1.pem" -o StrictHostKeyChecking=no \
  root@YOUR_SERVER_IP "systemctl restart wechat-ai"
```

### 第三步：验证

```bash
ssh -i "D:/小程序ai客服webhook/zm_pc1.pem" -o StrictHostKeyChecking=no \
  root@YOUR_SERVER_IP "curl -s http://127.0.0.1:8000/health"
```

返回 `{"status":"ok","service":"微信小程序AI客服"}` 即成功。

---

### 哪些情况需要重新部署

| 修改内容 | 是否需要部署 |
|----------|------------|
| 修改任何 `.py` 源码 | ✅ 需要 |
| 更新 `admin.html` | ✅ 需要 |
| 更新 `knowledge_base.json` | ✅ 需要 |
| 只改服务器 `.env` 文件（如 `AI_API_KEY`） | ❌ 不需要，仅重启服务即可 |

---

### 安装新依赖（requirements.txt 有改动时）

```bash
ssh -i "D:/小程序ai客服webhook/zm_pc1.pem" -o StrictHostKeyChecking=no \
  root@YOUR_SERVER_IP \
  "cd /opt/wechat-ai && venv/bin/pip install -r requirements.txt && systemctl restart wechat-ai"
```

---

## 第三章：服务管理与日志

所有命令在服务器上执行（SSH 连接后）：

```bash
# 查看服务状态
systemctl status wechat-ai

# 实时查看日志（Ctrl+C 退出）
journalctl -u wechat-ai -f

# 查看最近 100 行日志
journalctl -u wechat-ai -n 100

# 重启 / 停止 / 启动
systemctl restart wechat-ai
systemctl stop wechat-ai
systemctl start wechat-ai
```

---

### 关键日志标识

| 日志内容 | 含义 |
|----------|------|
| `收到消息 \| type=text` | 收到用户文字消息 |
| `[文字回复成功]` | AI 回复发送成功 |
| `[转人工] openid=...` | 用户进入人工模式 |
| `[人工模式] 缓冲消息` | 人工模式下用户消息已入队 |
| `[人工回复] openid=...` | 管理员通过后台成功发送回复 |
| `[关闭会话] openid=...` | 管理员关闭人工会话，用户恢复 AI |
| `[WS] 管理员连接` | 管理后台 WebSocket 已连接 |
| `[chat_logger] append_log 失败` | 聊天记录写入磁盘失败（检查 `/opt/wechat_chat_logs/` 权限） |
| `[微信API] 发送失败` | 微信 API 返回错误码 |

---

## 第四章：AI API 管理

### 配置位置

服务器上 `/opt/wechat-ai/.env` 文件（只在服务器存在，不进 Git）：

```bash
# 在服务器上编辑
ssh -i "D:/小程序ai客服webhook/zm_pc1.pem" root@YOUR_SERVER_IP "nano /opt/wechat-ai/.env"
```

修改后执行 `systemctl restart wechat-ai` 生效。

---

### AI 服务商切换

只需修改 `.env` 中对应变量，重启服务即可：

| 服务商 | `AI_BASE_URL` | `AI_MODEL` |
|--------|---------------|------------|
| DeepSeek（推荐，性价比最高） | `https://api.deepseek.com` | `deepseek-chat` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| Claude | `https://api.anthropic.com/v1` | `claude-haiku-4-5-20251001` |

---

### 余额监控

- DeepSeek 控制台：[platform.deepseek.com](https://platform.deepseek.com) → API Keys → 用量
- 预计月消耗（1000 咨询量）：约 ¥5–20，视消息长度而定
- 建议设置余额预警（低于 ¥30 时提醒充值）

---

## 第五章：系统提示词修改

### 方式一：修改 `.env`（推荐，仅需重启）

在服务器 `.env` 中新增或修改：

```
SYSTEM_PROMPT=你是一名专业、亲切的在线客服助手。请用简洁、友好的语气回复...
```

然后重启服务：`systemctl restart wechat-ai`

---

### 方式二：修改源码（需部署）

文件：`wechat_ai_service/config.py`，找到 `SYSTEM_PROMPT` 的默认值部分修改，再执行第二章的部署流程。

---

### 提示词建议

- 明确业务范围（本公司提供什么服务）
- 限制回复长度（建议 ≤200 字）
- 指定语气风格和称呼规范
- 产品具体信息放知识库，不要写在提示词里

---

## 第六章：知识库日常维护

### 文件位置

`wechat_ai_service/knowledge_base.json`（不进 Git，只在本地和服务器维护）

---

### 管理工具：`kb_tool.py`

```bash
cd D:\小程序ai客服webhook\wechat_ai_service

python kb_tool.py list                # 查看所有条目
python kb_tool.py add                 # 交互式添加新条目
python kb_tool.py delete <序号>       # 删除条目（序号从 1 开始）
python kb_tool.py export              # 导出为 knowledge_base.xlsx
python kb_tool.py import              # 从 knowledge_base.xlsx 批量导入
```

---

### 推荐工作流（批量更新）

```
1. python kb_tool.py export
   → 生成 knowledge_base.xlsx

2. 在 Excel 中批量编辑（问题 / 答案 / 关键词 / 图片URL）

3. python kb_tool.py import
   → 写回 JSON 文件

4. 上传并重启（见第二章）
```

---

### 关键词写作规则

- 宁多勿少，加同义词和口语词：`"积分" "查积分" "怎么看积分" "我的积分"`
- 每命中一个关键词 +2 分，多写关键词可提升命中率
- 避免过于通用的词（如"你好" "谢谢"），会拉高所有条目得分

---

## 第七章：聊天记录查阅

### 存储位置

服务器 `/opt/wechat_chat_logs/{openid}.json`

每个用户一个文件，包含**全部 AI 对话和人工对话**记录，永久保存，服务重启不丢失。

---

### 查看方式

```bash
# 列出所有用户的记录文件
ssh -i "D:/小程序ai客服webhook/zm_pc1.pem" root@YOUR_SERVER_IP \
  "ls -lh /opt/wechat_chat_logs/"

# 查看某个用户的完整记录（替换 openid）
ssh -i "D:/小程序ai客服webhook/zm_pc1.pem" root@YOUR_SERVER_IP \
  "cat /opt/wechat_chat_logs/{openid}.json | python3 -m json.tool"

# 查看最近修改的记录（最活跃的用户）
ssh -i "D:/小程序ai客服webhook/zm_pc1.pem" root@YOUR_SERVER_IP \
  "ls -lt /opt/wechat_chat_logs/ | head -10"
```

---

### 记录格式

```json
{
  "openid": "oXxxx...",
  "sessions": [
    {
      "session_id": "ai_1700000000",
      "start_ts": 1700000000,
      "end_ts": 1700001000,
      "log": [
        {"role": "user",  "text": "你好",       "ts": 1700000001},
        {"role": "ai",    "text": "您好！",      "ts": 1700000002}
      ]
    },
    {
      "session_id": "human_1700001100",
      "start_ts": 1700001100,
      "end_ts": null,
      "log": [
        {"role": "user",  "text": "转人工",     "ts": 1700001100},
        {"role": "agent", "text": "您好，我来帮您", "ts": 1700001200}
      ]
    }
  ]
}
```

`role` 字段：`user`=用户，`ai`=AI回复，`agent`=人工客服回复

---

## 第八章：管理后台 admin.html

### 访问方式

在浏览器中**直接打开本地文件**：

```
D:/小程序ai客服webhook/wechat_ai_service/admin.html
```

或从 GitHub 下载最新版后打开。

---

### 登录信息

| 字段 | 值 |
|------|----|
| **服务器地址** | `https://<你的域名或IP>` |
| **访问令牌** | 服务器 `.env` 中的 `ADMIN_TOKEN` 值 |

---

### 功能说明

| 功能 | 说明 |
|------|------|
| 实时会话列表 | WebSocket 推送，有新用户转人工时弹出提示 |
| 完整聊天记录 | 点击用户后展示完整 AI + 人工历史记录（从服务器文件加载） |
| 发送回复 | 输入框输入内容，点击发送或 Ctrl+Enter |
| 结束会话 | 点击"结束会话"，用户恢复 AI 自动回复模式 |
| HTTP 备用轮询 | WS 断开时自动切换 HTTP 每 10 秒轮询，不影响使用 |

---

### 更新 admin.html

修改本地文件后，按第二章流程上传 `admin.html` 到服务器并重启服务即可。

---

## 第九章：转人工机制说明

### 流程图

```
用户发送触发关键词（转人工 / 人工客服 / 人工 / 转接 / 真人）
        │
        ▼
系统进入人工模式（内存标记）
        ├─→ 用户收到：「正在为您转接人工客服，请稍候...」
        └─→ 管理员微信（ADMIN_OPENID）收到通知
                └─→ 管理员打开 admin.html 处理

用户继续发消息（人工模式中）
        │
        ▼
消息入队 + 写入本地日志文件 + 推送管理后台

管理员在 admin.html
        ├─→ 输入回复 → 用户收到
        └─→ 点击"结束会话" → 用户恢复 AI 模式
```

---

### 配置管理员通知（ADMIN_OPENID）

1. 管理员用微信打开小程序，向客服发任意一条消息
2. 服务器日志中找到：`收到消息 | openid=oXxxx... | type=text`
3. 复制完整 openid（约 28 位）
4. 写入服务器 `.env`：`ADMIN_OPENID=oXxxx...`
5. 重启服务

> `ADMIN_OPENID` 为空时：转人工流程仍然正常，但管理员不会收到微信通知，需主动打开后台查看。

---

### 触发关键词配置

当前关键词（`wechat_ai_service/config.py` 第 35 行）：

```python
HUMAN_TAKEOVER_KEYWORDS = ["转人工", "人工客服", "人工", "转接", "真人"]
```

修改后需重新部署。注意："人工" 命中范围较广，如出现误触发（如"人工智能"），可移除只保留更精确的词组。

---

### 注意：会话状态存于内存

服务重启后，正在进行的人工会话状态会丢失（用户会重新进入 AI 模式）。
**聊天记录不丢失**（已写入 `/opt/wechat_chat_logs/`）。

---

## 第十章：完整环境变量清单

> 位置：服务器 `/opt/wechat-ai/.env`

| 变量名 | 必填 | 说明 |
|--------|:----:|------|
| `WECHAT_TOKEN` | ✅ | 服务器验证 Token（与微信后台保持一致） |
| `WECHAT_APP_ID` | ✅ | 小程序 AppID |
| `WECHAT_APP_SECRET` | ✅ | 小程序 AppSecret |
| `WECHAT_ENCODING_AES_KEY` | ✅ | 消息加解密密钥（43 位） |
| `AI_API_KEY` | ✅ | AI 服务密钥（DeepSeek 等） |
| `AI_BASE_URL` | ❌ | AI 服务地址（默认 `https://api.deepseek.com`） |
| `AI_MODEL` | ❌ | 模型名称（默认 `deepseek-chat`） |
| `SYSTEM_PROMPT` | ❌ | 系统提示词（不填则用代码默认值） |
| `ADMIN_TOKEN` | ✅ | 管理后台访问令牌（自定义，建议32位随机字符串） |
| `ADMIN_OPENID` | ❌ | 管理员微信 openid，转人工时推送微信通知 |
| `KF_ACCOUNT` | ❌ | 指定人工客服账号（留空=自动分配） |
| `LOG_DIR` | ❌ | 聊天记录存储目录（默认 `/opt/wechat_chat_logs`） |
| `RAG_ENABLED` | ❌ | 知识库检索开关（默认 `true`） |
| `RAG_TOP_K` | ❌ | 每次返回最多条目数（默认 `3`） |
| `RAG_MIN_SCORE` | ❌ | 最低相关性分数阈值（默认 `1.0`） |

---

## 第十一章：常见问题排查

| 现象 | 排查步骤 |
|------|----------|
| 用户收不到任何回复 | 1. 查日志是否有`收到消息`；2. 检查 `AI_API_KEY` 是否有效；3. 查 DeepSeek 余额 |
| 收到"抱歉无法回复" | 查 `[AI] 调用失败` 日志，确认错误类型（超时/鉴权失败/余额不足） |
| 收不到图片 | 查 `[图片上传] 失败` 日志，确认图片 URL 可公开访问 |
| 回复不准确 | 检查 `knowledge_base.json` 关键词是否覆盖该问法 |
| 管理后台看不到客户消息 | 检查服务是否正常运行；查 `/opt/wechat_chat_logs/` 是否有文件写入 |
| 管理后台 WebSocket 一直重连 | 正常，HTTP 轮询(10s)会自动补位；检查 nginx 是否配置了 Upgrade 头 |
| 管理员没有收到转人工通知 | 检查 `.env` 中 `ADMIN_OPENID` 是否正确配置 |
| 聊天记录写入失败 | 检查 `/opt/wechat_chat_logs/` 目录权限：`chmod 755 /opt/wechat_chat_logs` |
| 服务启动失败 | 查 `journalctl -u wechat-ai -n 50` 找具体错误；常见原因：`.env` 缺失或依赖未安装 |

---

*最后更新：2026-03-05*
