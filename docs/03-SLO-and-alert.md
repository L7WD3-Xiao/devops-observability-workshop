# 三、SLO与告警

## 1.预备知识

### 1.1.名词解释

#### SLI（服务质量指标）

- **可用性**：请求成功返回 HTTP 2xx/3xx 的比例（排除404等业务错误）
- **延迟**：请求在 **100ms** 内完成的 proportion

#### SLO 目标

SLO：服务等级目标，**对 SLI 设定的目标值或范围**，代表服务承诺应达到的健康水平。

- **P99可用性**：99% 的请求成功返回 HTTP 2xx/3xx，对应下方的1% 的请求的错误预算
- **P99延迟**：99% 的跳转请求在 200ms 内成功返回

#### 错误预算-Error Budget

- 1% 的请求可以超过 100ms 或失败（每10000个请求允许100个“坏请求”）

### 1.2.常见监控指标

通常分为 **四个黄金指标（Google SRE 经典）** + 扩展指标：

| 类别         | 指标（SLI 候选）                                             | 典型阈值（举例）                   |
| :----------- | :----------------------------------------------------------- | :--------------------------------- |
| **延迟**     | 平均延迟、P50/P90/P99/P999 延迟（秒或毫秒）                  | P99 < 200ms                        |
| **流量**     | QPS / TPS、并发连接数、请求速率变化                          | 正常波动 < 30%                     |
| **错误**     | HTTP 5xx 率、超时率、业务错误码率                            | 错误率 < 0.1%                      |
| **饱和度**   | CPU/内存/磁盘使用率、goroutine/线程数、队列长度              | CPU < 80%                          |
| **可用性**   | 服务可请求成功的比例                                         | 99.9% ~ 99.999%                    |
| **其他关键** | - 数据库慢查询数 - 缓存命中率 - 消息队列积压 - DNS解析成功率 - SSL证书过期时间 | 慢查 < 1% 命中率 > 95% 积压 < 1000 |

**一句话原则**：监控 **用户能感知到的事情**（慢、失败、不可用），而不是内部无意义的“黑盒指标”。

### 1.3.统计时间尺度

| 用途                       | 粒度     | 窗口                     | 使用场景                       |
| :------------------------- | :------- | :----------------------- | :----------------------------- |
| **实时告警**               | 1s ~ 10s | 最近 1~5 分钟            | 快速发现异常（比如错误率突增） |
| **短期分析（SLO 燃烧率）** | 1 分钟   | 1 小时 / 5 分钟          | 检测 SLO 消耗过快，提前干预    |
| **标准 SLO 计算窗口**      | 1 分钟   | **滚动 30 天**（最常见） | 计算当前可用性、错误预算剩余   |
| **月报 / 趋势**            | 1 小时   | 3~12 个月                | 容量规划、供应商评估           |

> **重点**：SLO 通常采用 **滚动时间窗口**（如过去 28 天 / 30 天），而不是自然月，避免月初重置预算。

**举例**：

- 告警窗口：过去 5 分钟错误率 > 1% → 触发预警
- SLO 窗口：过去 30 天内总请求 1 亿次，错误次数 8 万 → 错误率 = 0.08% → 对比 SLO(0.1%) → 剩余预算充足

## 2.本项目监控大盘

一图流：

![msedge_aq4NOKTUNb](D:/Study/Note/project/shortener/docs/imgs/msedge_aq4NOKTUNb.png)

左半边为可用性监控，右半边为延迟监控，下面我们分别来看。

### 可用性

> 先明确基础数据来源：  
> 我们的 FastAPI 服务通过 `prometheus-fastapi-instrumentator` 自动暴露了以下指标（命名可能略有不同，但逻辑一致）：
>
> - `http_requests_total`：请求总数（labels: method, path, status_code）
> - `http_request_duration_seconds_bucket`：请求延迟直方图桶（labels: method, path, le）

（该部分仅介绍部分时间尺度的SLO，其余时间尺度写法相似，不重复介绍）

---

#### 1.`redirect_total`

```yaml
- record: shortener:redirect_total
  expr: |
    sum(
      increase(http_requests_total{method="GET", handler="/{short_code}"}[1h])
    )
```

**含义**：短链跳转接口（`GET /{code}`）的总请求次数，按 `code` 和 `cache_hit` 标签分组。

**拆解**：

