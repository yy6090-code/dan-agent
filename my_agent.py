"""
我的第一个 AI Agent - 使用 Kimi API
技能：联网搜索（Tavily）、网页抓取（Firecrawl）
"""

import os
import sys
import io

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
_orig_normalize = _httpx_models._normalize_header_value
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

# 创建 Kimi 客户端
kimi_client = AsyncOpenAI(
    api_key=KIMI_API_KEY,
    base_url="https://api.moonshot.cn/v1",
)

# ============================================
# 技能一：联网搜索（Tavily）
# 用法：搜索实时新闻、价格、任何问题
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
        content = item.get("content", "")[:300]  # 每条只取300字
        url = item.get("url", "")
        output.append(f"标题：{title}\n摘要：{content}\n链接：{url}")
    return "\n---\n".join(output) if output else "没有找到相关结果"

# ============================================
# 技能二：抓取网页内容（Firecrawl）
# 用法：读取某个网址的完整内容
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
# 创建 Agent（带技能）
# ============================================
agent = Agent(
    name="我的助手",
    instructions="""
    你是一个聪明的中文助手，拥有以下技能：
    - search_web：搜索互联网上的实时信息
    - scrape_webpage：抓取并阅读某个网页的内容

    使用规则（必须严格遵守）：
    - 用户问实时问题（新闻、价格、近况等），必须调用 search_web，不能凭记忆回答
    - 用户消息中出现任何 http 或 https 开头的网址，必须调用 scrape_webpage 读取内容，不能猜测或编造
    - 用 scrape_webpage 读取后，把页面的具体内容总结给用户，不能说"页面不存在"或"无法访问"
    - 如果工具返回了内容，就基于该内容回答，不要说自己不知道
    - 用简洁清晰的中文回答
    """,
    model=OpenAIChatCompletionsModel(
        model="moonshot-v1-32k",
        openai_client=kimi_client,
    ),
    tools=[search_web, scrape_webpage],  # 挂载技能
)

# ============================================
# 运行 Agent（对话循环）
# ============================================
async def chat():
    print("=================================")
    print("  我的 AI Agent 已启动！")
    print("  技能：联网搜索 + 网页抓取")
    print("  输入 'q' 退出")
    print("=================================\n")

    while True:
        user_input = input("你：").strip()
        if user_input.lower() in ["q", "quit", "退出"]:
            print("再见！")
            break
        if not user_input:
            continue

        print("Agent 思考中...")
        try:
            result = await Runner.run(agent, user_input)
            print(f"\nAgent：{result.final_output}\n")
        except Exception as e:
            msg = str(e).encode('utf-8', errors='replace').decode('utf-8')
            print(f"\n出错了：{msg}\n")

if __name__ == "__main__":
    import asyncio
    asyncio.run(chat())
