

# 五、高可用 & 容灾

阶段5的目标是：**通过代码和配置层面的关键改动和设计思想，让短链服务具备基础的高可用和容灾能力**。

核心思路不在于搭建多机房或全自动故障转移（这部分请自行学习K8s集群相关内容），而是：

1. **消除单点**（服务可水平扩展、依赖有冗余）
2. **可观测性验证**（能发现故障、能自动或手动恢复）
3. **优雅降级**（依赖挂掉时核心功能不崩溃）

下面按配置难度从易到难进行讲解。

---

## 1.健康检查与自动恢复

为短链服务添加 `/health` 和 `/ready` 接口

```python
@app.get("/health")
def health():
    # 简单点，只要app活着就行
    return {"status": "ok"}
```

Docker Compose 或 K8s 中配置：

```yaml
healthcheck:
  # slim 镜像无 curl，用 python urllib 探测（避免为检查工具增大镜像）
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5)"]
  interval: 10s
  retries: 3
```

---

## 2.消除单点（仅演示思想）

### 2.1 短链服务本身无状态

- 服务无状态 = 可多实例 + 前置负载均衡（虽然本项目没配多实例和负载均衡，请读者自行学习 Ngnix 相关内容）
- `app` 不存本地 Session，所有状态都在 Redis/MySQL**（无需额外配置）**

### 2.2 Redis 哨兵模式（模拟高可用）

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

### 2.3 MySQL 主从 + 读写分离

- 写走主库，读走从库（短链跳转是读操作）
- 代码片段：使用 SQLAlchemy 的 `bind` 或两个 Session

```python
# 读库 Session
SessionRead = sessionmaker(bind=read_engine)
# 写库 Session
SessionWrite = sessionmaker(bind=write_engine)
```

---

## 3.容灾模拟：依赖故障下的降级

### Redis 缓存完全不可用时的降级

原来的 `redirect` 逻辑：Redis → DB → 写回 Redis。 
当 Redis 连接失败时，直接绕过缓存，只查 DB。

**关键代码片段（删减版，仅保留代码逻辑）**：（可直接使用仓库中的示例）

```python
@app.get("/{short_code}")
def redirect(short_code: str, request: Request, db: Session = Depends(get_db)):
        with tracer.start_as_current_span("redirect-flow") as span:
            redis_client, redis_degraded = get_redis_with_fallback()
             # 尝试从 Redis 读取（仅当 Redis 可用且未降级）
            if redis_client is not None and not redis_degraded:
                try:
                    cache_key = f"short_url:{short_code}"
                    cached_url = redis_client.get(cache_key)
                    if cached_url:
                        logger.info(f"Cache hit", extra={"short_code": short_code})
                    else:
                        logger.info(f"Cache miss", extra={"short_code": short_code})
                except RedisError as e:
                    # 读取失败，记录但继续走 DB（不抛异常）

            else:
                # Redis 已降级，记录降级事件
            
            # 3. 缓存未命中或 Redis 降级 → 查询 DB
            if original_url is None:
                with tracer.start_as_current_span("db-query"):
                    url_map = crud.get_url_by_code(db, short_code)
                    logger.info(f"Loaded from DB", extra={"short_code": short_code})
                
                # 4. 写缓存
                if redis_client:
                    # xxx
            
            # 5. 增加点击量
            crud.increment_click_count(db, short_code)

            # 6. 如果处于降级模式，在响应头中标记（用于监控）
            if redis_degraded:
                response = RedirectResponse(url=original_url)
                response.headers["X-Redis-Degraded"] = "true"
                return response
            

            return RedirectResponse(url=original_url)
```

**注意：上述代码使用额外配置的 redis 熔断器（未展示） 和 带降级逻辑的 redis 客户端获取函数（未展示）**

```python
class CircuitBreakerState:
    # 熔断器实现

def get_redis_with_fallback():
    """
    带降级逻辑的 Redis 客户端获取函数
    返回 (redis_client, degraded)
    - redis_client: Redis 客户端或 None
    - degraded: 是否已降级（True 表示 Redis 不可用，使用 DB 兜底）
    """
```

---

### 容灾演练

手动模拟 Redis 宕机

```bash
docker compose stop redis
```

**注：若配置了主从结构和哨兵，请停掉所有Redis节点**

观察：

- Metrics：查询自定义的降级次数`shortener_redis_degradation_total{}` 
- 日志：出现 `Redis degraded mode active, bypassing cache` 等降级或 Redis 不可用日志
- 请求仍然成功（只查 DB，延迟增加但可用）

日志示例，出现WARN但仍可用：

![msedge_To5JLNSLpT](D:\Study\Note\project\shortener\docs\imgs\msedge_To5JLNSLpT.png)

---

## 4.面试中如何讲述阶段5

> “我在设计上着重消除了单点：服务本身无状态，Redis 用哨兵模式，MySQL 可主从。并且实现了健康检查接口，配合编排可以实现自动重启。 
>
> 最关键的是**降级**：当 Redis 不可用时，我会记录告警日志，但请求仍然能穿透到数据库，保证核心跳转功能不中断。 
>
> “我为短链服务实现了 Redis 不可用时的优雅降级，核心设计包括：
>
> 1. **快速失败**：设置 Redis 连接/读写超时（1秒/2秒），避免请求阻塞
> 2. **熔断保护**：连续3次失败后打开熔断器，30秒后尝试恢复，防止雪崩
> 3. **监控打点**：降级事件被记录到 Prometheus，支持告警
> 4. **响应标记**：降级时在 HTTP 响应头添加 `X-Redis-Degraded: true`，方便上游感知
>
> 有一次我在压测时手动停止 Redis 容器，观察到：
>
> - 前几个请求出现超时（记录了降级指标）
> - 熔断器打开后，后续请求直接走 DB，延迟稳定但稍高
> - 重启 Redis 后，系统在30秒内自动恢复缓存
>
> 这个设计保证了 Redis 故障时系统不会完全不可用，只是性能从 5ms 降级到 30ms（DB 查询），核心功能依然可用。”
>
> 高可用不是依赖永远不坏，而是坏的时候系统还能以受损模式工作。”

---

