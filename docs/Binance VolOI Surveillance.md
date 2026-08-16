
# Binance 永续合约成交量 + OI 异常监控系统——项目开发提示词

## 一、项目目标

请从零创建一个**全新的独立项目**，不要修改或依赖任何旧项目。

开发一个 Binance USDⓈ-M USDT 永续合约市场监控系统。

系统每 **5分钟**扫描一次所有符合流动性要求的交易对，在以下5个周期中检测异常：

```text
15m
30m
1h
4h
1d
```

核心逻辑：

```text
全部永续交易对
    ↓
24h成交额过滤
    ↓
符合条件的交易对
    ↓
实时K线
    ↓
计算当前K线预计成交量
    ↓
与历史成交量EMA12比较
    ↓
预计成交量 >= EMA12 × 1.5
    ↓
查询OI
    ↓
计算OI变化率
    ↓
OI变化率 >= 0.05%
    ↓
生成Signal
```

---

# 二、技术栈

## 后端

使用：

```text
Python 3.12+
FastAPI
SQLAlchemy
PostgreSQL
httpx
websockets
Pydantic
asyncio
```

后端采用异步架构。

---

## 前端

使用：

```text
Vue 3
Vite
TypeScript
Element Plus
Pinia
ECharts
```

前端主要用于：

* 实时Signal监控
* 历史Signal查询
* 参数配置
* 数据统计分析

---

# 三、Binance接口

使用以下接口：

### 1. ExchangeInfo

```text
GET /fapi/v1/exchangeInfo
```

用于获取所有USDT永续合约。

只保留：

```text
contractType = PERPETUAL
status = TRADING
quoteAsset = USDT
```

---

### 2. 24h Ticker

```text
GET /fapi/v1/ticker/24hr
```

一次获取全部交易对的24h行情。

不要一个交易对请求一次。

主要使用：

```text
symbol
lastPrice
priceChangePercent
quoteVolume
```

---

### 3. Kline

```text
GET /fapi/v1/klines
```

只用于：

* 程序启动时初始化历史K线
* WebSocket断线后的数据补偿
* 数据缺口修复

正常运行时**不要每5分钟重新请求全部K线**。

---

### 4. Premium Index

```text
GET /fapi/v1/premiumIndex
```

第一版可以获取并保存：

```text
markPrice
indexPrice
lastFundingRate
nextFundingTime
```

但暂时不作为Signal的硬性判断条件。

---

### 5. Open Interest

```text
GET /futures/data/openInterestHist
```

根据当前交易对和周期查询OI历史数据。

---

# 四、交易对筛选

系统启动后首先获取全部USDT永续交易对。

然后使用24h Quote Volume进行第一层过滤。

默认：

```text
min24hQuoteVolume = 10,000,000 USDT
```

只有：

```text
24h Quote Volume >= 10,000,000
```

的交易对进入Active Symbol Pool。

这个参数必须配置化，可以随时修改。

例如：

```text
1M
5M
10M
20M
50M
100M
```

交易对池必须支持动态更新。

---

# 五、K线数据架构

这是本项目的核心设计。

## 1. 历史K线使用REST初始化

程序启动后，对每个Active Symbol的5个周期获取历史K线。

建议每个周期初始化：

```text
1000根完整K线
```

例如：

```text
BTCUSDT
 ├── 15m → 1000根
 ├── 30m → 1000根
 ├── 1h  → 1000根
 ├── 4h  → 1000根
 └── 1d  → 1000根
```

主要用于：

* EMA12计算
* 数据初始化
* 后续指标扩展

---

## 2. 实时K线使用WebSocket

正常运行必须使用Binance WebSocket维护K线。

订阅：

```text
<symbol>@kline_15m
<symbol>@kline_30m
<symbol>@kline_1h
<symbol>@kline_4h
<symbol>@kline_1d
```

WebSocket负责：

```text
当前未完成K线实时更新
+
K线收盘后的完整K线更新
```

---

## 3. Kline Cache

后端维护内存缓存：

```python
kline_cache[symbol][timeframe]
```

每个周期至少保存：

```text
当前K线
+
最近50~100根完整K线
```

WebSocket不断更新当前K线。

当K线收盘后，将其变成完整K线。

---

# 六、为什么REST和WebSocket都需要

必须采用：

```text
REST
↓
历史K线初始化 / 缺失数据补偿

WebSocket
↓
实时K线更新
```

不能只使用WebSocket，因为WebSocket主要提供实时K线，EMA12需要历史完整K线。

也不能每5分钟使用REST重新拉取全部K线，否则没有必要增加API压力。

---

# 七、每5分钟扫描

系统每5分钟严格按照时间边界扫描：

```text
00
05
10
15
20
25
...
```

例如：

```text
21:55
22:00
22:05
```

而不是简单使用：

```python
setInterval(300000)
```

必须根据服务器时间计算下一个5分钟边界。

---

# 八、当前K线进度

对于每个：

```text
交易对 + 周期
```

计算当前K线已经过去多少百分比：

