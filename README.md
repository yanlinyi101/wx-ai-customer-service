# wx-ai-customer-service

微信小程序 AI 客服 Webhook 服务。用户在小程序客服对话中发消息，系统自动检索知识库、调用 AI 生成回复；用户输入关键词可一键转接人工，客服通过 Web 管理后台实时接待。

---

## 功能特性

### AI 自动回复
- **意图路由** — 前置意图分类层，根据 RAG 评分区分闲聊 / 模糊问题 / 明确问题，使用不同提示词策略回复（零额外 LLM 调用）
- **RAG 知识库检索** — 关键词 + 汉字重叠双重评分，优先从知识库提取答案注入提示词，支持图文回复
- **AI 自动回复** — 接入火山方舟 Ark（doubao-seed-2-0-pro），兼容 OpenAI 格式，切换服务商只需改 `.env`
- **多轮对话记忆** — 每个用户独立维护最近 5 轮对话历史

### 人工客服
- **一键转人工** — 检测关键词自动切换模式，通知管理员微信
- **会话认领** — 客服认领指定用户会话，独占接入避免抢单冲突
- **主动发起会话** — 客服可从历史记录主动向用户发起对话
- **超时自动回收** — 5 分钟无交互自动结束会话；3 分钟无人接入自动转回 AI
- **图片收发** — 用户可发送图片，客服可回复图片

### Web 管理后台（admin.html）
- **实时推送** — WebSocket 主推 + HTTP 轮询双保险，零延迟显示新消息
- **多账号管理** — 管理员可创建/删除子账号，设置管理员权限；普通账号仅见自己接待的数据
- **在线状态** — 客服可切换在线 / 离线状态，离线时系统仍可转人工但有提示
- **历史记录** — 查看任意用户的完整 AI + 人工聊天记录，支持日期跳转
- **全文搜索** — 按消息内容关键词 + 日期范围搜索历史聊天记录
- **星标用户** — 为重要用户打星标置顶，按账号独立存储
- **客户备注** — 为客户添加最多 200 字的共享备注，所有客服可见可编辑
- **常用语模板** — 每个客服账号独立维护最多 20 条常用回复，一键插入输入框
- **数据统计** — 会话数、接待用户数、有效应答率、平均应答时长等多维度数据，支持日期范围筛选
- **灰度测试** — 按比例将用户流量分配给 AI 或人工队列，实时可视化分流比例

### 系统
- **聊天记录持久化** — 全量对话写入服务器 JSON 文件，服务重启不丢失
- **消息安全加密** — 全程微信安全模式（AES-256-CBC），防消息伪造
- **消息去重** — MsgId 缓存防止微信重试导致消息被重复处理

---

## 架构

```
微信用户
  │  发消息
  ▼
微信服务器
  │  POST /webhook（AES 加密 XML）
  ▼
云服务器 FastAPI（uvicorn + nginx）
  ├─ crypto.py        解密 & 验签
  ├─ rag_service.py   检索知识库，返回 (context, images, top_score)
  ├─ ai_service.py    意图路由 → 选择提示词 → 调用 AI API
  ├─ human_service.py 人工模式状态管理（内存）
  ├─ gray_service.py  灰度测试分组（内存 + JSON 持久化）
  ├─ stats_service.py 会话统计数据读写
  ├─ chat_logger.py   全量对话写入本地 JSON 文件
  └─ wechat_api.py    调用微信客服消息 API 发送回复

客服
  │  打开 admin.html（浏览器本地文件）
  ▼
WebSocket /ws/admin ──── 实时推送新消息
REST API /admin/*   ──── 登录、回复、结束会话、查历史、统计、备注、常用语
```

---

## 项目结构

```
wx-ai-customer-service/
├── wechat_ai_service/
│   ├── main.py              FastAPI 入口，Webhook 路由 + 管理 API + WebSocket
│   ├── config.py            所有配置项（从 .env 读取）+ 意图路由阈值 + 三套提示词
│   ├── ai_service.py        意图路由、AI 调用、对话历史管理
│   ├── rag_service.py       知识库加载与检索，返回 top_score 供路由判断
│   ├── human_service.py     人工客服模式状态管理（内存）
│   ├── gray_service.py      灰度测试分组与流量分配
│   ├── stats_service.py     会话统计数据（读写 stats.json）
│   ├── chat_logger.py       聊天记录持久化（本地 JSON 文件）
│   ├── cos_logger.py        对话日志同步腾讯云 COS（可选）
│   ├── wechat_api.py        微信 API（发消息、上传素材、access_token）
│   ├── crypto.py            微信消息 AES-256-CBC 加解密
│   ├── kb_tool.py           知识库管理命令行工具
│   ├── admin.html           客服管理后台（单文件，浏览器直接打开）
│   ├── agents.json          客服账号数据（含常用语，自动生成）
│   ├── customer_notes.json  客户备注数据（自动生成）
│   └── requirements.txt     Python 依赖
├── deploy/
│   ├── nginx.conf           Nginx 反向代理配置（含 WebSocket 支持）
│   ├── wechat-ai.service    systemd 服务配置
│   └── setup.sh             服务器一键初始化脚本
├── deploy.py                一键部署脚本（打包 → scp → 服务器重启）
├── DEPLOY.md                服务器信息 & 快速部署命令参考
└── MAINTENANCE.md           完整运维手册
```

