# DevOps 校招简历 · 面试准备 · 改进方向

---

## 一、简历项目描述（STAR 法则）

> 以下描述可直接用于简历中的「项目经历」板块，建议精简为 1 个项目条目。

---

### 项目名称：短链服务全链路可观测性平台

**技术栈：** Python · FastAPI · MySQL · Redis · Prometheus · Grafana · Loki · Alloy · Jaeger · OpenTelemetry · GitHub Actions · Docker · k6

---

#### STAR 描述

**Situation（背景）**

独立设计并落地短链服务平台，核心目标不是业务复杂度，而是以短链服务为载体，**端到端地落地现代 SRE 的三大核心能力**：可观测性、SLO 驱动的发布决策、以及依赖故障下的优雅降级。

**Task（任务）**

- 构建一个能承载真实流量的短链服务，并为其搭建完整的可观测性体系（Metrics / Logs / Tracing 三大信号可关联跳转）
- 定义核心 SLO，建立错误预算模型，并将其嵌入 CI/CD 流水线作为质量门禁
- 实现 Redis 不可用时的自动降级与熔断保护，保证核心功能在依赖故障下仍可用
- 搭建从代码提交到部署的完整 CI/CD 流水线，含性能压测与 SLO 校验

**Action（行动）**

1. **可观测性关联落地**
   - 通过 OpenTelemetry SDK 对 FastAPI、SQLAlchemy、Redis 进行自动埋点，Traces 通过 OTLP gRPC 直推 Jaeger
   - 实现 `TraceIdFilter`，为每条结构化 JSON 日志注入当前 Span 的 `trace_id`，Logs 经 Grafana Alloy 转发至 Loki
   - 在 Grafana 中配置 Derived Fields，实现 **Loki 日志 → Jaeger 火焰图**的一键跳转；自定义带 `short_code` 标签的 Histogram 指标，支持 Grafana 面板 → Loki 日志的关联查询

2. **SLO 与错误预算**
   - 在 Prometheus 中定义 SLO 目标（P90可用性 ），通过 recording rules 计算 SLI、错误预算燃烧率（burn rate）和剩余预算百分比
   - 配置 3 条告警规则：燃烧率过高（burn rate > 10，持续 5 分钟）、剩余预算不足 30%、P99 延迟超过 200ms
   - 设计 Grafana SLO 看板：整体健康度、错误预算消耗趋势、P99/P90/P50 延迟分布、按短链维度的 Top N 延迟表

3. **熔断器与优雅降级**
   - 自研轻量熔断器（`CircuitBreakerState`）：连续 3 次失败触发熔断 → 30 秒后半开探测 → 成功则关闭
   - Redis 降级时请求穿透到 MySQL，响应头注入 `X-Redis-Degraded: true` 供上游感知；降级事件上报 Prometheus（`shortener_redis_degradation_total`）
   - 手动停 Redis 容器进行容灾演练：前几个请求超时后熔断器打开，后续请求稳定走 DB（延迟从 ~5ms 升至 ~30ms 稳态降级），重启 Redis 后 30 秒内自动恢复缓存

4. **CI/CD 流水线**
   - 基于 GitHub Actions 实现三阶段流水线：代码检查 → SSH 部署到开发环境 → k6 压测（30 VUs / 30s）+ SLO 门禁
   - SLO 门禁通过 SSH 到测试主机本地查询 Prometheus（不暴露 9090 端口到公网），burn rate > 10 时自动阻止部署
   - 生产部署独立 workflow，限定 `main` 分支 + `production` 环境审批

**Result（结果）**