- `fastapi_requests_total{method="GET", handler="/{short_code}"}`：筛选出所有 HTTP GET 请求，且路径是 `/` 后跟一串字母数字（即短链 code）。正则 `/[a-zA-Z0-9]+` 排除了 `/shorten` 等路径。
- `increase(...[1h])`：计算每个指标在过去 1 小时内的增量（即新增请求数）。

> 这条 rule 是为了后续计算错误率时，只统计跳转接口，避免混入 `/shorten` 等写操作。

---

#### 2. `redirect_success_total`

```yaml
- record: shortener:redirect_success_total
  expr: |
    sum(
      increase(http_requests_total{method="GET", handler="/{short_code}", status=~"2xx|3xx"}[1h])
    )
```

**含义**：成功的目标请求总数，满足 **状态码成功（2xx或3xx）**。

**拆解**：

- `increase(http_requests_total{..., status=~"2xx|3xx"}[1h])`  
  状态码为 2xx 或 3xx 的请求增量（3xx 是因为跳转是 302/301）。

#### 3.`sli_current`

```yaml
- record: shortener:sli_current
  expr: shortener:redirect_success_total / shortener:redirect_total
```

**含义**：当前 SLI（实际成功率）。

**拆解**：

- SLI = `成功的目标请求总数 / 总的目标请求总数`

---

#### 4. `error_budget_burn_rate`

```yaml
- record: shortener:error_budget_burn_rate
  expr: |
     (1 - shortener:sli_current)
     /
     (1 - max(slo:target))
```

**含义**：错误预算燃烧率。 
**公式**：`实际错误率 / (1 - SLO目标)` 

**拆解**：

- `1 - shortener:sli_current`：实际错误率
- `1 - max(slo:target)`：错误预算（设定的目标错误率上限，如P99则对应1%）
  - 关于`max(slo:target)`，本项目中将`slo:target`也设为record，有时该指标不能实时更新（虽然该数值不变，但不是每个统计时间点都传输值），为防止错误，用`max()`滤一下。

```yml
- record: slo:target
  expr: 0.9
  labels:
    service: "my-service"
    tier: "production"
```

#### 5. `error_budget_remaining`

```yaml
- record: shortener:error_budget_remaining_week
  expr: |
    1 - (
       (1 - shortener:sli_week)
       /
       (1 - max(slo:target))
    )
```

**含义**：过去 1 周内，剩余的错误预算比例。（时间尺度可调整）
**公式**：`1 - (实际错误率 / (1 - SLO目标))` = 剩余预算比例。

**拆解**：

- 其实实际就等于`1 - 错误预算燃烧率`，只是本项目的`error_budget_remaining`用来统计长时间尺度（如生产环境中的月度KPI），错误预算燃烧率统计短时间尺度（用于告警）。

---

### 延迟

#### P99

```yaml
- record: shortener:redirect_p99_latency_seconds
  expr: histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{method="GET", handler="/{short_code}"}[5m])) by (le))
```

**含义**：短链跳转接口（`GET /{code}`）的总请求次数，按 `code` 和 `cache_hit` 标签分组。

**拆解**：

- `histogram_quantile(φ, b)`用于从直方图（histogram）中计算指定的分位数（quantile）。φ为一个介于 0 和 1 之间的小数，表示所需的分位数。例如，*φ=0.95* 表示 P95。b为一个包含直方图数据的即时向量（instant vector），由 Prometheus 的 `_bucket` 时间序列提供。
- `sum(rate(http_request_duration_seconds_bucket{...}[5m])) by (le)` 
  按`le`分组聚合。这里 `le` 是直方图的一个桶。

---

## 3.告警规则详解

### 1. `ErrorBudgetBurnRateHigh`

```yaml
- alert: ErrorBudgetBurnRateHigh
  expr:  shortener:error_budget_burn_rate > 10
  for: 5m
```

**含义**：过去1小时内，实际的错误比例超过了错误预算的 10 倍。（如P99目标，允许的错误为1%，实际错误率为10%）
注意，此处的时间尺度为1h，短期的错误率很高，说明预算正在被极速烧光。

**为什么设置 `for: 5m`**：避免瞬时的尖刺误报，持续 5 分钟才触发。

#### 直观理解

- **Burn Rate = 1**
  → 按当前速率消耗预算，刚好在窗口结束时用光预算（维持 SLO 边界）
- **Burn Rate = 0.5**
  → 消耗速度只有允许速度的一半 → 很健康
