"""
把 my_agent.py 包装成 OpenAI 兼容的 API 服务器
这样 Open WebUI 就可以把它当成"模型"来对话

用法：
  pip install fastapi uvicorn
  python agent_server.py
然后在 Open WebUI → 设置 → 添加连接，填 http://localhost:8000
"""

import os
import json
import time
import asyncio
from pathlib import Path

# browser_use 需要 python3.11，用懒加载避免启动时报错
_browser_use_available = False
try:
    from browser_use import Agent as BrowserAgent
    from browser_use.llm.openai.like import ChatOpenAILike
    _browser_use_available = True
except ImportError:
    pass
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn
from openai import AsyncOpenAI
import httpx._models as _httpx_models

# ── 修复 httpx 不支持非 ASCII 请求头的问题（和 my_agent.py 相同的补丁）──
def _utf8_normalize(value, encoding=None):
    if isinstance(value, bytes):
        return value
    try:
        return value.encode(encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        return value.encode("utf-8")
_httpx_models._normalize_header_value = _utf8_normalize

# ── 配置 ──────────────────────────────────────────────────────────────────
KIMI_API_KEY      = os.environ.get("MOONSHOT_API_KEY", "")
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")
TAVILY_API_KEY    = os.environ.get("TAVILY_API_KEY", "")
MEMORY_FILE       = Path.home() / "dan-agent" / "memory.md"
MODEL             = "kimi-k2.6"
PORT              = 8000  # 服务器监听端口，可以改

kimi_client = AsyncOpenAI(
    api_key=KIMI_API_KEY,
    base_url="https://api.moonshot.cn/v1",
)

app = FastAPI()

# ── 工具函数（从 my_agent.py 复制过来）────────────────────────────────────

def read_memory() -> str:
    return MEMORY_FILE.read_text(encoding="utf-8").strip() if MEMORY_FILE.exists() else ""

def write_memory(content: str):
    MEMORY_FILE.write_text(content, encoding="utf-8")

def scrape_webpage(url: str) -> str:
    from firecrawl import FirecrawlApp
    if not FIRECRAWL_API_KEY:
        return "错误：未设置 FIRECRAWL_API_KEY"
    app = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
    result = app.scrape(url, formats=["markdown"])
    content = result.markdown if hasattr(result, "markdown") else result.get("markdown", "")
    return content[:5000] if content else "网页内容为空"

def save_memory(content: str) -> str:
    existing = read_memory()
    write_memory((existing + "\n- " + content).strip())
    return f"已记住：{content}"

def get_memory() -> str:
    memory = read_memory()
    return memory if memory else "暂无长期记忆"

def search_web_tavily(query: str) -> str:
    from tavily import TavilyClient
    if not TAVILY_API_KEY:
        return "错误：未设置 TAVILY_API_KEY"
    client = TavilyClient(api_key=TAVILY_API_KEY)
    result = client.search(query=query, max_results=3)
    output = []
    for item in result.get("results", []):
        output.append(f"标题：{item.get('title','')[:100]}\n摘要：{item.get('content','')[:300]}\n链接：{item.get('url','')}")
    return "\n---\n".join(output) if output else "没有找到相关结果"

BLOCKED_COMMANDS = [
    "rm -rf", "rm -r /", "sudo rm",
    "mkfs", "dd if=", ":(){ :|:& };:",
    "chmod -R 777 /", "chown -R",
    "> /etc/passwd", "| bash", "| sh",
    "shutdown", "reboot", "halt",
]

async def browse_web(task: str) -> str:
    """让浏览器自动完成任务，比如点击、填表、登录、截图等"""
    if not _browser_use_available:
        return "错误：browser_use 未安装。请用 bash ~/dan-agent/server.sh 启动服务器（必须用 python3.11）"
    if not KIMI_API_KEY:
        return "错误：MOONSHOT_API_KEY 未设置，请用 bash ~/dan-agent/server.sh 启动服务器"
    try:
        llm = ChatOpenAILike(
            model=MODEL,
            api_key=KIMI_API_KEY,
            base_url="https://api.moonshot.cn/v1",
            temperature=1,         # Kimi K2.6 只允许 temperature=1
            frequency_penalty=0,   # Kimi K2.6 只允许 frequency_penalty=0
        )
        agent = BrowserAgent(task=task, llm=llm)
        history = await agent.run()
        # 提取最后一条有意义的结果
        final = history.final_result()
        if final:
            return str(final)
        # 没有 final_result 就拼接所有 extracted_content
        contents = [r.extracted_content for r in history.all_results if r.extracted_content]
        return "\n".join(contents) if contents else "任务完成，无文字输出"
    except Exception as e:
        return f"浏览器操作出错：{e}"

def run_shell(command: str) -> str:
    import subprocess
    cmd_lower = command.lower().strip()
    for blocked in BLOCKED_COMMANDS:
        if blocked in cmd_lower:
            return f"🚫 命令被拦截，包含危险操作：{blocked}\n命令：{command}"
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=60
        )
        output = result.stdout + result.stderr
        return output[:3000] if output else "命令执行完毕，无输出。"
    except subprocess.TimeoutExpired:
        return "命令超时（超过60秒）"
    except Exception as e:
        return f"执行出错：{e}"