- 实现 Metrics → Logs → Traces **三信号关联排查**：从 Grafana 发现 P99 异常 → 点击跳转到 Loki 日志 → 通过 trace_id 查看 Jaeger 火焰图，定位到缓存穿透，全链路耗时 < 2 分钟
- CI/CD 流水线在 k6 压测后自动校验 SLO，错误预算燃烧率超标时阻断发布，**将发布质量决策从人工判断变为自动化门禁**
- Redis 容灾演练验证了降级逻辑：熔断器在 3 次失败后打开，服务端稳态降级 P99 约 30–60ms，核心跳转始终可用；恢复后 30 秒内自动闭合。CI 中 k6 的客户端 P99 ~500ms 主要来自 GitHub Runner 跨境 RTT（~400ms），不代表服务端延迟
- 整体方案覆盖 Google SRE Book 核心实践（SLO、错误预算、金丝雀/降级），具备校招面试中的差异化竞争力

---

## 二、面试问题与参考答案

---

### 2.1 可观测性

#### Q1：请介绍你的可观测性架构，三大信号的数据流是怎样的？

**答：**

我的短链服务同时输出 Metrics、Logs、Traces 三大信号，数据流如下：

- **Metrics**：FastAPI 通过 `prometheus-fastapi-instrumentator` 自动暴露 `/metrics` 端点，Prometheus 每 15 秒主动拉取。我还自定义了带 `short_code` 和 `cache_hit` 标签的 Counter 和 Histogram，用于按短链维度分析延迟
- **Logs**：应用使用 `python-json-logger` 输出结构化 JSON 日志，通过自定义的 `TraceIdFilter` 注入当前 Span 的 `trace_id`。日志通过 OpenTelemetry SDK 的 `OTLPLogExporter` 经 gRPC 推送到 Grafana Alloy，Alloy 再转发到 Loki
- **Traces**：通过 `FastAPIInstrumentor`、`SQLAlchemyInstrumentor`、`RedisInstrumentor` 三个自动埋点组件生成 Span，Traces 通过 `OTLPSpanExporter` 直接推送到 Jaeger

**追问：为什么 Logs 走 Alloy 而 Traces 直接走 Jaeger？**

Alloy 在这里充当 OTLP 日志的接收和转换层——Loki 不原生支持 OTLP 协议，需要 Alloy 将 OTLP 日志转换为 Loki 格式。而 Jaeger 原生支持 OTLP gRPC，所以 Traces 可以直接推送，不需要中间层。

---

#### Q2：trace_id 是怎么注入到日志里的？Log2Trace 关联跳转怎么实现的？

**答：**

注入过程分三步：

1. **TraceIdFilter**：我写了一个 `logging.Filter`，在每条日志记录被处理前，从 `trace.get_current_span()` 获取当前 Span 的上下文，将 `trace_id` 格式化为 32 位十六进制字符串并附加到 `record.trace_id`
2. **JSON 格式化**：使用 `python-json-logger` 的 `JsonFormatter`，在格式字符串中引用 `%(trace_id)s`，确保每条日志的 JSON 输出都包含 `trace_id` 字段
3. **桥接到 OTLP**：通过 OpenTelemetry 的 `LoggingHandler` 将标准 Python logging 日志桥接到 `LoggerProvider`，再通过 `OTLPLogExporter` 推送到 Alloy → Loki

Log2Trace 跳转是在 Grafana 数据源配置中实现的：在 Loki 数据源的 `derivedFields` 中配置正则匹配 `"traceid":"([a-f0-9]+)"`，并将匹配到的值作为 URL 参数指向 Jaeger 数据源（通过 `datasourceUid` 关联）。这样在 Loki 日志面板中，每个 trace_id 后面会出现一个 Jaeger 图标，点击直接跳转。

---

#### Q3：为什么用 Alloy 而不是 Promtail 采集日志？

**答：**

Alloy 是 Grafana Labs 推出的统一遥数据采集器（前身是 Grafana Agent），相比 Promtail 的优势是：

1. **多信号统一**：Alloy 同时支持 Metrics、Logs、Traces 的采集和路由，而 Promtail 只支持日志
2. **OTLP 原生支持**：我的应用通过 OpenTelemetry SDK 输出 OTLP 格式的日志，Alloy 内置 `otelcol.receiver.otlp` 组件可以直接接收，而 Promtail 不支持 OTLP
3. **声明式管道**：Alloy 使用 River 配置语言，可以灵活定义数据路由管道（如我的 `otelcol.receiver → otelcol.processor.batch → otelcol.exporter.loki → loki.write`）