- **Burn Rate = 10**
  → 每小时消耗 10 小时的预算额度 → 几小时内就会烧光预算

### 2. `ErrorBudgetLow`

```yaml
- alert: ErrorBudgetLow
  expr: shortener:error_budget_remaining < 0.3
  for: 5m
```

**含义**：剩余错误预算低于 30%。也就是说，实际表现已经接近 SLO 边缘。例如预算总额是 1% 的请求可失败，当已消耗 0.7% 时，只剩 0.3% 的额度，触发告警。

### 3. `HighLatencySLO`

```yaml
- alert: HighLatencySLO
  expr: shortener:redirect_p99_latency_seconds > 0.2
  for: 2m
```

**含义**：P99延迟超过200ms，且持续超过2m。

---

## 4.监控大盘布局

### 看板总览（建议仪表盘命名：`Shortener - SLO Dashboard`）

| 序号 | 面板名称                     | 类型                  | 核心作用                         |
| ---- | ---------------------------- | --------------------- | -------------------------------- |
| 1    | **整体健康度**               | Stat / Gauge          | 一眼前端看到当前成功率 & P99延迟 |
| 2    | **成功率 SLO 错误预算**      | Gauge + Time series   | 展示剩余预算，决策变更           |
| 3    | **P99 / P90 / P50 延迟趋势** | Time series           | 发现延迟恶化趋势                 |
| 4    | **延迟热力图**               | Heatmap               | 直观展示延迟分布变化             |
| 5    | **请求量 & 错误率**          | Time series (stacked) | 识别流量突增与错误比例           |
| 6    | **按短链维度的 Top N 延迟**  | Table                 | 定位慢短链（业务级可观测性）     |

---

### 面板 1：整体健康度（Stat / Gauge）

**作用**：展示当前（最近5分钟）的成功率和 P99 延迟，是否符合 SLO。

#### 成功率（当前 SLI）

```promql
sum(rate(http_requests_total{method="GET", handler="/{short_code}", status_code=~"2..|3.."}[5m]))
/
sum(rate(http_requests_total{method="GET", handler="/{short_code}"}[5m]))
```

- 单位：百分比（0–1 → 乘以 100）
- 阈值：绿色 >0.99，黄色 0.95~0.99，红色 <0.95

#### P99 延迟（当前值）

```promql
histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{method="GET", handler="/{short_code}"}[5m])) by (le))
```

- 单位：秒（可显示为 ms）
- 阈值：绿色 <0.1，黄色 0.1~0.2，红色 >0.2

**面试亮点**：一眼判断系统是否在 SLO 内，适合做成大数字展示在仪表盘顶部。

---

### 面板 2：成功率 SLO 错误预算

#### Time series 显示预算消耗速率

```promql
shortener:error_budget_burn_rate
```

---

### 面板 3：延迟百分位趋势（Time series）

同时显示 P50、P90、P99，观察延迟变化。

```promql
# P99
histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{method="GET", handler="/{short_code}"}[1m])) by (le))

# P90
histogram_quantile(0.90, sum(rate(http_request_duration_seconds_bucket{method="GET", handler="/{short_code}"}[1m])) by (le))

# P50
histogram_quantile(0.50, sum(rate(http_request_duration_seconds_bucket{method="GET", handler="/{short_code}"}[1m])) by (le))
```

- 线图，时间范围可选 last 1 hour
- Y 轴单位秒（建议显示 ms）

**面试亮点**：说明“P99 突然升高但 P50 正常，说明存在长尾慢请求，可能是某条冷短链或外部依赖问题”。

---

### 面板 4：延迟热力图（Heatmap）

更直观地展示延迟分布的变化（颜色越亮表示请求数越多）。

```promql
sum(rate(http_request_duration_seconds_bucket{method="GET", handler="/{short_code}"}[1m])) by (le)
```

- Grafana 热力图需要选择 **Heatmap** 可视化，并设置 Format 为 **Heatmap**。
- 需要配置 Bucket bound 从 `le` 标签中提取。

**替代方案**：如果觉得热力图配置复杂，可以使用 **Histogram** 面板（柱状堆叠）。

**面试亮点**：“热力图能一眼看出延迟分布的变化趋势，比如从集中在 20ms 变成分布在 100ms，说明系统性能劣化。”

---

### 面板 5：请求量与错误率（Stacked time series）

