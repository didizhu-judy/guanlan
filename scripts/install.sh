#!/usr/bin/env bash
# ============================================================
# 观澜 Guānlán · 容错型安装脚本
# ------------------------------------------------------------
# - 每个可选步骤都能跳过；跳过 = 对应功能缺失，其余正常
# - 不会偷偷覆盖已有的 .env / 已有的 venv
# - 跑完会打印一份「绿勾清单」，让你心里有数
# ============================================================
set -u

# ---- 颜色 ----
if [[ -t 1 ]]; then
  C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
  C_GREEN=$'\033[32m'; C_RED=$'\033[31m'; C_YEL=$'\033[33m'; C_CYAN=$'\033[36m'
else
  C_BOLD=""; C_DIM=""; C_OFF=""; C_GREEN=""; C_RED=""; C_YEL=""; C_CYAN=""
fi

ok()   { echo "${C_GREEN}✓${C_OFF} $*"; }
warn() { echo "${C_YEL}!${C_OFF} $*"; }
err()  { echo "${C_RED}✗${C_OFF} $*"; }
step() { echo; echo "${C_BOLD}${C_CYAN}── $* ──${C_OFF}"; }
dim()  { echo "${C_DIM}$*${C_OFF}"; }

ask_yn() {
  # ask_yn "问句" "y"  → 默认 y
  local prompt="$1" default="${2:-y}" reply
  read -r -p "$prompt [${default}/$( [[ $default == y ]] && echo n || echo y )]: " reply || reply="$default"
  reply="${reply:-$default}"
  [[ "${reply,,}" == "y" || "${reply,,}" == "yes" ]]
}

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

echo
echo "${C_BOLD}观澜 Guānlán · 安装向导${C_OFF}"
dim   "  半亩方塘一鉴开，天光云影共徘徊。"
echo

# ---- 状态记录 ----
PYTHON_OK="no"; VENV_OK="no"; DEPS_OK="no"
ENV_OK="no"; FINNHUB_OK="skipped"; CODEX_OK="skipped"
WIDGET_OK="skipped"

# =====================================================================
step "1 / 5 · 检查 Python"
# =====================================================================
PY=""
for p in python3.12 python3.11 python3.10 python3; do
  if command -v "$p" >/dev/null 2>&1; then PY="$p"; break; fi
done

if [[ -z "$PY" ]]; then
  err "未找到 Python 3。请到 https://www.python.org 装 3.11+ 再重跑。"
  exit 1
fi
PY_VER="$($PY --version 2>&1 | awk '{print $2}')"
PY_MAJ="${PY_VER%%.*}"; PY_MIN="$(echo "$PY_VER" | cut -d. -f2)"
if (( PY_MAJ < 3 || (PY_MAJ == 3 && PY_MIN < 9) )); then
  err "需要 Python ≥ 3.9，当前 $PY_VER。请升级。"
  exit 1
fi
ok "Python $PY_VER ($PY)"
PYTHON_OK="yes"

# =====================================================================
step "2 / 5 · 建立虚拟环境 + 装依赖"
# =====================================================================
if [[ -d .venv ]]; then
  ok "已有 .venv，跳过创建"
else
  $PY -m venv .venv || { err "venv 创建失败"; exit 1; }
  ok "已创建 .venv"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
VENV_OK="yes"

dim "装核心依赖…"
pip install --upgrade pip >/dev/null 2>&1 || true
if pip install -r requirements.txt >/tmp/guanlan-pip.log 2>&1; then
  ok "核心依赖安装完成"
else
  err "核心依赖安装失败，日志见 /tmp/guanlan-pip.log"
  exit 1
fi

dim "装 PyObjC（菜单栏 + 桌面挂件用）…"
if pip install "pyobjc-core<11" "pyobjc-framework-Cocoa<11" "pyobjc-framework-WebKit<11" \
     --only-binary :all: >>/tmp/guanlan-pip.log 2>&1; then
  ok "PyObjC 安装完成"
  DEPS_OK="yes"
else
  warn "PyObjC 安装失败 —— 菜单栏 / 桌面挂件将不可用。Safari PWA 仍可正常使用。"
  DEPS_OK="partial"
fi

# =====================================================================
step "3 / 5 · 配置 Trading 212 凭据（必需）"
# =====================================================================
if [[ -f .env ]] && grep -q '^TRADING212_API_KEY_ID=..' .env; then
  ok "已发现 .env 且包含 T212 凭据，跳过"
  ENV_OK="yes"
else
  if [[ ! -f .env ]]; then
    cp .env.example .env
    dim "已用 .env.example 生成 .env"
  fi
  echo
  echo "请到 ${C_CYAN}https://www.trading212.com${C_OFF} → Settings → API → Generate new key"
  echo "  ${C_BOLD}只勾选只读权限${C_OFF}：account.read / portfolio.read / history.read"
  echo "  不要勾 orders.execute —— 观澜不会下单。"
  echo
  read -r -p "Trading 212 Key ID: " KID
  read -r -p "Trading 212 Secret: " KSEC
  read -r -p "环境 [live/demo] (默认 live): " KENV
  KENV="${KENV:-live}"
  if [[ -n "$KID" && -n "$KSEC" ]]; then
    # 安全地写入（覆盖已有行）
    $PY - <<PY
