import os
import logging
import uuid
import time
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from redis import Redis
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter, Histogram, Gauge
from redis.exceptions import RedisError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

from database import SessionLocal, engine, Base
from utils import safe_span_setattr
import crud, utils, models

SHORT_CODE_LENGTH = 6

# ========== 自定义指标 ==========
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

redis_degradation_total = Counter(
    'shortener_redis_degradation_total',
    'Total number of Redis degradation events',
    ['reason']  # reason: connection_error, timeout, other
)

redis_circuit_breaker_state = Gauge(
    'shortener_redis_circuit_breaker_state',
    'Redis circuit breaker state (0=closed, 1=open)'
)

logger = utils.json_logger_with_trace_id_filter()

# ========== 初始化 OpenTelemetry ==========
try:
    resource = Resource(attributes={SERVICE_NAME: "shortener-service"})
    # 创建 TracerProvider
    provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # 创建 LoggerProvider
    logger_provider = LoggerProvider(resource=resource)
    # 添加 OTLP 导出器（指向 Alloy）
    otlp_log_exporter = OTLPLogExporter()
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(otlp_log_exporter))
    set_logger_provider(logger_provider)

    # 创建 LoggingHandler 将标准 logging 日志桥接到 OpenTelemetry
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    logging.getLogger().addHandler(handler)

    # 自动埋点
    FastAPIInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument(engine=engine)
    RedisInstrumentor().instrument()
    
    logger.info("OpenTelemetry initialized successfully")
except Exception as e:
    logger.warning(f"OpenTelemetry initialization failed: {e}. Continuing without tracing.")
    # 创建空的 tracer provider
    provider = TracerProvider()
    trace.set_tracer_provider(provider)

tracer = trace.get_tracer(__name__)

# ========== FastAPI 应用 ==========
try:
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified")
except Exception as e:
    logger.warning(f"Database tables already exist or error: {e}")

app = FastAPI(title="Shortener Service", version="1.0.0")

# Prometheus 指标
# v7 默认低分辨率桶 (0.1, 0.5, 1) 会使 P99 失真（全部压到 100ms 桶顶），显式使用细桶
Instrumentator().instrument(
    app,
    latency_lowr_buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1),
).expose(app)

# ========== 依赖注入 ==========
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

circuit_breaker = utils.get_circuit_breaker(logger=logger)

def get_redis_with_fallback():
    """
    带降级逻辑的 Redis 客户端获取函数
    返回 (redis_client, degraded)
    - redis_client: Redis 客户端或 None
    - degraded: 是否已降级（True 表示 Redis 不可用，使用 DB 兜底）
    """
    redis_client = None
    degraded = False
    
    # 检查熔断器是否允许访问
    if not circuit_breaker.should_allow():
        degraded = True
        redis_degradation_total.labels(reason="circuit_breaker_open").inc()
        logger.warning("Redis circuit breaker open, using degraded mode")
        return None, True
    
    try:
        # 设置连接超时和读写超时（避免长时间阻塞）
        redis_client = Redis.from_url(
            os.getenv("REDIS_URL"),
            socket_connect_timeout=1,   # 连接超时 1 秒
            socket_timeout=2,            # 读写超时 2 秒
            retry_on_timeout=False,      # 不自动重试，快速失败
            health_check_interval=30
        )
        # 测试连接（Ping 操作也会计入超时）
        redis_client.ping()
        circuit_breaker.record_success()
        redis_circuit_breaker_state.set(0)
        return redis_client, False
    except (RedisConnectionError, RedisTimeoutError) as e:
        # 连接失败或超时 → 降级
        degraded = True
        circuit_breaker.record_failure()
        redis_circuit_breaker_state.set(1 if circuit_breaker.is_open else 0)
        redis_degradation_total.labels(reason="connection_or_timeout").inc()
        logger.error(f"Redis connection/timeout error: {e}, entering degraded mode")
        return None, True
    except RedisError as e:
        # 其他 Redis 错误
        degraded = True
        circuit_breaker.record_failure()
        redis_degradation_total.labels(reason="other_redis_error").inc()
        logger.error(f"Redis error: {e}, entering degraded mode")
        return None, True
    except Exception as e:
        # 未知错误
        degraded = True
        redis_degradation_total.labels(reason="unexpected").inc()
        logger.error(f"Unexpected error connecting to Redis: {e}, entering degraded mode")
        return None, True