---

### 2.2 SLO 与错误预算

#### Q4：你的 SLO 是怎么定义的？错误预算燃烧率公式是什么？

**答：**

我的核心 SLO 是：**短链跳转接口（`GET /{code}`）的可用性 ≥ 90%**。

具体计算：

```
SLI = 成功请求数（2xx/3xx）/ 总请求数   （1小时滚动窗口）
错误预算 = 1 - SLO目标 = 1 - 0.9 = 0.1（即允许 10% 的错误率）
燃烧率 = (1 - SLI) / (1 - SLO目标) = 实际错误率 / 允许的错误率
```

燃烧率的直觉理解：
- **burn rate = 1**：按当前速度消耗预算，刚好在窗口结束时用完（正常边界）
- **burn rate = 0.5**：消耗速度只有允许速度的一半，很健康
- **burn rate = 10**：每小时消耗 10 小时的预算额度，几小时内就会烧光预算

**追问：为什么 SLO 目标设为 90% 而不是 99% 或 99.9%？**

这是为了演示目的刻意降低的。在压测场景下（k6 30 VUs），如果设 99%，错误预算只有 1%，演示时很难触发告警。设 90% 可以在压测中更容易观察到预算消耗和告警触发的完整流程。在生产环境中，通常会设 99.9% 或更高。

---

#### Q5：燃烧率告警和剩余预算告警有什么区别？为什么要两条？

**答：**

它们监控的是错误预算的两个不同维度：

- **`ErrorBudgetBurnRateHigh`（燃烧率 > 10，持续 5 分钟）**：监控短时间窗口（1 小时），检测错误预算是否在**快速消耗**。这是一个"速率告警"，目的是在问题刚发生时就发出信号，留出响应时间
- **`ErrorBudgetLow`（剩余 < 30%，持续 5 分钟）**：监控长时间窗口（30 天），检测**累计消耗**是否过多。这是一个"总量告警"，即使当前燃烧率正常，但过去一个月的累计错误已经快用光预算时也会触发

两条告警互补：燃烧率告警防止"突发故障烧光预算"，剩余预算告警防止"慢性劣化耗尽预算"。这也是 Google SRE 推荐的多窗口多燃烧率告警策略的简化版。

---

#### Q6：你的 CI/CD 中 SLO 门禁是怎么工作的？

**答：**

CI 流水线的第三个 stage 在 k6 压测完成后执行 SLO 校验：

1. 通过 SSH 连接到测试主机，在主机本地执行 `curl localhost:9090` 查询 Prometheus 的 `shortener:error_budget_burn_rate` 指标
2. 用 `awk` 做数值比较（而非 shell 的 `if [ $x -gt 10 ]`，因为 burn rate 是浮点数）
3. 如果 burn rate > 10，流水线 fail，阻止后续的生产部署

**追问：为什么要 SSH 到主机查 Prometheus，而不是直接从 GitHub Actions 查？**

因为 Prometheus 运行在测试服务器的 Docker 网络中，9090 端口不应该暴露到公网。如果直接从 GitHub Actions 查，就需要开放 9090 端口，这违反了零信任原则。通过 SSH 到主机本地查询，Prometheus 完全不暴露公网，同时复用了已有的 SSH 部署通道，安全性更好。

---

### 2.3 高可用与降级

#### Q7：你的熔断器是怎么实现的？状态转换是怎样的？

**答：**

我实现了一个轻量的三态熔断器 `CircuitBreakerState`：

```
CLOSED（正常）──[连续 3 次失败]──→ OPEN（熔断）──[30 秒超时]──→ HALF-OPEN（探测）
    ↑                                                             │
    └────────────[探测成功]────────────────────────────────────────┘
                                                                  │
    OPEN ←───────────[探测失败]────────────────────────────────────┘
```

核心逻辑：

