#!/usr/bin/env bash
# ============================================================
# 观澜 Guānlán · 构建发布 DMG
# ------------------------------------------------------------
# 把 4 个 .app + source/ + 文档 + 命令文件 装进一只 DMG。
# 依赖：
#   brew install create-dmg
# 用法：
#   ./scripts/build-dmg.sh [版本号]   # 默认 v0.1
# ============================================================
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

VERSION="${1:-v0.1}"
VOLUME_NAME="观澜 $VERSION"
OUT_DMG="$HERE/release/观澜-$VERSION.dmg"
STAGE="$HERE/release/.stage-$VERSION"

if [[ -t 1 ]]; then
  C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
  C_GREEN=$'\033[32m'; C_YEL=$'\033[33m'; C_CYAN=$'\033[36m'; C_RED=$'\033[31m'
else
  C_BOLD=""; C_OFF=""; C_GREEN=""; C_YEL=""; C_CYAN=""; C_RED=""
fi

echo
echo "${C_BOLD}观澜 · 构建 DMG ($VERSION)${C_OFF}"
echo

# ---- 0. 依赖检查 ----
if ! command -v create-dmg >/dev/null 2>&1; then
  echo "${C_RED}✗${C_OFF} 缺 create-dmg：brew install create-dmg"
  exit 1
fi

# ---- 1. 清理上次产物 ----
rm -rf "$STAGE" "$OUT_DMG"
mkdir -p "$STAGE"

# ---- 2. 准备 4 个 .app ----
echo "${C_CYAN}→${C_OFF} 收集 .app 包"
APPS=("Guanlan.app" "Guanlan-Menubar.app" "Guanlan-Desktop.app")
for a in "${APPS[@]}"; do
  if [[ -d "$HERE/$a" ]]; then
    cp -R "$HERE/$a" "$STAGE/" && echo "  ✓ $a"
  else
    echo "  ${C_YEL}!${C_OFF} 缺 $a —— 用户装好后跑 install.sh 时会重建（但 DMG 会少一个）"
  fi
done

# WidgetKit host 是另算的（要 Xcode 构建过才会有）
WIDGET_BUILD="$HERE/widget-kit/build/Build/Products/Release/Guanlan-Widget.app"
ALT_WIDGET="/Applications/Guanlan-Widget.app"
if [[ -d "$WIDGET_BUILD" ]]; then
  cp -R "$WIDGET_BUILD" "$STAGE/" && echo "  ✓ Guanlan-Widget.app (from build/)"
elif [[ -d "$ALT_WIDGET" ]]; then
  cp -R "$ALT_WIDGET" "$STAGE/" && echo "  ✓ Guanlan-Widget.app (from /Applications)"
else
  echo "  ${C_YEL}!${C_OFF} 没找到 Guanlan-Widget.app —— DMG 里将没有 WidgetKit 组件"
  echo "    （这没关系，用户可以从源码自行构建；或在 widget-kit/ 跑 xcodebuild 再重打 DMG）"
fi

# ---- 3. 拷源码（去掉本机状态 / 凭据 / 备份） ----
echo "${C_CYAN}→${C_OFF} 打包源码到 source/"
SRC="$STAGE/source"
mkdir -p "$SRC"
rsync -a \
  --exclude '.env' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'daily_totals.json' \
  --exclude 'transactions_cache.json' \
  --exclude 'price_log.json' \
  --exclude 'alert_queue.json' \
  --exclude '.desktop_widget_pos.json' \
  --exclude '.DS_Store' \
  --exclude 'release/' \
  --exclude 'dmg/' \
  --exclude '*_v1_*' \
  --exclude 'T212-Desktop_v1_*.app' \
  --exclude 'icon.iconset' \
  --exclude 'widget-kit/build' \
  --exclude 'widget-kit/DerivedData' \
  --exclude 'widget-kit/*.xcodeproj/xcuserdata' \
  --exclude 'Guanlan.app' --exclude 'Guanlan-Menubar.app' --exclude 'Guanlan-Desktop.app' \
  "$HERE/" "$SRC/"
echo "  ✓ source/ 完成"

# ---- 4. 文档 ----
echo "${C_CYAN}→${C_OFF} 复制文档"
cp "$HERE/README.md"   "$STAGE/README.md"   && echo "  ✓ README.md"
cp "$HERE/SETUP.md"    "$STAGE/SETUP.md"    && echo "  ✓ SETUP.md"
cp "$HERE/dmg/请先读我.txt" "$STAGE/请先读我.txt" && echo "  ✓ 请先读我.txt"

# ---- 5. 命令文件 ----
echo "${C_CYAN}→${C_OFF} 复制安装命令"
cp -p "$HERE/dmg/commands/用 Claude Code 自动安装.command" "$STAGE/" && echo "  ✓ Claude Code"
cp -p "$HERE/dmg/commands/用 Codex 自动安装.command"      "$STAGE/" && echo "  ✓ Codex"
cp -p "$HERE/dmg/commands/手动安装.command"                "$STAGE/" && echo "  ✓ 手动"
chmod +x "$STAGE"/*.command

# 移除 xattr (com.apple.quarantine 会让 Gatekeeper 弹警告)
xattr -cr "$STAGE" 2>/dev/null || true

# ---- 6. 构 DMG ----
echo
echo "${C_CYAN}→${C_OFF} 构建 DMG ($OUT_DMG)"
ICON_ARG=()
if [[ -f "$HERE/icon.icns" ]]; then
  ICON_ARG=(--volicon "$HERE/icon.icns")
fi

# create-dmg 会自动加 Applications 符号链接（--app-drop-link）
create-dmg \
  --volname "$VOLUME_NAME" \
  "${ICON_ARG[@]}" \
  --window-pos 200 120 \
  --window-size 720 480 \
  --icon-size 96 \
  --icon "Guanlan.app"          120 180 \
  --icon "Guanlan-Menubar.app"  240 180 \
  --icon "Guanlan-Desktop.app"  360 180 \
  --icon "Guanlan-Widget.app"   480 180 \
  --icon "用 Claude Code 自动安装.command" 120 320 \
  --icon "用 Codex 自动安装.command"        240 320 \
  --icon "手动安装.command"                 360 320 \
  --icon "请先读我.txt"                    560 320 \
  --app-drop-link 600 180 \
  --hide-extension "Guanlan.app" \
  --hide-extension "Guanlan-Menubar.app" \
  --hide-extension "Guanlan-Desktop.app" \
  --hide-extension "Guanlan-Widget.app" \
  --no-internet-enable \
  "$OUT_DMG" \
  "$STAGE" \
  || { echo "${C_RED}DMG 构建失败${C_OFF}"; exit 1; }

# ---- 7. 清舞台 ----
rm -rf "$STAGE"

echo
echo "${C_GREEN}${C_BOLD}✓ DMG 已生成：${C_OFF}"
echo "  $OUT_DMG"
ls -lh "$OUT_DMG" | awk '{print "  大小："$5}'
echo
echo "可以传到 GitHub Releases 给朋友下载了。"
echo