# ========== API ==========
@app.post("/shorten")
def shorten(original_url: str, db: Session = Depends(get_db)):
    short_code = utils.generate_short_code(SHORT_CODE_LENGTH)
    crud.create_url(db, short_code, original_url)
    
    logger.info(f"Short URL created", extra={"short_code": short_code, "original_url": original_url})
    return {"short_code": short_code}

@app.get("/{short_code}")
def redirect(short_code: str, request: Request, db: Session = Depends(get_db)):
    # 拦截非目标请求（扫描/恶意请求）
    if len(short_code) != SHORT_CODE_LENGTH:
        raise HTTPException(status_code=404)

    start_time = time.time()
    request_id = str(uuid.uuid4())[:8]
    
    try:
        with tracer.start_as_current_span("redirect-flow") as span:
            redis_client, redis_degraded = get_redis_with_fallback()
            if span:
                span.set_attribute("http.short_code", short_code)
                span.set_attribute("http.request_id", request_id)
                span.set_attribute("redis.degraded", redis_degraded)
            
            original_url = None
            cache_hit = False
             # 尝试从 Redis 读取（仅当 Redis 可用且未降级）
            if redis_client is not None and not redis_degraded:
                try:
                    cache_key = f"short_url:{short_code}"
                    cached_url = redis_client.get(cache_key)
                    if cached_url:
                        original_url = cached_url.decode()
                        cache_hit = True
                        logger.info(f"Cache hit", extra={"short_code": short_code})
                        safe_span_setattr(span, "cache.hit", True)
                    else:
                        logger.info(f"Cache miss", extra={"short_code": short_code})
                        safe_span_setattr(span, "cache.hit", False)
                except RedisError as e:
                    # 读取失败，记录但继续走 DB（不抛异常）
                    logger.warning(f"Redis get failed: {e}, falling back to DB")
                    redis_degradation_total.labels(reason="read_error").inc()
                    redis_client = None  # 标记不可用，后续不走回写
            else:
                # Redis 已降级，记录降级事件
                logger.warning(f"Redis degraded mode active, bypassing cache", extra={"short_code": short_code})
                safe_span_setattr(span, "cache.bypassed", True)
            
            # 3. 缓存未命中或 Redis 降级 → 查询 DB
            if original_url is None:
                with tracer.start_as_current_span("db-query"):
                    url_map = crud.get_url_by_code(db, short_code)
                    if not url_map:
                        logger.warning(f"Short code not found", extra={"short_code": short_code, "request_id": request_id})
                        raise HTTPException(status_code=404, detail="短链不存在")
                    original_url = url_map.original_url
                    logger.info(f"Loaded from DB", extra={"short_code": short_code})
                
                # 4. 写缓存
                if redis_client:
                    try:
                        cache_key = f"short_url:{short_code}"
                        redis_client.setex(cache_key, 300, original_url)
                        logger.debug(f"Cache backfilled", extra={"short_code": short_code})
                    except RedisError as e:
                        # 回写失败不影响主流程
                        logger.warning(f"Redis setex failed: {e}", extra={"short_code": short_code})
            
            # 5. 增加点击量
            crud.increment_click_count(db, short_code)

            # 6. 无论是否降级都记录耗时（降级请求标记 cache_hit="degraded"，保证自定义指标全流量可见）
            #    状态码记录真实响应码（RedirectResponse 默认为 307）
            if redis_degraded:
                response = RedirectResponse(url=original_url)
                response.headers["X-Redis-Degraded"] = "true"
                cache_hit_label = "degraded"
            else:
                response = RedirectResponse(url=original_url)
                cache_hit_label = str(cache_hit)

            redirect_requests_total.labels(short_code=short_code, status_code=response.status_code, cache_hit=cache_hit_label).inc()
            redirect_duration.labels(short_code=short_code, cache_hit=cache_hit_label).observe(time.time() - start_time)

            return response
    except HTTPException as e:
        # 记录真实状态码（如 404 短链不存在），避免与 500 混淆
        redirect_requests_total.labels(short_code=short_code, status_code=e.status_code, cache_hit="unknown").inc()
        raise
    except Exception as e:
        logger.error(f"Error processing redirect: {e}", extra={"short_code": short_code, "request_id": request_id})
        # 真实 500 也要计入分母，否则 SLI 会漏掉服务故障
        redirect_requests_total.labels(short_code=short_code, status_code=500, cache_hit="unknown").inc()
        raise HTTPException(status_code=500, detail="Internal server error")
    

@app.get("/health")
def health():
    return {"status": "ok"}