TOOL_MAP = {
    "scrape_webpage": scrape_webpage,
    "save_memory": save_memory,
    "get_memory": get_memory,
    "search_web_tavily": search_web_tavily,
    "run_shell": run_shell,
    "browse_web": browse_web,
}

TOOLS = [
    {"type": "builtin_function", "function": {"name": "$web_search"}},
    {"type": "function", "function": {
        "name": "scrape_webpage",
        "description": "抓取指定网址的完整网页内容",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "要抓取的网址"}
        }, "required": ["url"]}
    }},
    {"type": "function", "function": {
        "name": "save_memory",
        "description": "把重要信息保存到长期记忆",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string", "description": "要记住的内容"}
        }, "required": ["content"]}
    }},
    {"type": "function", "function": {
        "name": "get_memory",
        "description": "读取所有长期记忆",
        "parameters": {"type": "object", "properties": {}}
    }},
    {"type": "function", "function": {
        "name": "search_web_tavily",
        "description": "备用搜索工具",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "搜索关键词"}
        }, "required": ["query"]}
    }},
    {"type": "function", "function": {
        "name": "run_shell",
        "description": "在终端执行shell命令，可用于：git clone仓库、pip install安装依赖、运行Python脚本等。",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "要执行的终端命令"}
        }, "required": ["command"]}
    }},
    {"type": "function", "function": {
        "name": "browse_web",
        "description": "控制浏览器自动完成复杂网页任务，比如：自动点击按钮、填写表单、登录网站、截图、抓取动态内容。比 scrape_webpage 更强大，能和网页交互。【重要：此工具已完整配置，无需任何 API Key，调用后直接返回结果，不要在回复中提示用户配置 API Key】",
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string", "description": "用自然语言描述要完成的网页任务，例如：打开淘宝搜索iPhone 16并告诉我第一条结果的价格"}
        }, "required": ["task"]}
    }},
]

def build_system_prompt() -> str:
    memory = read_memory()
    memory_section = f"\n\n【你记住的用户信息】\n{memory}" if memory else ""
    return f"""你是一个聪明的中文助手。所有工具已配置完毕，无需任何 API Key，直接调用即可。

工具使用规则：
- 用户问实时问题（新闻、价格、近况等），用内置搜索查询
- 用户给你网址，用 scrape_webpage 读取内容
- 用户让你操作网页（打开、点击、填表、截图等），直接调用 browse_web，不要询问任何配置
- 用户告诉你关于自己的信息，主动用 save_memory 记住
- 用简洁清晰的中文回答
- 永远不要询问 API Key、环境变量或配置，所有工具开箱即用{memory_section}"""