- **CLOSED → OPEN**：`record_failure()` 累加 `consecutive_failures`，达到 3 次时设置 `is_open = True`，记录 `last_failure_time`
- **OPEN → HALF-OPEN**：`should_allow()` 检查当前时间与 `last_failure_time` 的差值，超过 30 秒则允许一次请求通过（半开探测）
- **HALF-OPEN → CLOSED**：如果探测请求成功，`record_success()` 将 `consecutive_failures` 重置为 0，关闭熔断器
- **HALF-OPEN → OPEN**：如果探测请求失败，重新进入熔断状态，`last_failure_time` 更新

**追问：这个熔断器有什么局限性？**

1. **进程内状态**：熔断器是内存中的单例，如果服务重启，状态丢失。生产环境应该用 Redis 或共享存储来持久化熔断状态
2. **无并发保护**：没有用锁保护状态转换，高并发下可能出现竞态条件
3. **半开探测只放一个请求**：更精细的做法是用令牌桶或百分比放行

---

#### Q8：Redis 挂掉后你的系统表现如何？请描述一次完整的故障演练过程。

**答：**

演练步骤和观察：

1. **正常状态**：`make test` 创建短链并跳转，Redis 缓存命中率 100%。轻载下单请求的服务端 P99（Prometheus）：**1G 数据库 / 100M Redis 持久卷**约 5–15ms，**10G 数据库 / 300M Redis 持久卷**约 10–25ms。延迟主要来自每个请求必做的 MySQL 点击量 UPDATE+COMMIT（每次 commit 一次 fsync），Redis GET 本身 < 1ms
2. **`docker compose stop redis`**：手动停掉 Redis 容器
3. **前 3 个请求**：每个请求尝试连接 Redis，因 `socket_connect_timeout=1` 在 1 秒后超时，记录 `redis_degradation_total{reason="connection_or_timeout"}`，熔断器 `consecutive_failures` 累加
4. **第 4 个请求起**：熔断器打开（`is_open = True`），`should_allow()` 返回 False，请求直接跳过 Redis 查 DB，不再尝试连接 Redis。稳态降级延迟（服务端）：1G 场景 P99 约 15–40ms，10G 场景约 30–60ms（缓冲池冷时可达 100ms+），请求仍成功。响应头出现 `X-Redis-Degraded: true`
5. **Prometheus 观察**：`shortener_redis_degradation_total` 递增，`shortener_redis_circuit_breaker_state` 变为 1
6. **`docker compose start redis`**：重启 Redis
7. **30 秒后**：熔断器进入半开状态，允许一个请求尝试连接 Redis。连接成功 → `record_success()` → 熔断器关闭，缓存恢复正常

整个过程核心功能（短链跳转）没有中断，只是性能降级。

**数字口径说明**：

- **演练瞬间 P99 ≠ 稳态降级 P99**：演练窗口前 3 个请求各阻塞约 1s（`socket_connect_timeout=1` 连接超时），若 P99 统计窗口包含这 3 个请求，会看到约 500ms–1s 的尖刺；熔断打开后纯走 DB 的**稳态** P99 才是 step 4 的数字。面试时建议分开表述
- **k6 客户端视角 ≠ 服务端视角**：CI 的 k6 跑在 GitHub Runner（境外），跨境 RTT ~400ms，客户端 P99 ~500ms 是端到端体验；Prometheus 服务端 P99 正常 ~50ms。把 k6 挪到服务器本地跑（真 30 并发）后，服务端 P50≈1.3s、P95≈1.9s——瓶颈在 app 自身（0.5 CPU + 每请求 fsync + 连接池 5），跨境 RTT 反而掩盖了真实容量
- **为什么 10G / 1G 数据库对 P99 影响不大**：`short_code` 是唯一索引点查，B+ 树深度都是 2–3 层，差别只在于缓冲池（MySQL 8 默认仅 128MB）是否命中冷页——10G 场景随机短码更可能读盘（SSD 5–20ms/次），所以 miss 路径 P99 从 ~20ms 抬到 ~50ms
- **真正的 P99 地板在代码里**：每个请求（含缓存命中）都会执行 `increment_click_count`（SELECT+UPDATE+COMMIT，`innodb_flush_log_at_trx_commit=1` 下每次 commit 一次 fsync），加上 SQLAlchemy 默认连接池只有 5 个、app 容器限 0.5 CPU。想降低 P99，应优先改造这三处（异步/批量计数、加大连接池、放宽缓冲池），而不是纠结 Redis 卷大小