#### 总请求 QPS

```promql
sum(rate(http_requests_total{method="GET", handler="/{short_code}"}[1m]))
```

#### 错误请求 QPS（4xx/5xx，不含 404 可酌情）

```promql
sum(rate(http_requests_total{method="GET", handler="/{short_code}", status_code=~"4..|5.."}[1m]))
```

使用 Stacked area 或两条线，便于观察错误率是否随流量增长而增长。

**面试亮点**：“通过观察错误率和流量的相关性，可以判断是系统过载导致错误，还是外部攻击/爬虫导致。”

---

### 面板 6：按短链维度的 Top N 延迟（Table）

这个面板**非常能体现业务可观测性**：你可以直接看到哪个短链跳转最慢。

**前提：你需要自定义一个 Histogram 带上 `short_code` 标签。**[见下方详解](# 5.高延迟短链Top N详解)

```promql
# 每个短链的 P99 延迟
histogram_quantile(0.99, sum(rate(shortener_redirect_duration_seconds_bucket{}[1h])) by (le, short_code))
```

- 在 Table 中显示 `path` 和 `value`，排序降序。
- 只显示 Top 5 或 Top 10。

**面试亮点**：“我可以快速定位到具体是哪个短链变慢了，然后去查它的原站是否响应慢，或者缓存策略是否失效。”

## 5.高延迟短链Top N详解

**问题：**默认的指标`http_request_duration_seconds_bucket`里不携带 `short_code` 标签，因此无法按`short_code` 分组聚合来按每个`short_code` 分别统计延迟。

**目标：**自定义携带`short_code`的指标

### 自定义指标与埋点

在app代码中，自定义以下指标（主要用到这个`shortener_redirect_duration_seconds`）

```python
from prometheus_client import Counter, Histogram

# ========== 自定义指标，注入short_code、cache_hit状态 ==========
# 自定义：带 short_code 的计数器
redirect_requests_total = Counter(
    'shortener_redirect_requests_total',
    'Total redirect requests by short_code and status',
    ['short_code', 'status_code', 'cache_hit']
)

# 自定义：带 short_code 的延迟直方图
redirect_duration = Histogram(
    'shortener_redirect_duration_seconds',
    'Redirect request duration by short_code',
    ['short_code', 'cache_hit'],
    buckets=(0.01, 0.05, 0.1, 0.5, 1)
)
```

在跳转接口中埋点，修改 `redirect` 函数：

```python
@app.get("/{short_code}")
def redirect(short_code: str, request: Request, ...):
    start_time = time.time()
    try:
        # ... 原有逻辑 ...
        cache_hit = "hit" if cached_url else "miss"
        redirect_requests_total.labels(short_code=short_code, status_code=200, cache_hit=cache_hit).inc()
        redirect_duration.labels(short_code=short_code, cache_hit=cache_hit).observe(time.time() - start_time)
        return RedirectResponse(url=original_url)
    except HTTPException as e:
        redirect_requests_total.labels(short_code=short_code, status_code=500, cache_hit="unknown").inc()
        raise
```

这样 Prometheus 就会得到类似这样的指标：

```text
shortener_redirect_requests_total{short_code="abc123", status="success", cache_hit="hit"} 
shortener_redirect_requests_total{short_code="abc123", status="success", cache_hit="miss"} 
```

使用下方查询语句

```text
histogram_quantile(0.99, sum(rate(shortener_redirect_duration_seconds_bucket{}[1h])) by (le, short_code))
```

可以得到类似这样的结果：

![msedge_ckmhZPSilv](D:\Study\Note\project\shortener\docs\imgs\msedge_ckmhZPSilv.png)

### 配置面板

#### 设置显示Instant查询结果

在面板的查询界面，你可能会看到类似下面的结果：

| Time | short_code | Value |
| ---- | ---- | ---- |
| 2026-05-31 22:16:06.000 | edylVX | 0.859 |
| 2026-05-31 22:16:07.000 | edylVX | 0.859 |

即Table中展示了同一 `short_code ` 的不同时间查询结果，而非像上方图中那样每个 `short_code ` 只显示一个结果。

**解决方法：**在 Grafana 的 Table 面板编辑界面：

1. 点击查询选项卡的 **“Options”**（或右上角的 **“Query options”**）
2. 找到 **“Format”**，选择 **“Table”**
3. 找到 **“Type”** 或 **“Query type”**，从 `Range` 改为 **`Instant`**
   - 有些版本显示为 **“Instant”** 复选框，勾选它即可
4. 保存并刷新

这样 Grafana 只会向 Prometheus 请求**当前时刻**（查询结束时间）的值，而不是一个时间范围内的多个点，于是每个 `short_code` 只会返回一行。

#### 设置一键跳转链接

**目标：**在我们知道哪些为高延迟短链后，我们希望能看到对应的日志以排查问题，因此需要配置一键跳转链接（就像上一章配置的 Log2Trace那样）。

在 Grafana 右侧的覆写配置（**Overrides**）中为 `short_code` 添加 **Data link**并配置 **Url**，如图所示：

![kWCy4bGzW2](D:\Study\Note\project\shortener\docs\imgs\kWCy4bGzW2.png)

![msedge_yDCg7LvVpP](D:\Study\Note\project\shortener\docs\imgs\msedge_yDCg7LvVpP.png)

---

### Url 详解

```text
/explore?left={"datasource":"Loki","queries":[{"refId":"A","expr":"{job=\"shortener-service\"} |= `\"short_code\":\"${__data.fields.short_code}\"`"}],"range":{"from":"now-1h","to":"now"}}
```

#### 整体结构

```text
/explore?left={ ... }
```

- `/explore`：Grafana 中的 Explore 视图，用于交互式查询日志/指标。
- `left`：表示左侧面板（Grafana 支持左右对比查询）。

#### 核心参数详解

1. 数据源

```json
"datasource":"Loki"
```

- 指定使用 **Loki** 作为日志数据源。

2. 查询数组

```json
"queries":[{
  "refId":"A",
  "expr":"{job=\"shortener-service\"} |= `\"short_code\":\"${__data.fields.short_code}\"`"
}]
```

- **`refId: "A"`**：查询的唯一标识符。
- **`expr`**：LogQL 查询表达式。
  - `{job="shortener-service"}`：过滤出 `job` 标签为 `shortener-service` 的日志流。
  - `|=`：LogQL 的过滤器，表示“包含”该字符串。
  - `\"short_code\":\"${__data.fields.short_code}\"`：查找日志中包含 `"short_code":"某个值"` 的行。
  - `${__data.fields.short_code}`：这是一个 **Grafana 变量**，会从当前上下文（仪表板变量）中动态获取值。