```text
progress =
(now - klineStartTime)
/
(klineEndTime - klineStartTime)
```

例如当前：

```text
21:56
```

15分钟K线：

```text
21:45 ~ 22:00
```

已经过去：

```text
11 / 15 = 73.33%
```

1小时K线：

```text
21:00 ~ 22:00
```

已经过去：

```text
56 / 60 = 93.33%
```

---

# 九、最小K线进度

为了避免K线刚开始时预计成交量严重失真，增加：

```text
minProgressPercent = 10
```

如果当前K线进度：

```text
< 10%
```

则不计算预计成交量，也不查询OI。

这个参数必须配置化。

---

# 十、预计完整K线成交量

使用当前未完成K线的成交量计算预计完整成交量。

公式：

```text
estimatedVolume =
currentVolume / progress
```

例如：

```text
currentVolume = 80,000
progress = 0.8
```

那么：

```text
estimatedVolume = 100,000
```

注意：

> 当前未完成K线不能加入Volume EMA。

---

# 十一、Volume EMA12

Volume EMA使用：

> 最近12根已经收盘的完整K线成交量。

默认：

```text
volumeEmaPeriod = 12
```

例如当前15m K线正在形成：

```text
当前K线
+
前面12根已经收盘的15m K线
```

当前K线只用于：

```text
currentVolume
estimatedVolume
```

前12根完整K线用于：

```text
Volume EMA12
```

这样可以避免当前爆量影响EMA基准。

EMA周期必须配置化。

---

# 十二、Volume Ratio

计算：

```text
volumeRatio =
estimatedVolume / volumeEMA
```

默认：

```text
volumeMultiplier = 1.5
```

判断：

```text
volumeRatio >= 1.5
```

才进入OI检测。

例如：

```text
EMA12 = 1,000,000
预计成交量 = 1,800,000

volumeRatio = 1.8
```

则：

```text
1.8 >= 1.5
```

通过。

---

# 十三、OI查询

只有Volume条件通过以后才查询OI。

不要对所有：

```text
交易对 × 5周期
```

全部查询OI。

例如：

```text
200个交易对 × 5周期 = 1000个组合
```

如果只有50个组合通过Volume条件：

```text
只查询这50个OI
```

这样可以大幅减少REST API请求。

---

# 十四、OI变化率

使用：

```text
GET /futures/data/openInterestHist
```

查询当前交易对、当前周期对应的OI历史数据。

计算：

```text
oiChangePercent =
((newestOI - oldestOI) / oldestOI) * 100
```

默认：

```text
oiChangeThresholdPercent = 0.05
```

即：

```text
0.05%
```

不是：

```text
5%
```

这个参数必须配置化。

---

# 十五、OI时间范围

OI数据必须尽可能对应当前正在形成的K线。

例如：

```text
当前15m K线：
21:45 ~ 22:00
```

则：

```text
oldestOI
```

尽量选择接近：

```text
21:45
```

的数据。

```text
newestOI
```

选择当前最新可用数据。

必须根据时间戳匹配，而不是简单无条件取第一条和最后一条。

---

# 十六、Signal条件

只有同时满足：

```text
Volume Ratio >= volumeMultiplier
```

以及：

```text
OI Change Percent >= oiChangeThresholdPercent
```

才生成Signal。

Signal类型：

```text
VOLUME_OI_ANOMALY
```

第一版**不要判断LONG/SHORT**。

因为：

```text
成交量增加 + OI增加
```

本身无法直接确定方向。

---

# 十七、Signal必须保存完整数据

Signal不能只保存：

```text
symbol
timeframe
volume
oi
```

必须保存Signal产生时的完整快照。

至少包括：

### 基础

```text
symbol
timeframe
detectedAt
```

### K线

```text
openTime
closeTime
open
high
low
currentPrice
currentVolume
currentQuoteVolume
progressPercent
```

### Volume

```text
estimatedVolume
volumeEMA
volumeEmaPeriod
volumeRatio
volumeMultiplier
```

### OI

```text
oldestOI
newestOI
oiChangeAbsolute
oiChangePercent
oldestTimestamp
newestTimestamp
```

### 市场

```text
lastPrice
priceChangePercent24h
quoteVolume24h
```

### Signal

```text
signalType
```

---

# 十八、Signal数据必须不可变

Signal产生以后，原始Signal Snapshot原则上不能修改。

原因：

未来回测必须能够准确回答：

> Signal产生的那一刻，市场到底是什么状态？

所以Signal保存的是：

```text
不可变历史快照
```

---

# 十九、未来表现数据

Signal生成时：

```text
futurePerformance = NULL
```

随着时间过去，系统自动计算：

```text
5m
15m
30m
1h
4h
1d
```

的未来收益率。

至少保存：

```text
return5m
return15m
return30m
return1h
return4h
return1d
```

以及：

```text
maxProfitPercent
maxLossPercent
```

用于以后分析Signal质量。

---

# 二十、数据库

使用PostgreSQL。

核心表：

```text
symbols
klines
open_interest_snapshots
signals
signal_future_performance
scanner_runs
system_config
```

