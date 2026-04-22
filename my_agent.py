"""
我的 AI Agent - Kimi K2.6
技能：内置联网搜索、网页抓取（Firecrawl）、长期记忆、看图、历史保存、花费统计
"""

import os
import sys
import io
import re
import json
import base64
from datetime import datetime
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
HISTORY_DIR     = Path.home() / "dan-agent" / "history"
MODEL           = "kimi-k2.6"
MAX_HISTORY_ROUNDS = 20        # 最多保留最近20轮对话，防止 token 超限
# kimi-k2.6 价格（元/千token）
PRICE_INPUT     = 0.015
PRICE_OUTPUT    = 0.015

kimi_client = AsyncOpenAI(
    api_key=KIMI_API_KEY,
    base_url="https://api.moonshot.cn/v1",
)

# ============================================
# Token 花费统计（从 claw-code cost_tracker 学来的）
# ============================================
class CostTracker:
    def __init__(self):
        self.total_input = 0
        self.total_output = 0
        self.rounds = 0

    def record(self, usage: dict):
        self.total_input  += usage.get("prompt_tokens", 0)
        self.total_output += usage.get("completion_tokens", 0)
        self.rounds += 1

    def summary(self) -> str:
        cost = (self.total_input * PRICE_INPUT + self.total_output * PRICE_OUTPUT) / 1000
        return (f"本次对话共 {self.rounds} 轮 | "
                f"输入 {self.total_input} tokens + 输出 {self.total_output} tokens | "
                f"花费约 ¥{cost:.4f}")

cost_tracker = CostTracker()

# ============================================
# 对话历史保存（从 claw-code transcript 学来的）
# ============================================
def save_transcript(history: list):
    """把对话记录保存到 history/ 目录"""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".md"
    filepath = HISTORY_DIR / filename
    lines = [f"# 对话记录 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "system" or not content or not isinstance(content, str):
            continue
        label = "你" if role == "user" else "Agent"
        lines.append(f"**{label}：** {content}\n")
    filepath.write_text("\n".join(lines), encoding="utf-8")
    return filepath

# ============================================
# 历史自动裁剪（防止 token 超限）
# ============================================
def trim_history(history: list) -> list:
    """只保留 system prompt + 最近 MAX_HISTORY_ROUNDS 轮对话"""
    system = [m for m in history if m.get("role") == "system"]
    others = [m for m in history if m.get("role") != "system"]
    # 每轮大约 2 条消息（user + assistant），多留一点余量
    keep = MAX_HISTORY_ROUNDS * 3
    if len(others) > keep:
        others = others[-keep:]
        print(f"（历史已自动裁剪，只保留最近 {MAX_HISTORY_ROUNDS} 轮）")
    return system + others

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

# ============================================
# 危险命令黑名单（从 claw-code 权限系统学来的）
# ============================================
BLOCKED_COMMANDS = [
    "rm -rf", "rm -r /", "sudo rm",          # 删除系统文件
    "mkfs", "dd if=", ":(){ :|:& };:",        # 格式化/炸弹
    "chmod -R 777 /", "chown -R",             # 权限破坏
    "> /etc/passwd", "| bash", "| sh",        # 下载后直接执行脚本
    "shutdown", "reboot", "halt",             # 关机
]

def is_dangerous(command: str):
    """检查命令是否危险，返回危险原因或 None"""
    cmd_lower = command.lower().strip()
    for blocked in BLOCKED_COMMANDS:
        if blocked in cmd_lower:
            return f"包含危险操作：{blocked}"
    return None

def run_shell(command: str) -> str:
    """执行终端命令（先检查黑名单，再让用户确认）"""
    import subprocess
    # 第一关：黑名单拦截
    danger = is_dangerous(command)
    if danger:
        return f"🚫 命令被拦截，{danger}\n命令：{command}"
    # 第二关：用户确认
    print(f"\n⚠️  Agent 想执行命令：\n    {command}")
    confirm = input("确认执行吗？(y/n)：").strip().lower()
    if confirm != "y":
        return "用户取消了命令，未执行。"
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
    "run_shell": run_shell,
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
    # 执行终端命令
    {"type": "function", "function": {
        "name": "run_shell",
        "description": "在终端执行shell命令，可用于：git clone仓库、pip install安装依赖、运行Python脚本等。执行前会让用户确认。",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "要执行的终端命令，例如：git clone https://github.com/xxx/yyy"}
        }, "required": ["command"]}
    }},
]

# ============================================
# 状态栏显示
# ============================================
def get_terminal_width() -> int:
    try:
        return os.get_terminal_size().columns
    except:
        return 60

def print_divider():
    print("─" * get_terminal_width())

