# SETUP — 观澜 Guānlán 安装指南

<sub>中文 · [English](SETUP.en.md)</sub>

> 这份文档**同时写给人和 AI 读**。如果你是 AI（Claude Code / Codex CLI），请按顺序执行；
> 每一步都标了「**检测**」「**做**」「**失败处理**」「**跳过的代价**」。
> 用户**可以跳过任何标了「⚠️ 可选」的步骤**，对应功能会优雅降级，不会让其他部分崩溃。

---

## 目录

1. [前置检查](#1-前置检查)
2. [必需：Python 后端](#2-必需python-后端)
3. [必需：Trading 212 凭据](#3-必需trading-212-凭据)
4. [可选：Finnhub（消息面）](#4-可选finnhub消息面)
5. [可选：Codex CLI（AI 分析）](#5-可选codex-cliai-分析)
6. [启动：四种形态](#6-启动四种形态)
7. [可选：WidgetKit 通知中心组件](#7-可选widgetkit-通知中心组件)
8. [可选：开机自启](#8-可选开机自启)
9. [故障排除](#9-故障排除)
10. [安全自检](#10-安全自检)

---

## 1. 前置检查

**检测**：
```bash
sw_vers -productVersion          # 需 ≥ 13.0
python3 --version                # 需 ≥ 3.9
xcode-select -p 2>/dev/null      # 有就行，没有也能跑前三种形态
```

**做**：
- macOS < 13：观澜不支持，请升级（WidgetKit 也用不了）。
- 没装 Python：到 [python.org](https://www.python.org/downloads/) 下载 3.11+；或 `brew install python@3.11`。
- 没装 Xcode Command Line Tools：`xcode-select --install`（弹窗里点「安装」）。

**失败处理**：python3 装不上的话，停下来问用户。

**跳过的代价**：无法跳过（macOS + Python 是地基）。

---

## 2. 必需：Python 后端

**检测**：
```bash
cd <项目根目录>
ls server.py requirements.txt
```

**做**：
```bash
# a) 建立虚拟环境（不污染系统 Python）
python3 -m venv .venv
source .venv/bin/activate

# b) 装依赖
pip install --upgrade pip
pip install -r requirements.txt

# c) PyObjC（菜单栏 + 桌面挂件用）
pip install "pyobjc-core<11" "pyobjc-framework-Cocoa<11" "pyobjc-framework-WebKit<11"
# 注意：pyobjc-core<11 是为了兼容 Python 3.9。
# 如果你的 Python ≥ 3.11，可去掉版本上限。

# d) 健康检查（不连真账户）
python3 -c "import fastapi, httpx, yfinance, objc; print('ok')"
```

**失败处理**：
- `error: command 'gcc' failed`：装 Xcode CLT（步骤 1）。
- `pyobjc-core` 编译失败：强制走预编译 wheel —— `pip install "pyobjc-core<11" --only-binary :all:`。
- `yfinance` 装不上：可以暂时跳过，K 线图会显示「无数据」，其余功能正常。

**跳过的代价**：跳不了。这是最小内核。

---

## 3. 必需：Trading 212 凭据

**检测**：
```bash
cat .env 2>/dev/null | grep TRADING212_API_KEY_ID
```

**做**：

1. 登录 [Trading 212](https://www.trading212.com/) → Settings → API → **Generate new key**。
2. **只勾选只读权限**（不要勾 `orders.execute` —— 观澜永远不会下单）：
   - ✅ `account.read`
   - ✅ `portfolio.read`
   - ✅ `history.read`
3. 复制 **Key ID** 和 **Secret**（页面只显示一次）。
4. 在仓库根目录新建 `.env`（如果不存在）：
   ```bash
   cp .env.example .env
   open -t .env    # 用 TextEdit 打开
   ```
5. 填入：
   ```
   TRADING212_API_KEY_ID=41311829xxxxxxxxxxxxxxxxx
   TRADING212_API_SECRET=Ozl0xxxxxxxxxxxxxxxxxxxxxxxx
   TRADING212_ENV=live           # 或 demo
   ```

**失败处理**：
- 401：99% 是 Basic Auth 没用对。**Key ID 是 username，Secret 是 password**，不是把 Key ID 当 token。如果你是 AI，请检查 `server.py` 里是不是 `httpx.BasicAuth`。
- 403：账户类型限制（如 ISA 的某些 endpoint）。观澜对 ISA 已做兼容，遇到再开 Issue。
- 429：T212 限流。观澜内置 stale-fallback，等 30s 再试即可。

**跳过的代价**：跳不了——没凭据等于没数据。

---

## 4. 可选 ⚠️：Finnhub（消息面）

**检测**：
```bash
grep FINNHUB_API_KEY .env 2>/dev/null
```

**做**：

1. 注册 [finnhub.io](https://finnhub.io/) 免费账号。
2. Dashboard 顶部复制 API Key（40 位字符，**不是两段拼起来**——T212 才是双段）。
3. 写入 `.env`：
   ```
   FINNHUB_API_KEY=d872xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

**失败处理**：
- Key 错：401。检查没有空格、换行。
- Free tier 限速：60 calls/min。观澜会自动延后。
- Free tier 没有 `/stock/candle`：这是 Finnhub 改规则后的事实，观澜已改用 yfinance 拿 K 线，不影响。

**跳过的代价**：
- 「消息面」Tab 显示「未配置 FINNHUB_API_KEY」。
- 单股 Modal 里的「公司新闻」段落空白。
- **其余一切正常**——持仓数据、菜单栏、桌面挂件、AI 聊天都不依赖 Finnhub。

---

## 5. 可选 ⚠️：Codex CLI（AI 分析）

**检测**：
```bash
which codex && codex --version
```

**做**：

1. 安装 Codex CLI（OpenAI 官方）：
   ```bash
   brew install codex     # 或 npm i -g @openai/codex
   ```
2. 登录：
   ```bash
   codex login
   # 跟着 OAuth 流程走完
   ```
3. 在 `.env` 里指定（默认就是 `codex`，可以省略）：
   ```
   AGENT_CMD=codex
   ```

**失败处理**：
- `command not found: codex`：装回去；或换成 `AGENT_CMD=claude` 用 Claude Code（实验性，需要自己改 `server.py` 输出解析）。
- Codex 一直转圈：检查是否登录、是否触网。

**跳过的代价**：
- 右侧「Codex 聊天」框灰显，按钮变成「未配置 AI」。
- 「技术面快读」「持仓体检」按钮不可用。
- **数据看板、菜单栏、桌面挂件、WidgetKit 全部正常**。

---

## 6. 启动：四种形态

**检测**：
```bash
curl -s http://localhost:8787/api/snapshot >/dev/null && echo "已在跑" || echo "未启动"
```

**做（按顺序，每开一个就好）**：

### 6.1 后端 + Safari PWA（必跑）

```bash
# 方式 1：双击启动器（推荐用户）
open Guanlan.app

# 方式 2：手动跑（推荐开发）
source .venv/bin/activate
python3 server.py
# 然后浏览器开 http://localhost:8787
# 在 Safari 里：文件 → 添加到程序坞，就有独立 App 入口
```

启动器 `Guanlan.app` 会：
1. 启动后端服务
2. 等端口可达
3. 用 Safari 打开 PWA URL

### 6.2 菜单栏（可选 ⚠️）

```bash
open Guanlan-Menubar.app
```

顶栏会出现 `观澜 ▲ £...`。**左键**弹出 popover，**右键**弹出原生菜单。

### 6.3 桌面挂件（可选 ⚠️）

```bash
open Guanlan-Desktop.app
```

浮窗会出现在屏幕右上角。可拖动，位置自动保存。
**退出方法**：`pkill -f desktop_widget.py`（下版会加托盘菜单）。

### 6.4 WidgetKit 通知中心（见第 7 节）

---

## 7. 可选 ⚠️：WidgetKit 通知中心组件

**前置**：Xcode 16（不是 26）。macOS 14.x 用户从 [Apple Developer Downloads](https://developer.apple.com/download/applications/) 装 Xcode 16，不要从 App Store 装最新版（会要求 macOS 26）。

**检测**：
```bash
xcodebuild -version | head -1
ls widget-kit/project.yml
```

**做（开发者）**：

```bash
cd widget-kit
brew install xcodegen          # 一次性
xcodegen generate              # 生成 T212Companion.xcodeproj
open T212Companion.xcodeproj
# 在 Xcode：Product → Archive → Distribute → Copy App
# 或者：xcodebuild -scheme T212Companion -configuration Release build
```

构建完毕后：
```bash
cp -R build/Build/Products/Release/Guanlan-Widget.app /Applications/
# 注册到 LaunchServices + pluginkit
./scripts/register-widget.sh
# 打开主 App 让系统发现 widget extension
open /Applications/Guanlan-Widget.app
```

随后到「通知中心 → 编辑小组件 → 搜索 观澜」即可添加 Small / Medium / Large。

**做（最终用户）**：DMG 里已经有预编译的 `Guanlan-Widget.app`，跑 `scripts/register-widget.sh` 即可，不需要 Xcode。

**失败处理**：
- 通知中心搜不到「观澜」：跑 `./scripts/register-widget.sh`，等 30s；不行就重启 `chronod`：`killall chronod`（注意：会重启所有人的 widget，但不丢数据）。
- 显示「No data」：先确认 `http://localhost:8787/api/snapshot` 返回 200；Widget 每 15 分钟刷新一次，也可以点 widget 右上角刷新按钮立即拉。

**跳过的代价**：通知中心里没有观澜组件。其余三种形态完全不受影响。

---

## 8. 可选 ⚠️：开机自启

**做**：

```bash
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/com.guanlan.menubar.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.guanlan.menubar</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/open</string>
    <string>-a</string>
    <string>/Applications/Guanlan-Menubar.app</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
EOF
launchctl load ~/Library/LaunchAgents/com.guanlan.menubar.plist
```

类似地，给 `Guanlan.app` 和 `Guanlan-Desktop.app` 各做一份（只改 Label 和 path）。

**跳过的代价**：每次开机需手动点 App。

---

## 9. 故障排除

| 症状 | 原因 | 修法 |
| :--- | :--- | :--- |
| 后端启动 5s 后 401 | T212 Key 写错 / 用了普通 Bearer | 改成 Basic Auth（`server.py` 已经是了，检查 `.env` 拼写） |
| 顶栏显示 `观澜 ⚠` | `localhost:8787` 没启动 | `open Guanlan.app` |
| 桌面挂件灰色无数据 | 后端 429 或 widget.html 缓存旧 | 等 30s（stale-fallback 兜底）；强刷：从菜单栏右键「立即刷新」 |
| 通知中心搜不到「观澜」 | LaunchServices 没收录 | `./scripts/register-widget.sh` |
| Codex 输出乱码 / 空 | 没登录 / 网络问题 | `codex login`；或 `AGENT_CMD=echo` 临时禁用 |
| K 线图空白 | Yahoo 临时风控 | 等 5–10min 自动恢复 |
| `.app` 双击说「无法打开」 | Gatekeeper | 右键 → 打开 → 确认；或 `xattr -d com.apple.quarantine <App>` |

更多见 [Issues](#) 或在 [`PLAN.md`](PLAN.md) 翻历史记录。

---

## 10. 安全自检

跑一遍，**全部回答「是」才安全**：

```bash
# a) .env 真的在 .gitignore 里？
grep -q '^\.env$' .gitignore && echo "✅" || echo "❌ 把 .env 加进 .gitignore"

# b) git status 看不到 .env？
git status --porcelain | grep -q '\.env$' && echo "❌ 立刻 git rm --cached .env" || echo "✅"

# c) 后端只监听 localhost？
grep -E 'host\s*=\s*["\x27]localhost' server.py >/dev/null && echo "✅" || echo "❌ 检查 server.py 启动行"

# d) T212 Key 没勾 orders.execute？
echo "→ 去 T212 网页确认这把 Key 的权限，确保没有 orders.execute"

# e) Finnhub Key 不在 Git history 里？
git log -p | grep -q "FINNHUB_API_KEY=d8" && echo "❌ 历史里有 Key，跑 git filter-repo 清掉并轮换" || echo "✅"
```

如果不小心 push 了带 Key 的 commit，立刻：
1. 去对应平台 **revoke 这把 Key**
2. 重新生成新的填回 `.env`
3. 用 `git filter-repo` 把历史清掉（或者干脆删仓库重建）

---

<div align="center">

观澜 v0.1 · 一切妥当，可以开始观你的盘了。

</div>