---

#### Q9：`get_redis_with_fallback` 里的超时参数是怎么设计的？

**答：**

```python
socket_connect_timeout=1   # 连接超时 1 秒
socket_timeout=2           # 读写超时 2 秒
retry_on_timeout=False     # 不自动重试
```

设计思路：

- **连接超时 1 秒**：Redis 正常时连接延迟 < 1ms，1 秒已经非常宽裕。设太长会导致请求长时间阻塞在 Redis 连接上
- **读写超时 2 秒**：缓存查询正常 < 5ms，2 秒覆盖了极端情况。超过 2 秒说明 Redis 本身有问题（如大 key、内存不足）
- **不自动重试**：快速失败原则。重试会增加延迟，而且 Redis 不可用时重试只会让情况更糟。应该尽快降级到 DB

---

### 2.4 CI/CD

#### Q10：你的 CI/CD 流水线有几个阶段？为什么这么设计？

**答：**

三个阶段，串行执行：

1. **Test**：Python 依赖安装 + 单元测试（目前是占位，预留 pytest 扩展点）
2. **Deploy Dev**：通过 SSH 将代码同步到开发服务器（git clone / reset --hard），执行 `make build` 构建镜像，`docker compose up -d --no-deps app` 只重启 app 不重启依赖。然后跑冒烟测试（创建短链 + 跟随跳转验证 200）
3. **Performance + SLO Gate**：k6 压测（30 VUs / 30s）→ 查询 Prometheus 错误预算燃烧率 → burn rate > 10 则 fail

设计思路是**渐进式质量门**：代码能编译 → 部署能成功 → 性能能达标。每个阶段的失败成本递增（编译失败 < 部署失败 < SLO 不达标），串行设计确保问题尽早暴露。

**追问：为什么生产部署是单独的 workflow 而不是同一个流水线的 stage？**

生产部署需要更高级别的审批控制。单独 workflow 可以：
1. 限定只在 `main` 分支触发，避免 develop 分支的代码直接上生产
2. 使用 GitHub Environments 的 `production` 环境，可以配置部署审批（需要人工确认）
3. 手动触发（`workflow_dispatch`），给运维人员完全的控制权

---

#### Q11：k6 压测的脚本和参数是怎么设计的？

**答：**

```javascript
export let options = {
  vus: 30,          // 30 个虚拟用户并发
  duration: "30s",  // 持续 30 秒
};
export default function () {
  // redirects=0：不跟随 302/307 重定向，只测短链服务本身
  let res = http.get(__ENV.URL, { redirects: 0 });
  check(res, { "status is 3xx": (r) => r.status >= 300 && r.status < 400 });
}
```

参数设计：
- **30 VUs**：对单实例 FastAPI（限 0.5 CPU）+ 单 MySQL + 单 Redis 的架构，30 并发已能压出真实容量上限（服务器本地实测 P50≈1.3s）
- **30 秒**：足够让 Prometheus 采集到 2-3 个数据点（15s scrape interval），供后续 SLO 计算使用
- **检查 3xx**：短链跳转返回 302/307（FastAPI `RedirectResponse` 默认 307），所以成功的标准是 3xx 而非 200；必须 `redirects: 0`，否则 k6 会跟随跳转抓取目标站，延迟数据被污染、检查也永远失败
- **运行位置**：k6 以容器方式跑在 dev 主机本地（`grafana/k6` + `--network host`），避免 GitHub Runner 跨境 RTT（~400ms）污染延迟数据；SLO 门禁仍以 Prometheus 服务端指标为准

