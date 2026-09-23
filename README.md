<div align="center">

<img src="static/icon-512.png" alt="观澜" width="128" height="128" />

# 观澜 · Guānlán

**一方桌面的 Trading 212 实盘观测台**

<sub>中文 · <a href="README.en.md">English</a></sub>

<br/>

> 半亩方塘一鉴开，天光云影共徘徊。
> 问渠那得清如许？为有源头活水来。
> <sub>—— 朱熹《观书有感》</sub>

<br/>

[![macOS](https://img.shields.io/badge/macOS-13%2B-1c1c1e?style=flat&logo=apple&logoColor=white)](#)
[![Python](https://img.shields.io/badge/Python-3.9%2B-3776ab?style=flat&logo=python&logoColor=white)](#)
[![FastAPI](https://img.shields.io/badge/FastAPI-local-009688?style=flat&logo=fastapi&logoColor=white)](#)
[![License](https://img.shields.io/badge/license-MIT-555?style=flat)](#license)
[![Status](https://img.shields.io/badge/status-v0.1%20preview-d29922?style=flat)](#)

</div>

---

> **原作与致谢**：观澜第一版（v0.1：本地看板、菜单栏、桌面挂件、通知中心组件，以及名字和图标）由 [**@Fei-Ni**](https://github.com/Fei-Ni) 创作。本仓库在此基础上继续开发——飞书技术监控脚本和 v2「决策台」（多账户、组合风险、催化剂日历、英国税年额度等）为后续新增。原作以 MIT 协议发布，其版权声明保留在 [LICENSE](LICENSE) 中。

## 🆕 v2 · 决策台（2026-09）

完整面板重写为「决策台」：少盯盈亏跳动，多看做决定真正需要的信息。

- **盯盘**：账户总览（锁定 + 浮动 = 总收益；锁定收益含股息、现金利息、换汇费明细）· 今日要点 · 按板块分组的持仓（30 日走势 / 日内区间 / 均线与 RSI）· 自选 · 大盘 · 持仓地图 · 催化剂 · 监控信号
- **个股面板**：自绘 K 线（期权墙、挂单、价格提醒、历史买卖点直接叠在图上）· 技术面体检（附白话解释）· 期权区间 · 基本面 · 投资逻辑笔记 · 价格提醒 · 按风险反推仓位
- **风险**：Beta / 波动 / VaR · 仓位 vs 波动贡献 · 压力测试 · 相关性 · 剔除入金影响的收益曲线
- **日历 · 新闻**：持仓财报 + 美国宏观数据 + FOMC，统一换算成本地时间
- **交易 · 税务**：挂单距离 · 成交记录 · 交易行为统计 · 英国 ISA 额度与资本利得免税额估算
- **多账户**：ISA 和 Invest 各用一把 API key（`.env` 里的 `TRADING212_INVEST_API_KEY_ID / _SECRET`），可合并或分开查看，改完 `.env` 自动生效
- 纸 / 夜主题 · 红涨绿跌切换 · 隐私模式（一键模糊金额）· ⌘K 搜索

---

## 一、观澜是什么

观澜 取意于「凭栏观澜」——**把每日跳动的市场数据，化作一道安静可见的波纹**。

它是一个完全在你本机运行的 Trading 212 看板。没有第三方服务器，没有遥测，账户密钥只存在于你电脑的 `.env`。所有数据在 `localhost:8787` 上汇成一池清水，再以四种形态浮现到桌面：

| 形态 | 在哪里看到 | 何时看 |
| :--- | :--- | :--- |
| 🖥️ **完整面板** | Safari PWA 全屏 | 想认真看盘、问 AI、分析持仓 |
| 📊 **菜单栏** | macOS 顶栏  `观澜  ▲ £293  0.66%` | 走路、写代码、随时一瞥 |
| 🪟 **桌面挂件** | 半透明浮窗，可拖动 | 整天放在屏幕一角，看持仓涨跌 |
| 📱 **通知中心组件** | 原生 WidgetKit Small / Medium / Large | 划开通知中心，一秒掌握 |

四者共享同一份后台数据，互不打架。

---

## 二、它能做什么

<table>
<tr>
<td width="50%" valign="top">

### 📈 看盘
- 实时持仓与浮动盈亏
- 今日 / 浮动 / 总收益 三档统计卡片
- 持仓占比 Treemap（含 ETF 国别穿透）
- 单股 1 年走势 + SMA / EMA / MACD / RSI / 布林带
- 今日异动告警（1h ≥ 4% / 当日 ≥ 10% / 摆动 ≥ 6%）

</td>
<td width="50%" valign="top">

### 🤖 问 AI
- 内嵌 **Codex CLI** 聊天框（本地，无 API 费用）
- 一键生成「持仓体检」「技术面快读」「消息面摘要」
- 上下文喂入预拉取的市场 JSON，AI 不再瞎查
- 聊天历史持久化（localStorage，4h TTL）

</td>
</tr>
<tr>
<td valign="top">

### 🪶 用得舒服
- 全部中文文案，沉浸式深色主题
- 状态栏 / 桌面挂件可同时开启
- 关掉再打开不会重新触发 AI 分析
- 数据缓存有「过期兜底」，T212 限流时仍可读旧值

</td>
<td valign="top">

### 🔒 安全
- 凭据只存 `.env`，从不离开本机
- T212 API 只用只读 scope（不勾选 `orders.execute`）
- 后端只暴露 `localhost`，不监听 0.0.0.0
- 全部开源，可审计、可改

</td>
</tr>
</table>

---

## 三、最快上手

### 方式 A · DMG 一键装（推荐给非开发者）

1. 从 [Releases](#) 下载 `观澜-v0.1.dmg`
2. 双击打开，把四个 `.app` 拖进 `Applications`
3. 双击 `用 Claude Code 自动安装.command`（或 `用 Codex 自动安装.command`），跟着 AI 走完账户配置
4. 启动 `观澜.app` —— 收工

> 没有 Claude / Codex？双击 `手动安装.command`，脚本会指引你一步步填。

### 方式 B · 从源码起（推荐给开发者）

```bash
git clone https://github.com/<your-username>/guanlan.git
cd guanlan
./scripts/install.sh        # 创建 venv, 装依赖, 引导配置 .env
./scripts/start.sh          # 启动后端 + Safari PWA
```

随后阅读 [`SETUP.md`](SETUP.md) 了解菜单栏 / 桌面挂件 / WidgetKit 的安装细节。

### 方式 C · 把这份 README 喂给 AI，让它帮你装

```bash
# 在装好 Claude Code 或 Codex CLI 后，把这个仓库 clone 下来
# 然后在仓库根目录运行：
claude  # 或  codex
# 对它说一句：
# "请你阅读 SETUP.md，帮我把观澜在我这台 Mac 上装好"
```

`SETUP.md` 专为 AI 阅读编写——每一步都有「检测条件 / 失败处理 / 跳过的代价」，AI 会安静地把所有事做完。

---

## 四、四种形态长什么样

<table>
<tr>
<th>🖥️ Safari PWA</th>
<th>📊 菜单栏</th>
</tr>
<tr>
<td>
完整面板：顶栏统计卡 + Treemap + 持仓表 + Codex 聊天 + 技术面 / 消息面分析 Tab。<br/><br/>
通过 Safari「添加到程序坞」可作为独立 App 启动，并在 Dock 显示观澜图标。
</td>
<td>
菜单栏标题实时显示「<code>观澜  ▲ £293  0.66%</code>」。<br/><br/>
左键弹出 popover（迷你看板 + 持仓列表 + 异动徽章），右键弹出原生菜单（打开 Dashboard / 立即刷新 / 退出）。
</td>
</tr>
<tr>
<th>🪟 桌面挂件</th>
<th>📱 通知中心 (WidgetKit)</th>
</tr>
<tr>
<td>
半透明浮窗，停在桌面图标层级之上、应用窗口之下。可拖动，位置自动保存。<br/><br/>
点击不会抢占焦点，安静陪伴。
</td>
<td>
原生 SwiftUI Widget，支持 Small / Medium / Large 三种尺寸。<br/><br/>
Medium / Large 内嵌「刷新」按钮（AppIntent 驱动），点一下立即重拉数据。
</td>
</tr>
</table>

---

## 五、架构

```
┌─────────────────────────────────────────────────────────────┐
│                       你的 Mac                                │
│                                                              │
│   ┌───────────────┐    ┌──────────────────────────────┐     │
│   │  Trading 212  │ ←→ │   FastAPI 后台 (localhost)    │     │
│   └───────────────┘    │                              │     │
│   ┌───────────────┐    │   • 缓存 + 过期兜底           │     │
│   │   Finnhub     │ ←→ │   • 工作区快照 → Codex CLI    │     │
│   └───────────────┘    │   • 告警引擎                 │     │
│   ┌───────────────┐    │   • 异动 / 技术面计算         │     │
│   │   yfinance    │ ←→ │   :8787                      │     │
│   └───────────────┘    └──────┬───────────────────────┘     │
│                               │                              │
│       ┌───────────────────────┼───────────────────────┐     │
│       ↓                       ↓                       ↓     │
│  ┌─────────┐            ┌────────────┐         ┌──────────┐ │
│  │ Safari  │            │  菜单栏 +    │         │ WidgetKit│ │
│  │  PWA    │            │ 桌面挂件     │         │  通知中心 │ │
│  └─────────┘            └────────────┘         └──────────┘ │
└─────────────────────────────────────────────────────────────┘
```

- **后端**：`server.py`（FastAPI），缓存层有 TTL + stale-fallback
- **AI**：Codex CLI 子进程，预先把 snapshot.json / market/*.json 写到 `/tmp/t212-codex-workspace`
- **存储**：`transactions_cache.json` / `daily_totals.json` / `price_log.json` / `alert_queue.json`

详情见 [`PLAN.md`](PLAN.md) 与 [`SETUP.md`](SETUP.md)。

---

## 六、技术栈

| 层 | 用到的东西 |
| :--- | :--- |
| 后端 | FastAPI · httpx · python-dotenv |
| 数据 | Trading 212 API (HTTP Basic Auth) · Finnhub · yfinance |
| 前端 | 原生 HTML + JS（无构建步骤）· Chart.js · D3 treemap |
| 菜单栏 / 桌面挂件 | PyObjC (NSStatusBar / NSPopover / WKWebView / NSWindow) |
| 通知中心 | SwiftUI · WidgetKit · AppIntents（Xcode 16 项目） |
| AI | Codex CLI（本地，无 API 计费） |

---

## 七、依赖与可选项

观澜按「能用就装，缺啥就降级」的原则设计——下面这些**不是全都必需**：

| 依赖 | 用途 | 缺失时的后果 |
| :--- | :--- | :--- |
| Trading 212 API Key | 拉取实盘数据 | **必需**，否则没数据 |
| Python 3.9+ | 跑后端 | **必需** |
| Codex CLI | AI 分析 | 缺失 → 聊天框灰显，其余功能不受影响 |
| Finnhub API Key | 消息面、单股报价 | 缺失 → 消息面 Tab 显示「未配置」，持仓数据仍正常 |
| Xcode 16 | 编译 WidgetKit | 缺失 → 通知中心组件不可用，其余三种形态照常 |
| `create-dmg` | 构建发布 DMG | 仅开发者需要 |

---

## 八、开源与隐私

- **MIT 协议** —— 随便 fork、改、商用
- **没有遥测** —— 这是一个完全私有的工具，不会向任何服务器汇报你的持仓
- **凭据本地化** —— `.env` 在 `.gitignore` 里，不可能被 push 出去
- **审计友好** —— 全部代码可见，不到 5k 行

如果你担心 API Key 泄露，[这里有一份「凭据自检清单」](SETUP.md#安全自检) 帮你确认。

---

## 九、致谢与缘起

- **原作**：观澜第一版由 [@Fei-Ni](https://github.com/Fei-Ni) 创作并以 MIT 协议发布，本仓库是在其基础上的延续开发。名字、图标和四种形态的整体设计都出自原作。
- **名字**：取自朱熹《观书有感》「为有源头活水来」与岭南古镇「观澜」。希望这只仪表盘也能成为你看市场的一汪活水。
- **图标**：金牛踏浪，箭头穿云——希望牛市常在，也希望你穿越波澜。
- **灵感**：感谢 Trading 212 提供 API，感谢 Codex 让本地 AI 成为可能，感谢曾经用 Bloomberg Terminal 把屏幕填得满满的所有量化人。

---

## 十、贡献 / 反馈

- 发现 bug：开 Issue
- 想加形态（比如 Touch Bar、Today Extension）：开 PR
- 想聊聊：观澜默默观你，你也可以来观澜

<div align="center">
<br/>
<sub>用 ❤️ 与 SwiftUI / PyObjC / FastAPI 砌成 · 2026</sub>
<br/>
<sub><b>观澜  v2</b> · 原作 v0.1 由 <a href="https://github.com/Fei-Ni">@Fei-Ni</a> 创作</sub>
</div>

<a id="license"></a>

## License

MIT · 原作 © 2026 [Fei-Ni](https://github.com/Fei-Ni) · v2 改动 © 2026 观澜 v2 contributors —— 全文见 [LICENSE](LICENSE)