def _status_text() -> str:
    now = datetime.now().strftime("%H:%M")
    tokens = cost_tracker.total_input + cost_tracker.total_output
    cost = (cost_tracker.total_input * PRICE_INPUT + cost_tracker.total_output * PRICE_OUTPUT) / 1000
    return f" 🤖 {MODEL}  |  🔢 {tokens} tokens  |  💰 ¥{cost:.4f}  |  🕐 {now}"

def input_with_statusbar(prompt="你：") -> str:
    """输入框，状态栏固定显示在输入框下方"""
    width = get_terminal_width()
    divider = "─" * width
    status = _status_text()

    # 打印输入框上方横线，然后在下方预打印状态栏，再把光标移回输入行
    sys.stdout.write(f"{divider}\n\n{divider}\n{status:<{width}}\033[3A\r")
    sys.stdout.flush()

    user_input = input(prompt)

    # 用户按回车后清除下方两行状态栏，保留上方横线
    sys.stdout.write(f"\033[2K\033[1B\033[2K\033[1A\r")
    sys.stdout.flush()

    return user_input

def build_system_prompt() -> str:
    memory = read_memory()
    memory_section = f"\n\n【你记住的用户信息】\n{memory}" if memory else ""
    return f"""你是一个聪明的中文助手。

使用规则：
- 用户问实时问题（新闻、价格、近况等），用内置搜索查询
- 用户给你网址，用 scrape_webpage 读取内容
- 用户告诉你关于自己的信息，主动用 save_memory 记住
- 用户让你安装/运行 GitHub 仓库：先用 scrape_webpage 读 README，再用 run_shell 执行 git clone、pip install、python 运行等命令，每步单独调用
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
    w = 52
    lines = [
        f"  🤖  我的 AI Agent 已启动！",
        f"  模型：{MODEL}",
        f"  技能：联网搜索 · 网页抓取 · 长期记忆 · 看图",
        f"  技能：执行命令 · Token统计 · 对话保存",
        f"  输入 'q' 退出",
    ]
    def display_width(text):
        import unicodedata
        w = 0
        for c in text:
            ea = unicodedata.east_asian_width(c)
            w += 2 if ea in ('W', 'F') else 1
        return w

    def box_line(text, width):
        pad = width - display_width(text)
        return f"║{text}{' ' * max(pad, 0)}║"

    print("╔" + "═" * w + "╗")
    for line in lines:
        print(box_line(line, w))
    print("╚" + "═" * w + "╝")
    print()

    memory = read_memory()
    if memory:
        print(f"（已加载记忆：{len(memory.splitlines())} 条）\n")

    history = [{"role": "system", "content": build_system_prompt()}]

    def do_exit():
        print(f"\n{cost_tracker.summary()}")
        if cost_tracker.rounds > 0:
            saved = save_transcript(history)
            print(f"对话已保存到：{saved}")
        print("再见！")

    while True:
        try:
            user_input = input_with_statusbar("你：").strip()
        except KeyboardInterrupt:
            do_exit()
            break
        if user_input.lower() in ["q", "quit", "退出"]:
            do_exit()
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
        history = trim_history(history)  # 自动裁剪防超限
        print("Agent 思考中...")

        try:
            # 工具调用循环
            while True:
                raw_resp = await kimi_client.chat.completions.with_raw_response.create(
                    model=MODEL,
                    messages=history,
                    tools=TOOLS,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                raw_dict = json.loads(raw_resp.content)
                # 记录本次 token 花费
                if "usage" in raw_dict:
                    cost_tracker.record(raw_dict["usage"])
                msg_dict = raw_dict["choices"][0]["message"]
                tool_calls = msg_dict.get("tool_calls") or []

                # 没有工具调用，直接输出
                if not tool_calls:
                    reply = msg_dict.get("content") or ""
                    history.append({"role": "assistant", "content": reply})
                    print(f"\nAgent：{reply}\n")
                    break

                # 把 reasoning_content 设为 null（kimi-k2.6 要求）
                msg_dict["reasoning_content"] = None
                history.append(msg_dict)

                # 区分内置工具（Kimi自己执行）和外部工具（我们执行）
                for tc in tool_calls:
                    fn_name = tc["function"]["name"]
                    if tc.get("type") == "builtin_function":
                        # $web_search：把搜索结果原样回传（Kimi 用 search_id 查结果）
                        history.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": tc["function"].get("arguments", ""),
                        })
                    else:
                        # 外部工具（scrape_webpage/save_memory 等）：我们执行并回传
                        result = run_tool(fn_name, tc["function"].get("arguments", "{}"))
                        history.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        })

        except Exception as e:
            msg_str = str(e).encode('utf-8', errors='replace').decode('utf-8')
            print(f"\n出错了：{msg_str}\n")
            history.pop()  # 移除出错的用户消息，避免历史污染

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(chat())
    except KeyboardInterrupt:
        print("\n\n已退出，再见！")