3. 时间范围

```json
"range":{
  "from":"now-1h",
  "to":"now"
}
```

- 查询最近 **1 小时** 的日志。

## 待完成

（AIGC）

### AlertManager

### 主机性能/redis/db监控

（受限于服务器2g内存，请自行配置）

### Redis缓存预热

**问题**：某个热门短链因为缓存过期（或从未缓存）导致频繁穿透到 DB，拖慢 P99。

**做法**：

- 写一个**定时任务**（或后台脚本），每隔几分钟扫描延迟 Top 10 的短链。
- 对于这些短链，提前调用业务接口触发缓存加载（例如模拟一次跳转，或直接写 Redis）。
- 可以结合告警：例如当某个短链的 P99 超过阈值 2 分钟，自动调用预热接口。

**落地建议**：

- 写一个 Python 脚本，通过 Prometheus API 拉取延迟最高的 5 个短链，然后对这些短链发送 `GET` 请求。
- 把这个脚本放到 cron job（或用 FastAPI 的 BackgroundTasks 定期执行）。

**面试话术**：

> “我发现热门短链的缓存穿透是延迟的主要来源，所以实现了一个自动预热脚本：每 5 分钟拉取 Prometheus 中延迟最高的短链，主动加载到 Redis。上线后这些短链的 P99 延迟从 300ms 降到了 20ms。”

### 业务健康看板

**问题**：短链服务不光是技术同学用，运营或产品也会关心哪些短链转化率低、体验差。

**做法**：

- 在面板上增加**点击量**和**平均延迟**的表格，按短链聚合。
- 增加一个**健康分数**：例如 `成功率*0.6 + (1-延迟归一化)*0.4`，低分短链需要人工 review。
- 将 Grafana 看板分享给非技术团队（或截图），作为改进依据。

**面试话术**：

> “我还做了一个业务健康看板，产品经理可以看到每个短链的点击量和 P99 延迟。有一次某个营销短链特别慢，导致用户流失，我们根据数据找到原因（下游 CDN 节点问题），和运营一起推动解决。”