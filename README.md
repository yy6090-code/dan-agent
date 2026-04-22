# Dan Agent 🤖

一个基于 Kimi K2.6 的智能 AI Agent，支持联网搜索、网页抓取、长期记忆、执行命令，并可通过 Open WebUI 提供漂亮的聊天界面。

---

## 功能特性

- 🔍 **联网搜索** — 自动搜索实时信息（新闻、价格、近况等）
- 🌐 **网页抓取** — 给它一个网址，它帮你读取全文内容
- 🧠 **长期记忆** — 记住你告诉它的重要信息，下次启动也不忘
- 💻 **执行命令** — 可以运行终端命令，安装软件、克隆仓库等
- 🖼️ **看图理解** — 支持发送图片让 Agent 分析
- 💰 **花费统计** — 实时显示 Token 用量和费用
- 📝 **对话保存** — 每次对话自动保存到本地

---

## 使用方式

### 方式一：终端直接对话

```bash
python3 my_agent.py
```

### 方式二：通过 Open WebUI 网页界面对话（推荐）

先启动 Agent 服务器：
```bash
python3 agent_server.py
```
然后打开浏览器访问 `localhost:3000`，选择 `my-kimi-agent` 模型开始对话。

---

## 安装部署

### 第一步：下载项目

```bash
git clone https://github.com/yy6090-code/dan-agent.git
cd dan-agent
```

### 第二步：安装依赖

```bash
pip3 install openai firecrawl-py tavily-python fastapi uvicorn
```

### 第三步：配置 API Key

去 [Kimi 开放平台](https://platform.moonshot.cn/console/api-keys) 创建 API Key，然后：

```bash
echo 'export MOONSHOT_API_KEY="你的key"' >> ~/.zshrc
source ~/.zshrc
```

### 第四步（可选）：设置快捷启动命令

```bash
echo 'alias agent="python3 ~/dan-agent/agent_server.py"' >> ~/.zshrc
source ~/.zshrc
```

以后只需输入 `agent` 即可启动服务器。

---

## 配合 Open WebUI 使用

Open WebUI 是一个开源的 AI 聊天界面，需要单独安装（需要 Docker）。

### 安装 Open WebUI

```bash
docker run -d -p 3000:8080 \
  -v open-webui:/app/backend/data \
  --name open-webui \
  ghcr.io/open-webui/open-webui:main
```

打开 `localhost:3000` 注册账号。

### 连接到你的 Agent

1. 管理员面板 → 设置 → 外部连接
2. OpenAI 接口旁点 **+** 添加连接：
   - URL：`http://host.docker.internal:8000/v1`
   - API Key：随便填（比如 `123456`）
3. 保存后刷新页面，顶部选择 `my-kimi-agent` 开始对话

---

## 每次使用流程

```
1. 打开终端，输入：agent
2. 打开浏览器：localhost:3000
3. 选择模型：my-kimi-agent
4. 开始对话 ✅
```

---

## 常见问题

**模型列表里没有 my-kimi-agent？**
→ 确认 agent 服务器已启动，Open WebUI 连接 URL 必须是 `host.docker.internal:8000/v1`，不能用 `localhost`。

**报错 401 Invalid Authentication？**
→ 检查 `echo $MOONSHOT_API_KEY` 是否有输出，没有就重新设置 API Key。

**端口 8000 被占用？**
→ 运行 `lsof -ti:8000 | xargs kill -9`，再重启服务器。

---

## 项目结构

```
dan-agent/
├── my_agent.py       # 终端版 Agent（直接对话）
├── agent_server.py   # 服务器版 Agent（供 Open WebUI 调用）
├── memory.md         # 长期记忆文件（自动生成）
└── history/          # 对话历史目录（自动生成）
```

---

## 依赖说明

| 库 | 用途 |
|----|------|
| openai | 调用 Kimi API |
| firecrawl-py | 网页抓取 |
| tavily-python | 备用搜索 |
| fastapi + uvicorn | 服务器（供 Open WebUI 连接） |