---

## 快速部署

### 前提条件

- 一台公网 Linux 服务器（Ubuntu 20.04+），已备案域名或直接用 IP
- 微信小程序已开通客服消息功能
- 火山方舟 Ark API Key（或其他兼容 OpenAI 格式的服务商）

### 1. 克隆仓库

```bash
git clone https://github.com/yanlinyi101/wx-ai-customer-service.git
cd wx-ai-customer-service
```

### 2. 服务器初始化（首次部署）

```bash
# 上传代码到服务器
scp -r wechat_ai_service/* root@YOUR_SERVER_IP:/opt/wechat-ai/

# 在服务器上执行初始化
ssh root@YOUR_SERVER_IP "bash /opt/wechat-ai/deploy/setup.sh"
```

### 3. 配置环境变量

在服务器 `/opt/wechat-ai/.env` 中填写：

```env
# 微信小程序（mp.weixin.qq.com → 开发设置）
WECHAT_TOKEN=自定义字符串
WECHAT_APP_ID=小程序AppID
WECHAT_APP_SECRET=小程序AppSecret
WECHAT_ENCODING_AES_KEY=43位随机字符串

# AI 服务（火山方舟 Ark，兼容 OpenAI 格式）
AI_API_KEY=your_ark_api_key
AI_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
AI_MODEL=doubao-seed-2-0-pro-260215

# 管理后台
ADMIN_TOKEN=自定义访问令牌（建议32位随机字符串）
ADMIN_OPENID=管理员微信openid（可选，用于转人工时推送通知）

# 图片存储（客服发图片功能需要）
IMAGE_DIR=/opt/wechat_images
IMAGE_BASE_URL=https://你的域名/images
```

### 4. 启动服务

```bash
systemctl restart wechat-ai
curl http://127.0.0.1:8000/health  # 返回 {"status":"ok"} 即成功
```

### 5. 微信后台配置

