（AIGC，预计内容，尚未实现）

# 五、高可用 & 容灾

阶段5的目标是：**通过代码层面的关键改动和设计思想，让短链服务具备基础的高可用和容灾能力**。

核心思路不是搭建多机房或全自动故障转移，而是：

1. **消除单点**（服务可水平扩展、依赖有冗余）
2. **可观测性验证**（能发现故障、能自动或手动恢复）
3. **优雅降级**（依赖挂掉时核心功能不崩溃）

下面给出**关键设计 + 必要代码片段**。

---

## 一、消除单点（设计中体现）

### 1.1 短链服务本身无状态

- 服务无状态 = 可多实例 + 前置负载均衡
- 关键代码：确保 `app` 不存本地 Session，所有状态都在 Redis/MySQL

### 1.2 Redis 哨兵模式（模拟高可用）

在 `docker-compose` 中增加一个哨兵拓扑（至少 1 主 2 从 + 3 哨兵）。  
**关键代码片段**（仅示意哨兵配置）：

```yaml
# 仅展示额外服务，不完整
redis-sentinel:
  image: bitnami/redis-sentinel:latest
  environment:
    REDIS_MASTER_HOST: redis-master
    REDIS_MASTER_PORT_NUMBER: 6379
```

**应用层连接哨兵**（使用 `redis-py` 的 `Sentinel` 支持）：

```python
from redis.sentinel import Sentinel

sentinel = Sentinel([('redis-sentinel-1', 26379), ...], socket_timeout=0.1)
redis_client = sentinel.master_for('mymaster', socket_timeout=0.1)
```

### 1.3 MySQL 主从 + 读写分离（可选）

- 写走主库，读走从库（短链跳转是读操作）
- 代码片段：使用 SQLAlchemy 的 `bind` 或两个 Session

```python
# 读库 Session
SessionRead = sessionmaker(bind=read_engine)
# 写库 Session
SessionWrite = sessionmaker(bind=write_engine)
```

---

## 二、健康检查与自动恢复（配合编排）

### 2.1 为短链服务添加 `/health` 和 `/ready` 接口

```python
@app.get("/health")
def health():
    # 检查依赖：Redis, DB
    try:
        redis_client.ping()
        db.execute("SELECT 1")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(503)
```

Docker Compose 或 K8s 中配置：

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
  interval: 10s
  retries: 3
```

---

## 三、容灾模拟：依赖故障下的降级

### 3.1 Redis 缓存完全不可用时的降级

原来的 `redirect` 逻辑：Redis → DB → 写回 Redis。  
当 Redis 连接失败时，直接绕过缓存，只查 DB。

**关键代码片段（不完整）**：

```python
def get_redis_or_none():
    try:
        return Redis.from_url(REDIS_URL, socket_connect_timeout=1)
    except:
        return None

redis_client = get_redis_or_none()
if redis_client:
    cached = redis_client.get(key)
    ...
else:
    logger.warning("Redis unavailable, fallback to DB only")
    # 直接查 DB
```

### 3.2 MySQL 不可用时，短链跳转会失败，但创建短链可以拒绝

此时可以返回 503 并展示友好错误页面。

---

## 四、容灾演练与可观测性验证

### 4.1 手动模拟 Redis 宕机

```bash
docker stop redis
```

观察：

- Metrics：`redis_up` 指标变为 0
- 日志：出现 `Redis unavailable` 降级日志
- 请求仍然成功（只查 DB，延迟增加但可用）

### 4.2 模拟服务实例下线

```bash
docker stop app
```

负载均衡应自动剔除该实例（如果有 Nginx 或 Docker Compose 的 `depends_on` 不是 LB，需要简单反向代理）。 
**代码无关**，但面试要能说：“我可以配置 Nginx upstream 或使用 Docker swarm 的 routing mesh 实现自动剔除”。

---

## 五、面试中如何讲述阶段4

> “我在设计上着重消除了单点：服务本身无状态，Redis 用哨兵模式，MySQL 可主从。并且实现了健康检查接口，配合编排可以实现自动重启。  
> 最关键的是**降级**：当 Redis 不可用时，我会记录告警日志，但请求仍然能穿透到数据库，保证核心跳转功能不中断。  
> 为了验证容灾，我手动停止 Redis 容器，观察到了降级流程和 Prometheus 告警，并且业务成功率没有下降（只是延迟略有升高）。  
> 这种设计让我体会到：高可用不是依赖永远不坏，而是坏的时候系统还能以受损模式工作。”

---

## 六、需要你动手验证的关键点（无代码）

- 把 Redis 停掉，发几个请求，确认依然能跳转
- 查看日志是否输出了降级信息
- 观察 Grafana 里 `redis_up` 指标变化
- 同时启动两个 `app` 实例，用 `ab` 或 `curl` 轮流请求，证明无状态