压测流程：先在 dev 主机本地创建短链获取 `short_code`，再用 k6 对 `http://localhost:8000/{code}` 施压。

---

### 2.5 基础架构

#### Q12：Docker Compose 中为什么有两个网络？observability 用 profiles 是什么考虑？

**答：**

两个网络：
- **`app-network`**：app + MySQL + Redis，业务流量隔离
- **`observability`**：Prometheus + Grafana + Loki + Alloy + Jaeger，监控流量隔离

app 同时接入两个网络（因为 Prometheus 需要 scrape app 的 `/metrics`，app 需要推送 OTLP 到 Alloy/Jaeger），但 MySQL 和 Redis 只接入 `app-network`，监控组件无法直接访问数据库，这是**最小权限原则**的体现。

`profiles: ["observability"]` 的好处：
- `docker compose up -d` 只启动业务三件套（app + MySQL + Redis），适合本地开发时快速启动
- `docker compose --profile observability up -d` 启动全套，适合调试或演示
- 避免在不需要监控的场景下浪费资源（6 个容器约占 1.5GB 内存）

---

#### Q13：健康检查是怎么做的？

**答：**

两层健康检查：

1. **应用层**：`/health` 端点返回 `{"status": "ok"}`，只要 FastAPI 进程能响应就返回 200。**注意**：`/health` 必须注册在 `/{short_code}`（catch-all）路由**之前**，否则 6 字符的 "health" 会被当成短链查询返回 404（Starlette 按注册顺序匹配路由，我踩过这个坑）
2. **Docker Compose 层**：为 app 容器配置了 `healthcheck`（curl /health，30s 间隔，3 次重试），MySQL 用 `mysqladmin ping`，Redis 用 `redis-cli ping`。app 的 `depends_on` 使用了 `condition: service_healthy`，确保数据库和 Redis 就绪后才启动 app

**追问：`/health` 和 `/ready` 应该有什么区别？**

`/health`（liveness）回答"进程还活着吗"——如果失败，应该重启容器。`/ready`（readiness）回答"能接收流量吗"——如果失败（比如 DB 连接池满了），应该从负载均衡中摘除但不重启。我的项目目前只有 liveness，生产环境应该加上 readiness，检查 DB 连接和 Redis 连接是否正常。

---

### 2.6 自定义指标与高基数

#### Q14：为什么自定义了带 short_code 标签的指标？不怕高基数问题吗？

**答：**

默认的 `http_request_duration_seconds_bucket` 只有 `method`、`handler`、`status` 等标签，无法按短链维度分析延迟。但在实际运营中，我们需要知道**哪个短链变慢了**（可能是它的目标站点响应慢，或缓存策略失效）。

所以我自定义了 `shortener_redirect_duration_seconds` Histogram，带上 `short_code` 和 `cache_hit` 标签，支持：
- Grafana 面板上按短链做 Top N 延迟排行
- 从高延迟短链一键跳转到 Loki 日志（通过 Data Link 配置）

关于高基数：这是一个有意的设计权衡。短链数量如果达到万级别，确实会导致 Prometheus 时间序列膨胀。应对策略：
1. **短期**：控制短码长度为 6 位（52^6 ≈ 190 亿理论空间，但实际活跃短链远少于此）
2. **中期**：可以用 `histogram_quantile` 只保留聚合结果，定期清理不活跃的短链指标
3. **长期**：如果短链规模真的上去了，应该把 `short_code` 从标签移到独立的分析系统（如 ClickHouse），Prometheus 只保留全局聚合指标

**追问：你知道 Prometheus 高基数会导致什么问题吗？**

1. **内存膨胀**：每个唯一标签组合都是一条时间序列，每条序列约占 200 字节内存 + 磁盘存储
2. **查询变慢**：`rate()` 等聚合函数需要扫描更多序列
3. **compaction 变慢**：TSDB 的压缩和合并操作变重
4. **OOM 风险**：极端情况下 Prometheus 可能因内存不足而崩溃

---

