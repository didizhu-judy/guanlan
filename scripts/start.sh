#!/usr/bin/env bash
# ============================================================
# 观澜 Guānlán · 启动脚本（开发模式 / 命令行模式）
# ------------------------------------------------------------
# 普通用户：双击 Guanlan.app
# 开发者：跑这个脚本，可见日志、Ctrl-C 即停
# ============================================================
set -u

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

PORT="${GUANLAN_PORT:-8787}"

# ---- 颜色 ----
if [[ -t 1 ]]; then
  C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
  C_GREEN=$'\033[32m'; C_YEL=$'\033[33m'; C_CYAN=$'\033[36m'; C_DIM=$'\033[2m'
else
  C_BOLD=""; C_OFF=""; C_GREEN=""; C_YEL=""; C_CYAN=""; C_DIM=""
fi

# ---- 环境检查 ----
if [[ ! -d .venv ]]; then
  echo "${C_YEL}未发现 .venv —— 请先跑 ./scripts/install.sh${C_OFF}"
  exit 1
fi
if [[ ! -f .env ]]; then
  echo "${C_YEL}未发现 .env —— 请先跑 ./scripts/install.sh${C_OFF}"
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# ---- 已有实例？----
if lsof -i ":$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "${C_GREEN}后端已在 :$PORT 上运行 —— 直接打开浏览器。${C_OFF}"
  open "http://localhost:$PORT"
  exit 0
fi

# ---- 启动后端 ----
echo
echo "${C_BOLD}观澜 · 启动中${C_OFF}"
echo "${C_DIM}  端口 $PORT · Ctrl-C 退出${C_OFF}"
echo

# 等服务起来再开浏览器
(
  for i in $(seq 1 60); do
    if curl -sf "http://localhost:$PORT/api/snapshot" >/dev/null 2>&1; then
      echo "${C_GREEN}✓ 后端就绪${C_OFF}"
      open "http://localhost:$PORT"
      exit 0
    fi
    sleep 0.5
  done
  echo "${C_YEL}后端启动超时（30s）—— 仍可手动访问 http://localhost:$PORT${C_OFF}"
) &

exec python3 server.py