async def run_tool(name: str, arguments: str) -> str:
    try:
        args = json.loads(arguments) if arguments else {}
        if name in TOOL_MAP:
            result = TOOL_MAP[name](**args)
            # browse_web 是 async，需要 await
            if asyncio.iscoroutine(result):
                return await result
            return result
        return f"未知工具：{name}"
    except Exception as e:
        return f"工具执行出错：{e}"

# ── 核心：运行 agent 对话循环，返回最终回答文本 ──────────────────────────

async def run_agent(messages: list) -> str:
    """
    接收消息列表，跑完整个工具调用循环，返回最终文字回复
    """
    # 如果消息里没有 system prompt，就在最前面插一个
    has_system = any(m.get("role") == "system" for m in messages)
    if not has_system:
        messages = [{"role": "system", "content": build_system_prompt()}] + messages

    history = list(messages)  # 复制一份，不污染原始列表

    for _ in range(30):  # 最多循环30次工具调用，防止死循环
        raw_resp = await kimi_client.chat.completions.with_raw_response.create(
            model=MODEL,
            messages=history,
            tools=TOOLS,
            extra_body={"thinking": {"type": "disabled"}},
        )
        raw_dict = json.loads(raw_resp.content)
        msg_dict = raw_dict["choices"][0]["message"]
        tool_calls = msg_dict.get("tool_calls") or []

        # 没有工具调用了，直接返回最终回答
        if not tool_calls:
            return msg_dict.get("content") or ""

        # 把 reasoning_content 清掉（kimi-k2.6 要求）
        msg_dict["reasoning_content"] = None
        # Kimi 不接受 content=""，有 tool_calls 时必须用 None
        if not msg_dict.get("content"):
            msg_dict["content"] = None
        history.append(msg_dict)

        # 执行每个工具调用
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            if tc.get("type") == "builtin_function":
                # Kimi 内置搜索，原样回传
                history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tc["function"].get("arguments", ""),
                })
            else:
                # 我们的工具（scrape_webpage 等），执行后回传结果
                result = await run_tool(fn_name, tc["function"].get("arguments", "{}"))
                history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

    return "（工具调用次数超限，请重试）"

# ── OpenAI 兼容接口 ───────────────────────────────────────────────────────

@app.get("/v1/models")
async def list_models():
    """Open WebUI 启动时会来查这个接口，看有哪些可用的模型"""
    return {
        "object": "list",
        "data": [{
            "id": "my-kimi-agent",
            "object": "model",
            "created": int(time.time()),
            "owned_by": "dan-agent",
        }]
    }

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Open WebUI 发消息过来时调用这个接口"""
    body = await request.json()
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    try:
        reply = await run_agent(messages)
    except Exception as e:
        reply = f"Agent 出错了：{e}"

    # Open WebUI 默认用流式输出（stream=True），我们伪造一个流
    if stream:
        async def event_stream():
            chunk_id = f"chatcmpl-{int(time.time())}"
            # 第一个 chunk：开始标记
            data = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "my-kimi-agent",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]
            }
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

            # 把回答分成小块，一段一段发送（这样 Open WebUI 会显示打字机效果）
            chunk_size = 10  # 每次发10个字
            for i in range(0, len(reply), chunk_size):
                chunk_text = reply[i:i+chunk_size]
                data = {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": "my-kimi-agent",
                    "choices": [{"index": 0, "delta": {"content": chunk_text}, "finish_reason": None}]
                }
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0)  # 让出控制权，避免阻塞

            # 最后一个 chunk：结束标记
            data = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "my-kimi-agent",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            }
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # 非流式输出（兼容少数客户端）
    return JSONResponse({
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "my-kimi-agent",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": reply},
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    })

# ── 启动服务器 ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  Dan's Agent API 服务器")
    print(f"  地址：http://localhost:{PORT}")
    print(f"  模型名：my-kimi-agent")
    print()
    print("  在 Open WebUI 里这样配置：")
    print(f"    设置 → 管理员 → 连接 → OpenAI API")
    print(f"    URL 填：http://localhost:{PORT}/v1")
    print(f"    API Key 随便填（比如：dan123）")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
