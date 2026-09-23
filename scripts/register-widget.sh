#!/usr/bin/env bash
# ============================================================
# 观澜 Guānlán · 让 macOS 通知中心发现 WidgetKit 组件
# ------------------------------------------------------------
# 把 Guanlan-Widget.app 注册到 LaunchServices + pluginkit。
# 用户在装好 .app 之后跑一次即可。
# ============================================================
set -u

WIDGET_APP="${1:-/Applications/Guanlan-Widget.app}"

if [[ -t 1 ]]; then
  C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
  C_GREEN=$'\033[32m'; C_YEL=$'\033[33m'; C_CYAN=$'\033[36m'
else
  C_BOLD=""; C_OFF=""; C_GREEN=""; C_YEL=""; C_CYAN=""
fi

echo
echo "${C_BOLD}观澜 · 注册通知中心组件${C_OFF}"
echo

if [[ ! -d "$WIDGET_APP" ]]; then
  echo "${C_YEL}未发现 $WIDGET_APP${C_OFF}"
  echo "  请先把 Guanlan-Widget.app 拖到 /Applications，或传路径作为参数："
  echo "  $0 /path/to/Guanlan-Widget.app"
  exit 1
fi

LS=/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Support/lsregister

echo "→ 注册到 LaunchServices"
"$LS" -f "$WIDGET_APP" >/dev/null 2>&1 && echo "  ${C_GREEN}✓${C_OFF}" || echo "  (skipped)"

EXT_PATH="$WIDGET_APP/Contents/PlugIns/T212WidgetExtension.appex"
if [[ -d "$EXT_PATH" ]]; then
  echo "→ 注册 widget extension"
  pluginkit -a "$EXT_PATH" >/dev/null 2>&1 && echo "  ${C_GREEN}✓${C_OFF}" || echo "  (skipped)"
else
  echo "  ${C_YEL}!${C_OFF} 未找到 T212WidgetExtension.appex —— 是不是构建出错？"
fi

echo "→ 打开主 App 让系统发现组件"
open "$WIDGET_APP"

echo
echo "${C_GREEN}完成。${C_OFF}30 秒后到「通知中心 → 编辑小组件 → 搜索 ${C_BOLD}观澜${C_OFF}」即可添加。"
echo "如果还是搜不到，可以试 ${C_CYAN}killall chronod${C_OFF}（注意会重启所有人的 widget）。"
echo
