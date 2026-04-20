#!/bin/bash
export OPENAI_API_KEY="dummy"
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1
export LANG=zh_CN.UTF-8        # 强制终端语言为中文UTF-8
export LC_ALL=zh_CN.UTF-8
cd ~/dan-agent && python3 my_agent.py
