# 微信AI客服 Webhook — 运维维护手册

> 本手册面向**客服负责人**，涵盖系统上线后的日常运维操作。
> 初次部署流程请参阅：`wechat_ai_service/腾讯云部署说明.md`

---

## 目录

1. [HTTPS 与 ICP 备案](#第一章https-与-icp-备案)
2. [AI API 管理](#第二章ai-api-管理)
3. [系统提示词修改](#第三章系统提示词修改)
4. [知识库图片 COS 存储](#第四章知识库图片-cos-存储)
5. [一键部署（代码更新后）](#第五章一键部署代码更新后)
6. [知识库日常维护](#第六章知识库日常维护)
7. [SCF 日志与故障排查](#第七章scf-日志与故障排查)
8. [完整环境变量清单](#第八章完整环境变量清单)
9. [转人工机制与管理员指令](#第九章转人工机制与管理员指令)
10. [管理后台 admin.html](#第十章管理后台-adminhtml)

---

## 第一章：HTTPS 与 ICP 备案

### 现状

| 项目 | 说明 |
|------|------|
| 当前 Webhook URL | `https://xxxxxxxx.scf.tencentcs.com/webhook` |
| HTTPS 来源 | 腾讯云 SCF 函数 URL 自带，**永久有效** |
| 是否需要 ICP 备案 | **不需要**，`tencentcs.com` 为腾讯官方子域名，无需备案 |

使用原生 SCF 函数 URL，HTTPS 证书由腾讯云自动维护，**无任何续期操作**，长期使用无问题。

---

### 何时需要 ICP 备案

**只有一种情况需要备案：** 当公司需要将 Webhook 绑定到**自定义域名**（如 `api.yourcompany.com`）时。
继续使用现有 SCF 函数 URL，**永远不需要备案**。

---

### 如需绑定自定义域名（备案后操作）

1. 腾讯云控制台 → **云函数** → 函数详情 → 函数 URL → **自定义域名**
2. 在 DNS 服务商添加 CNAME 记录：
   ```
   api.yourcompany.com  →  xxx.scf.tencentcs.com
   ```
3. 腾讯云会自动签发/续签 SSL 证书（Let's Encrypt，90 天自动续期，无需手动操作）
4. 在微信后台将 Webhook URL 更新为新域名

**ICP 备案流程：**

- 入口：腾讯云控制台 → **网站备案**
- 所需材料：营业执照、法人身份证、域名证书
- 审核周期：约 20–30 个工作日
- 备案完成后才可在腾讯云绑定该域名

---

## 第二章：AI API 管理

### 当前配置位置

腾讯云控制台 → **云函数** → `wechat-ai-service` → **函数配置** → **环境变量**

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `AI_API_KEY` | DeepSeek API 密钥 | — |
| `AI_BASE_URL` | AI 服务地址 | `https://api.deepseek.com` |
| `AI_MODEL` | 模型名称 | `deepseek-chat` |

---

### 密钥管理流程

1. 登录 [platform.deepseek.com](https://platform.deepseek.com) → **API Keys**
2. 建议为本项目新建独立密钥（便于权限隔离和用量追踪）
3. **更换密钥时：** SCF 控制台 → 环境变量 → 修改 `AI_API_KEY` → **保存**
   - 保存后**立即生效**，无需重新部署

---

### 余额监控

- 在 DeepSeek 控制台查看 API 用量和余额
- 每日消息量约 50–200 条，月消耗约 ¥2–10（根据消息长度而定）
- 建议设置余额预警（余额低于 ¥20 时提醒充值）

---

### 切换 AI 服务商

只需修改环境变量，**无需重新部署**：

| 服务商 | `AI_BASE_URL` | `AI_MODEL` |
|--------|---------------|------------|
| DeepSeek（推荐，性价比最高） | `https://api.deepseek.com` | `deepseek-chat` |
| OpenAI | `https://api.openai.com` | `gpt-4o-mini` |
| Claude | `https://api.anthropic.com/v1` | `claude-haiku-4-5-20251001` |

---

## 第三章：系统提示词修改

### 方式一：SCF 环境变量（推荐）

**优点：无需重新部署，保存后立即生效**

1. SCF 控制台 → 环境变量 → 找到或新增 `SYSTEM_PROMPT`
2. 值填写完整提示词文本
3. 点击**保存**，立即生效

---

### 方式二：修改源码（需重新部署）

文件路径：`wechat_ai_service/config.py`，第 24–30 行

```python
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", """
你是一名专业、亲切的在线客服助手。请遵守以下规则：
1. 用简洁、友好的语气回复，每次回复不超过200字
2. 如果用户询问退款、投诉等敏感问题，告知会转接人工客服
3. 不要编造产品信息，不确定时说"我帮您确认后回复"
4. 始终保持礼貌，称呼用户为"您"
""".strip())
```

修改后需执行一键部署（见[第五章](#第五章一键部署代码更新后)）。

---

### 提示词写作建议

- 明确业务范围（如：本公司提供 XX 课程/服务）
- 说明回复长度限制（建议 ≤200 字）
- 指定语气风格和称呼规范
- 不要在提示词中写产品具体信息（用知识库代替）

---

## 第四章：知识库图片 COS 存储

### 背景

知识库条目的 `image_url` 目前部分存储在阿里云 OSS。
建议将**新增图片**统一迁移到腾讯云 COS，与服务器同云厂商，访问更稳定。

---

### COS 存储桶配置（一次性设置）

1. 腾讯云控制台 → **COS** → **创建存储桶**
2. 名称：`wechat-kb-images-{AppID}`（自定义），地域：**上海**（与 SCF 同地域）
3. **访问权限：公有读私有写**（图片需公开访问）
4. 记录存储桶名称和地域备用

---

### 上传图片并获取 URL

1. 存储桶 → **文件列表** → **上传文件** → 选择图片
2. 建议目录结构：`kb_images/积分/积分入口.png`
3. 上传后点击文件 → **详情** → 复制"对象地址"，格式为：
   ```
   https://wechat-kb-images-{AppID}.cos.ap-shanghai.myqcloud.com/kb_images/xxx.png
   ```
4. 将此 URL 填入 `knowledge_base.json` 对应条目的 `image_url` 字段

---

### 图片规格建议

| 项目 | 建议 |
|------|------|
| 格式 | JPG 或 PNG（微信客服消息支持 JPG/PNG/GIF） |
| 宽度 | ≤750px（手机端显示友好） |
| 文件大小 | <1MB（减少上传时间） |

---

## 第五章：一键部署（代码更新后）

### 推荐工具：`deploy_to_scf.py`（根目录）

---

### 使用前提

根目录 `.env` 文件必须存在并包含以下内容：

```
TENCENT_SECRET_ID=AKIDxxxxxxxx
TENCENT_SECRET_KEY=xxxxxxxx
SCF_FUNCTION_NAME=wechat-ai-service
SCF_REGION=ap-shanghai
```

> 可选字段：`SCF_NAMESPACE`（默认 `default`）

---

### 执行命令

```bash
cd D:\小程序ai客服webhook
python deploy_to_scf.py
```

---

### 脚本执行步骤（自动完成）

1. **[1/3] 打包** — 遍历 `wechat_ai_service/` 目录，压缩为 `wechat_ai_service_v2.zip`
   （自动跳过 `.pyc` 文件和 `__pycache__` 目录）
2. **[2/3] 读取 ZIP 并编码** — 将 ZIP 转为 Base64 格式
3. **[3/3] 部署** — 调用腾讯云 SCF API `UpdateFunctionCode` 上传代码

**成功输出示例：**

```
[1/3] 打包 wechat_ai_service ...
    打包完成：1192 个文件，8.3 MB
[2/3] 读取 ZIP 并编码 ...
[3/3] 部署到 SCF：wechat-ai-service (ap-shanghai/default) ...

部署成功！RequestId: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
函数：wechat-ai-service  地域：ap-shanghai  命名空间：default
```

---

### ⚠️ 注意：`make_zip_python.py` 已废弃

- 该脚本含硬编码的旧桌面路径（`C:\Users\EDY\Desktop\...`），**不适用当前工程目录**
- 仅打包不部署，功能已完全包含在 `deploy_to_scf.py` 中
- **请始终使用 `deploy_to_scf.py`**，不要使用 `make_zip_python.py`

---

### 哪些情况需要重新部署

| 修改内容 | 是否需要部署 |
|----------|------------|
| 修改 `config.py` / `main.py` 等源码 | ✅ 需要 |
| 更新 `knowledge_base.json` | ✅ 需要 |
| 只改 SCF 环境变量（如 `AI_API_KEY`、`SYSTEM_PROMPT`） | ❌ 不需要，保存后立即生效 |

---

## 第六章：知识库日常维护

### 文件位置

`wechat_ai_service/knowledge_base.json`（随代码一起部署到 SCF）

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

2. 在 Excel 中批量编辑
   （问题 / 答案 / 关键词 / 图片URL）

3. python kb_tool.py import
   → 写回 JSON 文件

4. cd .. && python deploy_to_scf.py
   → 一键部署到 SCF
```

---

### 关键词写作规则

- **宁多勿少**，加入同义词和口语词
  - 示例："积分" "查积分" "怎么看积分" "积分商城" "我的积分"
- 每命中一个关键词 +2 分，多写关键词可提升命中率
- 避免过于通用的词（如"你好" "谢谢"），会污染所有条目的得分

---

## 第七章：SCF 日志与故障排查

### 查看日志

腾讯云控制台 → **云函数** → `wechat-ai-service` → **日志查询**

---

### 关键日志标识

| 日志内容 | 含义 |
|----------|------|
| `[INFO] 收到消息 \| type=text` | 收到用户文字消息 |
| `[AI] 第1次失败，1s后重试` | AI API 首次失败，正在重试 |
| `[AI] 调用失败(已重试)` | AI 连续失败，已发送知识库答案或兜底回复 |
| `[AI] 使用知识库答案兜底` | AI 挂掉时用知识库直接回答 |
| `[文字回复成功]` | 消息发送成功 |
| `[图片发送成功]` | 图片发送成功 |
| `[图片上传] 失败` | 图片下载或上传微信临时素材失败 |
| `[转人工] openid=...` | 用户进入人工模式，状态已写入 COS，管理员已收到通知 |
| `[人工模式] 缓冲消息 openid=...` | 人工模式下用户消息已入队，等待管理员处理 |
| `[人工回复] openid=...` | 管理员通过 `#reply` 指令成功发送回复 |
| `[关闭会话] openid=... 恢复 AI 模式` | 管理员通过 `#close` 指令关闭会话，用户恢复 AI |
| `[human_service] COS 状态已加载，人工会话数=N` | 实例启动后从 COS 恢复了 N 个人工会话状态 |
| `[human_service] COS 读取/写入失败` | COS 操作异常，已降级为纯内存模式 |
| `[微信API] 发送失败: {'errcode': ...}` | 微信 API 返回错误 |

---

### 常见问题排查

| 现象 | 排查步骤 |
|------|----------|
| 用户收不到任何回复 | 1. 检查 SCF 日志是否有 `[收到消息]`；2. 检查 `AI_API_KEY` 是否有效；3. 检查 DeepSeek 账户余额 |
| 收到"抱歉无法回复" | 查 `[AI] 调用失败` 日志，确认错误类型（超时 / 鉴权失败 / 余额不足） |
| 收不到图片 | 查 `[图片上传] 失败` 日志，确认图片 URL 可公开访问 |
| 回复不准确 | 检查 `knowledge_base.json` 关键词是否覆盖该问法；考虑添加同义词 |
| `40001` token 错误（已修复） | 已改用 `getStableAccessToken`，该错误不应再出现；如再现请联系开发者 |

---

## 第八章：完整环境变量清单

> 位置：腾讯云控制台 → **云函数** → 函数配置 → **环境变量**

| 变量名 | 必填 | 说明 | 来源 |
|--------|:----:|------|------|
| `WECHAT_TOKEN` | ✅ | 服务器验证 Token | 自定义，与微信后台保持一致 |
| `WECHAT_APP_ID` | ✅ | 小程序 AppID | mp.weixin.qq.com → 开发设置 |
| `WECHAT_APP_SECRET` | ✅ | 小程序 AppSecret | 同上（需验证身份） |
| `WECHAT_ENCODING_AES_KEY` | ✅ | 消息加解密密钥（43 位） | 微信后台"随机生成" |
| `AI_API_KEY` | ✅ | AI 服务密钥 | platform.deepseek.com |
| `AI_BASE_URL` | ❌ | AI 服务地址（默认 DeepSeek） | 切换服务商时修改 |
| `AI_MODEL` | ❌ | 模型名称（默认 `deepseek-chat`） | 切换模型时修改 |
| `SYSTEM_PROMPT` | ❌ | 系统提示词（不填则用代码默认值） | 自定义 |
| `KF_ACCOUNT` | ❌ | 指定人工客服账号（留空=自动分配） | 格式：`xxx@gh_公众号ID` |
| `COS_ENABLED` | ❌ | 是否开启聊天日志存储（默认 `false`） | `true` / `false` |
| `COS_SECRET_ID` | ❌ | COS 子账号 SecretId | CAM 控制台 |
| `COS_SECRET_KEY` | ❌ | COS 子账号 SecretKey | 同上 |
| `COS_REGION` | ❌ | COS 存储桶地域（默认 `ap-guangzhou`） | 与存储桶同地域 |
| `COS_BUCKET` | ❌ | 存储桶名称（格式：`name-appid`） | COS 控制台 |
| `RAG_ENABLED` | ❌ | 知识库检索开关（默认 `true`） | `true` / `false` |
| `RAG_TOP_K` | ❌ | 每次返回最多条目数（默认 `3`） | 数字 |
| `RAG_MIN_SCORE` | ❌ | 最低相关性分数阈值（默认 `1.0`） | 浮点数 |
| `ADMIN_OPENID` | ❌ | 管理员微信 openid，用于接收转人工通知和发送管理指令 | SCF 日志中获取（见第九章） |

---

## 第九章：转人工机制与管理员指令

### 方案概述

系统采用**微信指令管理**模式：管理员直接在微信对话框中向小程序客服发送指令（`#list`/`#reply`/`#close`），无需打开任何网页。会话状态写入 **COS JSON 文件**，多个 SCF 实例共享同一份数据，不会因实例切换而丢失。

```
用户 → "转人工"
        │
        ▼
Webhook 标记人工模式，写入 COS
        ├─→ 给用户发确认消息
        └─→ 给管理员发微信通知（含操作指令提示）

用户（人工模式）→ 继续发消息
        │
        ▼
消息入队写入 COS，回复"客服正在处理，请稍候..."

管理员（微信对话）→ 发送指令
        ├─ #list           → 收到等待用户列表
        ├─ #reply <前8位> <消息> → 用户收到回复
        └─ #close <前8位>  → 会话关闭，用户恢复 AI
```

---

### 前提条件：配置 ADMIN_OPENID

**第一步：获取管理员 openid**

管理员向小程序客服发送任意一条消息，在 SCF 日志（控制台 → 云函数 → 日志查询）中找到：

```
[INFO] 收到消息 | openid=oXxxx1234567890abcdef | type=text
```

复制完整的 openid（约 28 位）。

**第二步：配置环境变量**

在 SCF 控制台 → 函数配置 → 环境变量中新增：

```
ADMIN_OPENID = oXxxx1234567890abcdef
```

保存后**立即生效**，无需重新部署。

> ⚠️ `ADMIN_OPENID` 为空时，转人工流程仍然正常运行（用户可进入人工模式、消息可入队），但管理员不会收到通知，且无法使用指令。

---

### 管理员指令说明

管理员在微信小程序客服对话框中直接发送以下指令（Webhook 会优先识别管理员身份，不走 AI 流程）：

| 指令 | 格式 | 功能 |
|------|------|------|
| `#list` | `#list` | 查看当前所有等待中的用户及消息摘要 |
| `#reply` | `#reply <openid前8位> <消息内容>` | 向指定用户发送一条回复 |
| `#close` | `#close <openid前8位>` | 关闭该用户的人工会话，用户恢复 AI 模式 |
| 其他 | 任意文字 | 收到指令帮助说明 |

**示例操作流程：**

```
管理员发: #list
收到:     等待中的用户：
          • oXxxx1234 (3条)
            最新: 「我要退款」
            #reply oXxxx1234 <消息>
            #close oXxxx1234

管理员发: #reply oXxxx1234 您好，退款申请已收到，1-3个工作日处理完毕
用户收到: 您好，退款申请已收到，1-3个工作日处理完毕
管理员收到: ✅ 已发送给 oXxxx1234...

管理员发: #close oXxxx1234
用户收到: 感谢您的耐心等候，如有其他问题随时告诉我 😊
管理员收到: ✅ 已关闭 oXxxx1234... 的会话，用户已恢复 AI 模式
```

---

### COS 状态持久化

会话状态存储在 COS 固定路径：`human_state/state.json`（与聊天日志使用同一个存储桶）。

| 时机 | 操作 |
|------|------|
| 实例首次收到消息 | 从 COS 读取状态，加载到内存（懒加载）|
| 用户进入/退出人工模式 | 写入内存 + 同步写入 COS |
| 用户在人工模式下发消息 | 写入内存 + 同步写入 COS |
| COS 不可用 | 静默降级为纯内存模式，打印 error 日志 |

> COS 持久化需要 `COS_BUCKET`、`COS_SECRET_ID`、`COS_SECRET_KEY`、`COS_REGION` 环境变量正确配置。若 `COS_BUCKET` 为空，系统自动使用纯内存模式（重启后状态丢失）。

---

### 触发关键词配置

当前关键词列表（`config.py` 第 35 行）：

```python
HUMAN_TAKEOVER_KEYWORDS = ["转人工", "人工客服", "人工", "转接", "真人"]
```

**修改关键词：** 直接编辑 `config.py` 第 35 行，然后重新部署（见第五章）。

**注意：** "人工" 是单个汉字，命中范围较广。若出现误触发（如用户说"人工智能"），可将 "人工" 从列表中移除，只保留更精确的词组。

---

### 测试步骤

**步骤一：验证转人工触发**

1. 用测试设备打开小程序，进入客服对话
2. 发送：`转人工`
3. 预期结果：
   - 用户收到："好的，正在为您转接人工客服，请稍候。..."
   - SCF 日志出现：`[转人工] openid=xxxxxxxx...`
   - **管理员微信**收到通知，内容包含 openid 前8位和操作指令提示

**步骤二：验证人工模式消息缓冲**

1. 完成步骤一后，测试用户继续发消息（如 "我要退款"）
2. 预期结果：
   - 用户收到："客服正在处理，请稍候..."
   - AI **不回复**
   - SCF 日志出现：`[人工模式] 缓冲消息 openid=...`

**步骤三：验证管理员 `#list` 指令**

1. 管理员在微信发：`#list`
2. 预期结果：收到等待用户列表，包含用户 openid 前8位、消息条数、最新消息摘要

**步骤四：验证管理员 `#reply` 指令**

1. 管理员发：`#reply xxxxxxxx 您好，我是人工客服，正在处理您的问题`
2. 预期结果：
   - 测试用户**收到该消息**
   - 管理员收到：`✅ 已发送给 xxxxxxxx...`

**步骤五：验证 `#close` 关闭会话并恢复 AI**

1. 管理员发：`#close xxxxxxxx`
2. 预期结果：
   - 测试用户收到："感谢您的耐心等候..."
   - 管理员收到：`✅ 已关闭 xxxxxxxx... 的会话，用户已恢复 AI 模式`
3. 测试用户再次发一条普通问题（如 "你好"）
4. 预期结果：用户收到 AI 自动回复，SCF 日志出现 `[文字回复成功]`

**步骤六（可选）：验证 COS 跨实例状态恢复**

1. 完成步骤一（让某个用户进入人工模式）
2. 在 SCF 控制台手动**重新部署**（模拟新实例启动）
3. 管理员发 `#list`
4. 预期结果：仍能看到该用户（状态从 COS 恢复）

---

### 常见问题排查

| 现象 | 原因 | 解决 |
|------|------|------|
| 发"转人工"后用户没有收到确认消息 | 关键词未命中 / `send_text_message` API 失败 | 查 SCF 日志确认 errcode |
| 管理员没有收到通知 | `ADMIN_OPENID` 未配置或配置错误 | 检查 SCF 环境变量，重新获取 openid |
| `#list` 返回"没有等待中的用户" | 状态未持久化（COS 未配置）且实例已重启 | 配置 COS 相关环境变量启用持久化 |
| `#reply` 提示"未找到匹配用户" | openid 前8位输入有误 | 先用 `#list` 确认正确的前8位 |
| 人工模式用户发消息时 AI 也回复了 | 状态丢失（纯内存模式下实例被替换）| 配置 COS 持久化；或检查 `[human_service] COS 读取失败` 日志 |
| `[human_service] COS 写入失败` | COS 凭证配置错误或存储桶权限不足 | 检查 `COS_SECRET_ID`/`COS_SECRET_KEY`/`COS_BUCKET` 是否正确 |

---

## 第十章：管理后台 admin.html

### 背景

2024 年 1 月后创建的 COS 存储桶**不支持在浏览器直接预览 HTML 文件**（访问会强制下载），
因此 admin.html 已迁移到 **Cloudflare Pages** 托管（免费，全球 CDN，正常渲染）。
SCF 仍负责所有后端逻辑，admin.html 通过 AJAX 跨域调用 SCF API（`main.py` 已配置 `CORSMiddleware allow_origins=["*"]`，无需额外改动）。

---

### 访问地址

```
https://wechat-admin-panel.pages.dev/admin.html
```

---

### 登录方式

打开页面后填入以下信息：

| 字段 | 值 |
|------|----|
| **SCF URL** | API Gateway 地址（腾讯云控制台 → 云函数 → 函数 URL） |
| **Token** | `3db825397d467b2421324f9e0b20d02b` |

填入后点击登录，即可看到会话列表。

> **快捷登录（URL 参数）：** 可将 SCF URL 和 Token 写入地址栏，直接登录：
> ```
> https://wechat-admin-panel.pages.dev/admin.html?token=3db825397d467b2421324f9e0b20d02b&scf=https://<your-apigw-url>
> ```

---

### 更新 admin.html

每次修改 `wechat_ai_service/admin.html` 后，执行以下命令重新部署：

```bash
cp "D:/小程序ai客服webhook/wechat_ai_service/admin.html" /d/tmp/wechat-admin/
cd /d/tmp/wechat-admin
npx wrangler pages deploy . --project-name wechat-admin-panel --branch main
```

部署成功后输出示例：

```
✨ Success! Uploaded 1 files (2.00 sec)
✨ Deployment complete! Take a peek over at https://xxxx.wechat-admin-panel.pages.dev
```

> **注意：** `/d/tmp/wechat-admin/` 为临时工作目录，`npx wrangler` 需要已登录 Cloudflare 账号（`npx wrangler login`，OAuth 认证，一次登录长期有效）。

---

### Cloudflare 项目信息

| 项目 | 值 |
|------|----|
| 项目名称 | `wechat-admin-panel` |
| 生产分支 | `main` |
| 生产域名 | `https://wechat-admin-panel.pages.dev` |
| 管理控制台 | https://dash.cloudflare.com → Pages |

---

*最后更新：2026-03-03*