在 [mp.weixin.qq.com](https://mp.weixin.qq.com) → 开发 → 开发设置 → 消息推送：

- URL：`https://你的域名/webhook`
- Token：与 `WECHAT_TOKEN` 一致
- 加密方式：安全模式
- 消息加密密钥：与 `WECHAT_ENCODING_AES_KEY` 一致

### 6. 后续部署（代码更新）

```bash
python deploy.py   # 本地一键打包上传并重启服务
```

### 7. 打开管理后台

用浏览器直接打开本地文件 `wechat_ai_service/admin.html`，填入服务器地址和 `ADMIN_TOKEN` 登录即可。

---

## 意图路由

消息进入后，系统先用 RAG 检索知识库得到最高匹配分 `top_score`，再根据阈值路由到三种处理策略，**无需额外 LLM 调用**。

| top_score 范围 | 意图 | 策略 | 图片 |
|---|---|---|---|
| < 2.0 | 闲聊（如"在吗"） | 亲和客服语气直接回复 | 不返回 |
| 2.0 ~ 3.9 | 模糊问题（如"我想买东西"） | 追问 1-2 个关键细节 | 不返回 |
| ≥ 4.0 | 明确产品问题 | 注入知识库内容回答 | 正常返回 |

阈值通过环境变量 `INTENT_LOW_THRESHOLD`（默认 2.0）/ `INTENT_HIGH_THRESHOLD`（默认 4.0）调整，无需改代码。

---

## 管理后台功能说明

### 会话处理

| 操作 | 说明 |
|------|------|
| 等待列表 | 左侧显示所有请求转人工的用户，有新请求时弹出提示音 + 系统通知 |
| 接入会话 | 点击用户卡片 → 确认弹窗 → 独占认领，其他客服只读 |
| 发送回复 | 支持文字（Ctrl+Enter 或 Enter 发送）和图片 |
| 常用语 | 点击 💬 按钮弹出常用语列表，一键插入输入框；可在管理弹窗中增删 |
| 客户备注 | 顶部 NOTE 栏点击可为当前用户添加/编辑共享备注（≤200字） |
| 结束会话 | 点击"结束会话"，用户恢复 AI 自动回复模式 |
| 主动发起 | 历史记录 Tab 选中用户后可主动发起人工会话 |

### 历史记录

| 操作 | 说明 |
|------|------|
| 切换 Tab | 点击"历史记录"标签加载所有历史用户 |
| 搜索用户 | 按昵称过滤用户列表 |
| 全文搜索 | 按消息关键词 + 日期范围搜索所有聊天记录 |
| 日期跳转 | 历史记录内点击"跳转日期"可快速定位到指定日期 |
| 星标 | 点击 ☆ 为用户打星标置顶 |

### 账号权限

| 角色 | 权限 |
|------|------|
| 管理员 | 查看所有用户数据、账号管理、灰度测试配置、数据统计 |
| 普通客服 | 仅查看自己接待过的用户、使用常用语、编辑客户备注 |

### 灰度测试

在设置面板中可按比例将流量分配给 AI 或人工队列：
- 启用后，新用户按设定比例随机分组
- 分组结果持久化，同一用户始终走同一路径
- 保存新配置后自动清空所有历史分组，重新采样

---

## 知识库编辑

### 条目格式

```json
{
  "question": "如何申请退款？",
  "answer": "支持收到商品后7天无理由退款，请在小程序「我的订单」中申请。",
  "keywords": ["退款", "退货", "不想要", "七天", "7天", "无理由"],
  "image_url": ""
}
```

| 字段 | 说明 |
|------|------|
| `question` | 问题描述，参与字符重叠评分 |
| `answer` | 标准答案，命中时原文注入 AI 提示词 |
| `keywords` | 触发词列表，每命中一词 +2 分；**宁多勿少，多写口语词和同义词** |
| `image_url` | 命中时附带发送的图片链接，无图留空 `""` |

### 命令行工具

```bash
cd wechat_ai_service
python kb_tool.py list            # 查看所有条目
python kb_tool.py add             # 交互式添加
python kb_tool.py delete <序号>   # 删除
python kb_tool.py export          # 导出为 Excel
python kb_tool.py import          # 从 Excel 批量导入
```

---

## 切换 AI 服务商

修改服务器 `.env`，重启服务即可：

| 服务商 | `AI_BASE_URL` | `AI_MODEL` |
|--------|--------------|------------|
| 火山方舟（当前） | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seed-2-0-pro-260215` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |

---

## 技术栈

| 组件 | 选型 |
|------|------|
| Web 框架 | FastAPI + uvicorn |
| 实时通信 | WebSocket（FastAPI 原生） |
| HTTP 客户端 | httpx（异步） |
| 消息加解密 | pycryptodome（AES-256-CBC） |
| 部署 | 云服务器 + systemd + Nginx |
| AI 接口 | 火山方舟 Ark（doubao-seed-2-0-pro，兼容 OpenAI 格式） |
| 聊天记录 | 本地 JSON 文件（`/opt/wechat_chat_logs/`） |
| 管理后台 | 纯 HTML + 原生 JS（无框架，浏览器直接打开） |

---

## API 端点速览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/webhook` | 微信服务器验证 & 消息接收 |
| GET | `/health` | 健康检查 |
| POST | `/admin/login` | 客服登录 |
| GET | `/admin/sessions` | 获取实时会话列表 |
| POST | `/admin/reply` | 客服发送文字回复 |
| POST | `/admin/reply_image` | 客服发送图片回复 |
| POST | `/admin/claim` | 认领会话 |
| POST | `/admin/close` | 结束会话 |
| POST | `/admin/initiate_session` | 主动发起会话 |
| GET | `/admin/history/{openid}` | 获取用户聊天记录 |
| GET | `/admin/all_users` | 获取历史用户列表 |
| GET | `/admin/search` | 全文搜索聊天记录 |
| GET/PUT | `/admin/notes/{openid}` | 获取/更新客户备注 |
| GET/PUT | `/admin/quick_replies` | 获取/更新客服常用语 |
| GET/POST | `/admin/gray` | 灰度测试配置 |
| GET/POST/PUT/DELETE | `/admin/agents` | 客服账号管理 |
| GET | `/admin/stats` | 数据统计 |
| WS | `/ws/admin` | 实时 WebSocket 推送 |

---

## 文档

- [DEPLOY.md](./DEPLOY.md) — 服务器信息 & 快速部署命令参考
- [MAINTENANCE.md](./MAINTENANCE.md) — 完整运维手册（日志查看、知识库维护、故障排查等）

---

## License

MIT
