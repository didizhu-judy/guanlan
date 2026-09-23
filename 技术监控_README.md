# 观澜技术触发监控 · tech_monitor.py

盯住你配置的股票的技术触发条件，**一旦"图破了"就往飞书推一条消息**。

## 它怎么工作（架构）

```
tech_monitor.py  ──HTTP──>  观澜服务 /api/tech  ──>  yfinance(温好的,带缓存)
   (只用标准库)              (复用与"技术面"面板                ↑
   评估触发 + 去重            完全一致的指标算法)          冷启动会卡，所以
   ↓ 触发了                                              不让监控自己抓数
飞书 webhook 推送
```

- 监控脚本**不导入 yfinance**（避免冷启动卡死），数据全部走观澜服务的 `/api/tech` 接口。
- **边沿触发 + 状态去重**：只在"今天刚发生"那一刻推一次，不会反复刷屏（状态存在 `monitor_state.json`）。

## 盯哪些信号（MU 默认 5 条，都是日线）

| 触发器 | 含义 |
|---|---|
| `macd_death_cross` | 日线 MACD 死叉（蓝线下穿橙线）—— 动能转向最早信号 |
| `below_ma20` | 收盘跌破 MA20 —— 上升结构破位 |
| `rsi_lose_70` | RSI 从 ≥70 跌破 70 —— 超买回落降温 |
| `down_on_volume` | 放量下跌（量 > 1.5×20 日均量）—— 有人出货 |
| `bearish_divergence` | 顶背离：价逼近/创新高但 MACD 驼峰更矮 —— 动能没跟上 |

改规则 / 加股票：编辑 `tech_monitor.py` 顶部的 `WATCH` 字典即可（已留 LITE 示例）。

## 一次性配置（4 步）

**1. 拿飞书 webhook**：飞书群 → 设置 → 群机器人 → 添加「自定义机器人」→ 复制 Webhook 地址。
（如果勾了「签名校验」，把密钥也记下。）

**2. 填进 `.env`**：
```
FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxxx
FEISHU_SECRET=          # 没开签名校验就留空
```

**3. 重启观澜服务**（让新的 `/api/tech` 接口生效）：
```bash
# ⚠️ 重启前先确认 yfinance 能正常 import（冷启动有时会被 Yahoo 限流卡住）：
.venv/bin/python3 -c "import yfinance; print('ok')"      # 卡住就等一会再重启，别杀旧服务
# 确认 ok 后再重启：
pkill -f "观澜/.venv/bin/python3 server.py"
nohup .venv/bin/python3 server.py > /tmp/guanlan_server.log 2>&1 &
curl -s "http://localhost:8787/api/tech?ticker=MU_US_EQ" | head -c 200   # 应返回 JSON 指标
```

**4. 测试 & 上线**：
```bash
.venv/bin/python3 tech_monitor.py --test-feishu   # 飞书收到测试消息 = 通了
.venv/bin/python3 tech_monitor.py --status        # 看当前指标 + 各触发器状态
.venv/bin/python3 tech_monitor.py                 # 真跑一次（触发了才推）
```

## 让它后台一直跑（macOS LaunchAgent，每 30 分钟一次）

```bash
cp com.guanlan.techmonitor.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.guanlan.techmonitor.plist
# 关掉： launchctl unload ~/Library/LaunchAgents/com.guanlan.techmonitor.plist
# 日志：  tail -f /tmp/guanlan_techmonitor.log
```

## 小贴士

- 日线指标**最干净的确认时点是美股收盘后**（英国时间 ~21:00），盘中是"暂定"信号、会抖。每 30 分钟跑能给你盘中早提醒，收盘后那次最算数。
- 触发是**提示不是命令**——收到后照《观澜技术面看图SOP》自己再看一眼，结合仓位决定。
- 想临时停推、只观察：用 `--status`。

---

# 第二层：深度分析 · deep_analysis.py（多智能体定期复盘）

参考 **TradingAgents**（多智能体交易框架）的精简版：对核心持仓跑
**分析师团队(基本面/技术/新闻) → 多空各一轮 → 交易员裁决**，
每天 **盘前/盘中/盘后** 各一次，结论推飞书 + 完整报告存档到 `reports/`。

**和实时监控的分工**：`tech_monitor` 抓"破位那一刻"（**时点**，秒级阈值）；
`deep_analysis` 抓"该怎么看、该不该动"（**方向 + 决策**，定期深度）。

**数据来源**：持仓走 `/api/snapshot`(实时)；指标走 `/api/tech`(没有则退回 `/tmp` 缓存并标注)；
新闻走 `/tmp` 市场缓存；触发告警读 `alert_queue.json`。**每只票 1 次 codex 调用**（模型内部扮演各角色，控成本）。

```bash
# 看喂给 codex 的 prompt（不调用、不推送）
.venv/bin/python3 deep_analysis.py --stock MU_US_EQ --dry-run
# 真跑一只，终端看结果
.venv/bin/python3 deep_analysis.py --stock MU_US_EQ --no-feishu
# 跑整轮（核心 6 只），推飞书 + 存档
.venv/bin/python3 deep_analysis.py --session postclose
```

盯哪些票：改 `deep_analysis.py` 顶部的 `WATCH_DEEP`（默认核心 6 只：MU/LITE/GOOGL/MSFT/AAOI/SNDK）。

**后台 3×/天**（英国时间 13:30 盘前 / 17:30 盘中 / 21:30 盘后，周末自动跳过）：
```bash
cp com.guanlan.deepanalysis.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.guanlan.deepanalysis.plist
# 日志： tail -f /tmp/guanlan_deepanalysis.log
```

> 💸 **成本提示**：核心 6 只 × 3 次/天 ≈ **21 次 codex 调用/天**。想省就减 `WATCH_DEEP` 或降频率。
> 🔌 **依赖**：① 观澜服务在跑（取数）；② `codex` CLI 可用（分析）；③ 指标要最新需 `/api/tech`（服务重启后生效，没重启则用 `/tmp` 缓存并标注）；④ 飞书 webhook（与 tech_monitor 共用 `.env`）。