其中：

### klines

唯一约束：

```text
symbol + timeframe + openTime
```

防止重复K线。

### signals

保存完整Signal Snapshot。

### scanner_runs

记录每次扫描：

```text
startedAt
completedAt
duration
symbolCount
candidateCount
oiRequestCount
signalCount
errorCount
```

---

# 二十一、API限流和异常处理

后端必须统一管理Binance REST请求。

实现：

```text
RateLimiter
RequestQueue
Retry
Timeout
```

必须处理：

```text
429
500
502
503
网络超时
连接失败
```

如果收到429：

```text
退避
等待
重试
```

不能无限重试。

也不能一次性：

```python
asyncio.gather()
```

同时发起大量REST请求。

---

# 二十二、WebSocket异常处理

必须支持：

```text
自动重连
重新订阅
心跳
断线检测
K线缺口检测
REST补缺
```

WebSocket断开不能导致整个系统停止。

---

# 二十三、前端页面

第一版只需要几个核心页面。

## Dashboard

显示：

```text
全部永续交易对数量
过滤后交易对数量
当前WebSocket状态
最后扫描时间
最后扫描耗时
今日Signal数量
```

---

## Signal Monitor

实时显示：

```text
时间
交易对
周期
当前价格
K线进度
预计成交量
EMA12
Volume Ratio
OI变化%
24h成交额
```

支持：

```text
周期筛选
交易对搜索
Volume Ratio排序
OI排序
时间排序
```

---

## Signal Detail

显示完整Signal数据。

包括：

```text
K线
成交量
Volume EMA
Volume Ratio
OI
24h行情
Signal Snapshot
```

---

## Configuration

支持修改：

```text
min24hQuoteVolume
volumeEmaPeriod
volumeMultiplier
minProgressPercent
oiChangeThresholdPercent
```

以及必要的系统参数。

---

# 二十四、项目目录

保持简单，不要过度设计：

```text
project/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   │   ├── binance/
│   │   │   ├── websocket/
│   │   │   ├── cache/
│   │   │   ├── scanner/
│   │   │   └── performance/
│   │   └── main.py
│   ├── migrations/
│   ├── tests/
│   └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── views/
│   │   ├── stores/
│   │   └── types/
│   └── package.json
│
├── docker-compose.yml
├── README.md
└── .env.example
```

---

# 二十五、默认配置

第一版默认：

```python
timeframes = [
    "15m",
    "30m",
    "1h",
    "4h",
    "1d"
]

min24hQuoteVolume = 10_000_000

volumeEmaPeriod = 12

volumeMultiplier = 1.5

minProgressPercent = 10

oiChangeThresholdPercent = 0.05

scanIntervalMinutes = 5
```

所有核心参数必须配置化，不能写死。

---

# 二十六、开发原则

### 1

K线：

```text
REST初始化
+
WebSocket实时维护
```

正常运行不要每5分钟重新拉全部K线。

### 2

OI：

```text
Volume通过
↓
才查询
```

### 3

EMA：

```text
只使用已经收盘的K线
```

### 4

Signal：

```text
保存完整快照
```

### 5

API：

```text
控制并发
控制请求频率
处理429
```

### 6

第一版不要加入：

```text
自动交易
AI
机器学习
复杂策略
Redis集群
Kafka
Kubernetes
微服务
```

先把：

```text
实时数据
+
Signal
+
历史数据
+
分析基础
```

做好。

---

# 二十七、最终数据流程

最终系统必须实现：

```text
Binance
   ↓
ExchangeInfo
   ↓
全部USDT永续
   ↓
24h Quote Volume过滤
   ↓
Active Symbol Pool
   ↓
REST初始化历史K线
   ↓
WebSocket实时K线
   ↓
Kline Cache
   ↓
每5分钟Scanner
   ↓
计算K线进度（同一周期的所有交易对只需要计算一次即可）
   ↓
计算预计完整成交量
   ↓
计算完整K线Volume EMA12
   ↓
Volume Ratio
   ↓
Volume Ratio >= 1.5
   ↓
查询OI
   ↓
计算OI Change %
   ↓
OI >= 0.05%
   ↓
生成VOLUME_OI_ANOMALY
   ↓
保存完整Signal
   ↓
WebSocket推送前端
   ↓
未来自动计算5m/15m/30m/1h/4h/1d表现
   ↓
用于后续统计、参数优化和回测
```

---

# 二十八、开发完成要求

开发完成后请提供：

1. 完整项目目录
2. 数据库表结构
3. Binance API调用说明
4. WebSocket架构说明
5. Scanner逻辑
6. Volume EMA计算逻辑
7. OI计算逻辑
8. Signal数据结构
9. API接口列表
10. 前端页面说明
11. 配置参数说明
12. 启动方式
13. 单元测试
14. 异常处理说明

最终目标：

> **建立一个低REST API压力、实时维护K线、每5分钟检测成交量异常 + OI异常，并完整保存Signal历史快照的Binance永续合约数据监控平台，为后续回测和量化策略开发提供可靠的数据基础。**
