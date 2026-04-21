"""
我的第一个 AI Agent - 使用 Kimi API
技能：联网搜索（Tavily）、网页抓取（Firecrawl）、长期记忆
"""

import os
import sys
import io
import re
import base64
from pathlib import Path

# 解决终端中文乱码
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
if hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

from agents import Agent, Runner, OpenAIChatCompletionsModel, set_tracing_disabled, function_tool
from openai import AsyncOpenAI

set_tracing_disabled(True)

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
# 配置 API Keys
# ============================================
KIMI_API_KEY = os.environ.get("MOONSHOT_API_KEY", "")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")

# 记忆文件路径（单独存在 dan-agent 目录里）
MEMORY_FILE = Path.home() / "dan-agent" / "memory.md"

# ============================================
# 记忆读写函数
# ============================================
def read_memory() -> str:
    if MEMORY_FILE.exists():
        return MEMORY_FILE.read_text(encoding="utf-8").strip()
    return ""

def write_memory(content: str):
    MEMORY_FILE.write_text(content, encoding="utf-8")

# 创建 Kimi 客户端
kimi_client = AsyncOpenAI(
    api_key=KIMI_API_KEY,
    base_url="https://api.moonshot.cn/v1",
)

# ============================================
# 技能一：联网搜索（Tavily）
# ============================================
@function_tool
def search_web(query: str) -> str:
    """在互联网上搜索最新信息，返回搜索结果摘要"""
    from tavily import TavilyClient
    if not TAVILY_API_KEY:
        return "错误：未设置 TAVILY_API_KEY 环境变量"
    client = TavilyClient(api_key=TAVILY_API_KEY)
    result = client.search(query=query, max_results=3)
    output = []
    for item in result.get("results", []):
        title = item.get("title", "")[:100]
        content = item.get("content", "")[:300]
        url = item.get("url", "")
        output.append(f"标题：{title}\n摘要：{content}\n链接：{url}")
    return "\n---\n".join(output) if output else "没有找到相关结果"

# ============================================
# 技能二：抓取网页内容（Firecrawl）
# ============================================
@function_tool
def scrape_webpage(url: str) -> str:
    """抓取指定网址的网页内容，返回页面文字"""
    from firecrawl import FirecrawlApp
    if not FIRECRAWL_API_KEY:
        return "错误：未设置 FIRECRAWL_API_KEY 环境变量"
    app = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
    result = app.scrape(url, formats=["markdown"])
    content = result.markdown if hasattr(result, "markdown") else result.get("markdown", "")
    return content[:5000] if content else "网页内容为空"

# ============================================
# 技能三：保存长期记忆
# ============================================
@function_tool
def save_memory(content: str) -> str:
    """把重要信息保存到长期记忆，下次启动也能记住"""
    existing = read_memory()
    new_memory = (existing + "\n- " + content).strip()
    write_memory(new_memory)
    return f"已记住：{content}"

@function_tool
def get_memory() -> str:
    """读取所有长期记忆"""
    memory = read_memory()
    return memory if memory else "暂无长期记忆"

# ============================================
# 启动时读取记忆，注入到 Agent 背景知识
# ============================================
def build_instructions() -> str:
    memory = read_memory()
    memory_section = f"\n\n【你记住的用户信息】\n{memory}" if memory else ""
    return f"""你是一个聪明的中文助手，拥有以下技能：
    - search_web：搜索互联网上的实时信息
    - scrape_webpage：抓取并阅读某个网页的内容
    - save_memory：把重要信息保存到长期记忆
    - get_memory：读取所有长期记忆

    使用规则（必须严格遵守）：
    - 用户问实时问题（新闻、价格、近况等），必须调用 search_web
    - 用户消息中出现 http 或 https 开头的网址，必须调用 scrape_webpage
    - 用户告诉你关于他自己的信息（名字、爱好、目标等），主动调用 save_memory 记住
    - 用简洁清晰的中文回答{memory_section}"""

# ============================================
# 运行 Agent（对话循环）
# ============================================
async def chat():
    print("=================================")
    print("  我的 AI Agent 已启动！")
    print("  技能：联网搜索 + 网页抓取 + 长期记忆")
    print("  输入 'q' 退出")
    print("=================================\n")

    # 每次启动重新读记忆，构建 Agent
    agent = Agent(
        name="我的助手",
        instructions=build_instructions(),
        model=OpenAIChatCompletionsModel(
            model="kimi-k2.6",
            openai_client=kimi_client,
        ),
        tools=[search_web, scrape_webpage, save_memory, get_memory],
    )

    memory = read_memory()
    if memory:
        print(f"（已加载记忆：{len(memory.splitlines())} 条）\n")

    history = []

    while True:
        user_input = input("你：").strip()
        if user_input.lower() in ["q", "quit", "退出"]:
            print("再见！")
            break
        if not user_input:
            continue

        print("Agent 思考中...")
        try:
            # 检测输入里有没有图片路径
            image_paths = re.findall(r'(/[^\s]+\.(?:png|jpg|jpeg|gif|webp))', user_input, re.IGNORECASE)
            if image_paths and Path(image_paths[0]).exists():
                # 图片消息：直接调 Kimi API（绕过 agents SDK）
                img_path = image_paths[0]
                img_data = base64.b64encode(Path(img_path).read_bytes()).decode()
                ext = img_path.split('.')[-1].lower()
                mime = f"image/{ext}" if ext != 'jpg' else "image/jpeg"
                text_part = user_input.replace(img_path, "[图片]")

                # 把之前的文字历史 + 本次图文消息一起发
                text_history = [m for m in history if isinstance(m.get("content"), str)]
                messages = text_history + [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text_part},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_data}"}}
                    ]
                }]
                resp = await kimi_client.chat.completions.create(
                    model="kimi-k2.6",
                    messages=messages,
                )
                reply = resp.choices[0].message.content
                # 把这轮对话加入历史（文字形式）
                history.append({"role": "user", "content": f"{text_part}（含图片）"})
                history.append({"role": "assistant", "content": reply})
                print(f"\nAgent：{reply}\n")
            else:
                result = await Runner.run(agent, history + [{"role": "user", "content": user_input}])
                history = result.to_input_list()
                print(f"\nAgent：{result.final_output}\n")
        except Exception as e:
            msg = str(e).encode('utf-8', errors='replace').decode('utf-8')
            print(f"\n出错了：{msg}\n")

if __name__ == "__main__":
    import asyncio
    asyncio.run(chat())
