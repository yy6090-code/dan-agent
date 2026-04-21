"""
我的 AI Agent - Kimi K2.6
技能：内置联网搜索、网页抓取（Firecrawl）、长期记忆、看图
"""

import os
import sys
import io
import re
import json
import base64
from pathlib import Path

# 解决终端中文乱码
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
if hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")
from openai import AsyncOpenAI

# 修复 httpx 不支持非 ASCII 请求头的问题
import httpx._models as _httpx_models
def _utf8_normalize(value, encoding=None):
    if isinstance(value, bytes):
        return value
    try:
        return value.encode(encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        return value.encode("utf-8")
_httpx_models._normalize_header_value = _utf8_normalize

# ============================================
# 配置
# ============================================
KIMI_API_KEY    = os.environ.get("MOONSHOT_API_KEY", "")
TAVILY_API_KEY  = os.environ.get("TAVILY_API_KEY", "")
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")
MEMORY_FILE     = Path.home() / "dan-agent" / "memory.md"
MODEL           = "kimi-k2.6"

kimi_client = AsyncOpenAI(
    api_key=KIMI_API_KEY,
    base_url="https://api.moonshot.cn/v1",
)

# ============================================
# 记忆读写
# ============================================
def read_memory() -> str:
    return MEMORY_FILE.read_text(encoding="utf-8").strip() if MEMORY_FILE.exists() else ""

def write_memory(content: str):
    MEMORY_FILE.write_text(content, encoding="utf-8")

# ============================================
# 工具函数定义（给 Kimi 调用）
# ============================================
def scrape_webpage(url: str) -> str:
    """抓取网页内容"""
    from firecrawl import FirecrawlApp
    if not FIRECRAWL_API_KEY:
        return "错误：未设置 FIRECRAWL_API_KEY"
    app = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
    result = app.scrape(url, formats=["markdown"])
    content = result.markdown if hasattr(result, "markdown") else result.get("markdown", "")
    return content[:5000] if content else "网页内容为空"

def save_memory(content: str) -> str:
    """保存长期记忆"""
    existing = read_memory()
    write_memory((existing + "\n- " + content).strip())
    return f"已记住：{content}"

def get_memory() -> str:
    """读取长期记忆"""
    memory = read_memory()
    return memory if memory else "暂无长期记忆"

def search_web_tavily(query: str) -> str:
    """Tavily 备用搜索"""
    from tavily import TavilyClient
    if not TAVILY_API_KEY:
        return "错误：未设置 TAVILY_API_KEY"
    client = TavilyClient(api_key=TAVILY_API_KEY)
    result = client.search(query=query, max_results=3)
    output = []
    for item in result.get("results", []):
        output.append(f"标题：{item.get('title','')[:100]}\n摘要：{item.get('content','')[:300]}\n链接：{item.get('url','')}")
    return "\n---\n".join(output) if output else "没有找到相关结果"

# 工具分发表
TOOL_MAP = {
    "scrape_webpage": scrape_webpage,
    "save_memory": save_memory,
    "get_memory": get_memory,
    "search_web_tavily": search_web_tavily,
}

# ============================================
# 传给 Kimi 的工具列表
# ============================================
TOOLS = [
    # Kimi 内置搜索（优先）
    {"type": "builtin_function", "function": {"name": "$web_search"}},
    # 网页抓取
    {"type": "function", "function": {
        "name": "scrape_webpage",
        "description": "抓取指定网址的完整网页内容",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "要抓取的网址"}
        }, "required": ["url"]}
    }},
    # 保存记忆
    {"type": "function", "function": {
        "name": "save_memory",
        "description": "把重要信息保存到长期记忆，下次启动也能记住",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string", "description": "要记住的内容"}
        }, "required": ["content"]}
    }},
    # 读取记忆
    {"type": "function", "function": {
        "name": "get_memory",
        "description": "读取所有长期记忆",
        "parameters": {"type": "object", "properties": {}}
    }},
    # Tavily 备用搜索
    {"type": "function", "function": {
        "name": "search_web_tavily",
        "description": "备用搜索工具（当内置搜索不够用时使用）",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "搜索关键词"}
        }, "required": ["query"]}
    }},
]

def build_system_prompt() -> str:
    memory = read_memory()
    memory_section = f"\n\n【你记住的用户信息】\n{memory}" if memory else ""
    return f"""你是一个聪明的中文助手。

使用规则：
- 用户问实时问题（新闻、价格、近况等），用内置搜索查询
- 用户给你网址，用 scrape_webpage 读取内容
- 用户告诉你关于自己的信息，主动用 save_memory 记住
- 用简洁清晰的中文回答{memory_section}"""

# ============================================
# 执行工具调用
# ============================================
def run_tool(name: str, arguments: str) -> str:
    try:
        args = json.loads(arguments) if arguments else {}
        if name in TOOL_MAP:
            return TOOL_MAP[name](**args)
        return f"未知工具：{name}"
    except Exception as e:
        return f"工具执行出错：{e}"

# ============================================
# 主对话循环
# ============================================
async def chat():
    print("=================================")
    print("  我的 AI Agent 已启动！")
    print("  技能：Kimi内置搜索 + 网页抓取 + 长期记忆 + 看图")
    print("  输入 'q' 退出")
    print("=================================\n")

    memory = read_memory()
    if memory:
        print(f"（已加载记忆：{len(memory.splitlines())} 条）\n")

    history = [{"role": "system", "content": build_system_prompt()}]

    while True:
        user_input = input("你：").strip()
        if user_input.lower() in ["q", "quit", "退出"]:
            print("再见！")
            break
        if not user_input:
            continue

        # 检测图片路径
        image_paths = re.findall(r'(/[^\s]+\.(?:png|jpg|jpeg|gif|webp))', user_input, re.IGNORECASE)
        if image_paths and Path(image_paths[0]).exists():
            img_path = image_paths[0]
            img_data = base64.b64encode(Path(img_path).read_bytes()).decode()
            ext = img_path.split('.')[-1].lower()
            mime = f"image/{ext}" if ext != 'jpg' else "image/jpeg"
            text_part = user_input.replace(img_path, "[图片]")
            user_msg = {"role": "user", "content": [
                {"type": "text", "text": text_part},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_data}"}}
            ]}
        else:
            user_msg = {"role": "user", "content": user_input}

        history.append(user_msg)
        print("Agent 思考中...")

        try:
            # 工具调用循环
            while True:
                resp = await kimi_client.chat.completions.create(
                    model=MODEL,
                    messages=history,
                    tools=TOOLS,
                )
                msg = resp.choices[0].message

                # 没有工具调用，直接输出
                if not msg.tool_calls:
                    reply = msg.content or ""
                    history.append({"role": "assistant", "content": reply})
                    print(f"\nAgent：{reply}\n")
                    break

                # 有工具调用，执行并把结果塞回历史
                # 用 model_dump 拿完整原始字典（含 reasoning_content）
                raw = resp.model_dump()
                msg_dict = raw["choices"][0]["message"]
                history.append(msg_dict)
                for tc in msg.tool_calls:
                    result = run_tool(tc.function.name, tc.function.arguments)
                    history.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

        except Exception as e:
            msg_str = str(e).encode('utf-8', errors='replace').decode('utf-8')
            print(f"\n出错了：{msg_str}\n")
            history.pop()  # 移除出错的用户消息，避免历史污染

if __name__ == "__main__":
    import asyncio
    asyncio.run(chat())
