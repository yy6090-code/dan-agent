#!/bin/bash
# 启动 Agent API 服务器（Open WebUI 专用）
# 用法：bash ~/dan-agent/server.sh

# 加载 .zshrc 里的 API Key 环境变量
source ~/.zshrc 2>/dev/null || true

# 确认 Key 已加载
if [ -z "$MOONSHOT_API_KEY" ]; then
    echo "❌ 错误：MOONSHOT_API_KEY 未设置，请检查 ~/.zshrc"
    exit 1
fi

echo "✅ MOONSHOT_API_KEY 已加载"
echo "🚀 正在用 python3.11 启动服务器..."

# 必须用 python3.11，因为 browser_use 装在这里
cd ~/dan-agent && python3.11 agent_server.py