import re, pathlib
p = pathlib.Path(".env")
txt = p.read_text() if p.exists() else ""
def set_kv(k, v):
    global txt
    pat = re.compile(rf'^{k}=.*$', re.M)
    if pat.search(txt):
        txt = pat.sub(f'{k}={v}', txt)
    else:
        txt += f'\n{k}={v}\n'
set_kv("TRADING212_API_KEY_ID", "$KID")
set_kv("TRADING212_API_SECRET", "$KSEC")
set_kv("TRADING212_ENV", "$KENV")
p.write_text(txt)
PY
    ok "已写入 .env"
    ENV_OK="yes"
  else
    err "未填 T212 凭据 —— 观澜将没有数据。"
    err "请稍后手动编辑 .env 再启动。"
    ENV_OK="missing"
  fi
fi

# =====================================================================
step "4 / 5 · 可选：Finnhub（消息面 + 单股报价）"
# =====================================================================
if grep -q '^FINNHUB_API_KEY=.\+$' .env 2>/dev/null; then
  ok "已配置 FINNHUB_API_KEY"
  FINNHUB_OK="yes"
elif ask_yn "现在配置 Finnhub？(可跳过，消息面 Tab 会显示「未配置」)"; then
  echo "注册地址：${C_CYAN}https://finnhub.io${C_OFF} （免费 60 calls/min）"
  read -r -p "Finnhub API Key: " FKEY
  if [[ -n "$FKEY" ]]; then
    $PY - <<PY
import re, pathlib
p = pathlib.Path(".env"); txt = p.read_text()
pat = re.compile(r'^FINNHUB_API_KEY=.*$', re.M)
if pat.search(txt): txt = pat.sub(f'FINNHUB_API_KEY=$FKEY', txt)
else: txt += f'\nFINNHUB_API_KEY=$FKEY\n'
p.write_text(txt)
PY
    ok "已写入 FINNHUB_API_KEY"
    FINNHUB_OK="yes"
  else
    warn "未填 —— 消息面功能将禁用，其余正常"
  fi
else
  warn "跳过 Finnhub —— 消息面 Tab 将显示「未配置」"
fi

# =====================================================================
step "5 / 5 · 可选：Codex CLI（AI 分析）"
# =====================================================================
if command -v codex >/dev/null 2>&1; then
  ok "已安装 Codex CLI ($(codex --version 2>&1 | head -1))"
  CODEX_OK="yes"
elif ask_yn "现在装 Codex CLI？(可跳过，AI 聊天框将灰显)"; then
  if command -v brew >/dev/null 2>&1; then
    brew install codex && ok "Codex 安装完成" && CODEX_OK="yes" || warn "brew 装 codex 失败 —— 可稍后手动装"
  else
    warn "未发现 brew。请到 https://github.com/openai/codex 按指引手动安装。"
  fi
  if [[ "$CODEX_OK" == "yes" ]]; then
    dim "记得跑一次 'codex login' 完成 OAuth"
  fi
else
  warn "跳过 Codex —— AI 聊天 / 技术面快读功能将禁用"
fi

# =====================================================================
# 总结
# =====================================================================
echo
echo "${C_BOLD}── 安装完成 · 清单 ──${C_OFF}"
fmt_row() { printf "  %-22s %s\n" "$1" "$2"; }
case "$PYTHON_OK"  in yes) fmt_row "Python"          "${C_GREEN}✓${C_OFF}";;        *) fmt_row "Python"          "${C_RED}✗${C_OFF}";;        esac
case "$VENV_OK"    in yes) fmt_row "虚拟环境"        "${C_GREEN}✓${C_OFF}";;        *) fmt_row "虚拟环境"        "${C_RED}✗${C_OFF}";;        esac
case "$DEPS_OK"    in yes) fmt_row "依赖"            "${C_GREEN}✓ 含 PyObjC${C_OFF}";; partial) fmt_row "依赖" "${C_YEL}✓ 但无 PyObjC${C_OFF}";; *) fmt_row "依赖" "${C_RED}✗${C_OFF}";; esac
case "$ENV_OK"     in yes) fmt_row "Trading 212"     "${C_GREEN}✓${C_OFF}";;        missing) fmt_row "Trading 212" "${C_RED}✗ 未配置${C_OFF}";; *) fmt_row "Trading 212" "${C_RED}✗${C_OFF}";; esac
case "$FINNHUB_OK" in yes) fmt_row "Finnhub"         "${C_GREEN}✓${C_OFF}";;        *)   fmt_row "Finnhub"        "${C_DIM}— 跳过（消息面禁用）${C_OFF}";; esac
case "$CODEX_OK"   in yes) fmt_row "Codex CLI"       "${C_GREEN}✓${C_OFF}";;        *)   fmt_row "Codex CLI"      "${C_DIM}— 跳过（AI 禁用）${C_OFF}";;   esac

echo
if [[ "$ENV_OK" == "yes" ]]; then
  echo "${C_GREEN}${C_BOLD}观澜已就绪。${C_OFF}"
  echo "  下一步运行：${C_BOLD}./scripts/start.sh${C_OFF}"
  echo "  或直接双击 ${C_BOLD}Guanlan.app${C_OFF}"
else
  echo "${C_YEL}${C_BOLD}观澜未完整就绪（T212 凭据缺失）。${C_OFF}"
  echo "  请编辑 ${C_BOLD}.env${C_OFF} 后再启动。"
fi
echo
