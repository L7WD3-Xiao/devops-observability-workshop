import os
import logging
import uuid
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from redis import Redis
from prometheus_fastapi_instrumentator import Instrumentator
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from pythonjsonlogger import jsonlogger
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

from database import SessionLocal, engine, Base
import crud, utils, models

# ========== 1. 修复：更健壮的 trace_id filter ==========
class TraceIdFilter(logging.Filter):
    def filter(self, record):
        # 确保 record 有 trace_id 属性
        if not hasattr(record, 'trace_id'):
            span = trace.get_current_span()
            if span:
                ctx = span.get_span_context()
                if ctx.is_valid:
                    record.trace_id = format(ctx.trace_id, '032x')
                else:
                    record.trace_id = 'no-trace'
            else:
                record.trace_id = 'no-trace'
        return True

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 移除默认的 handler，避免重复
if logger.hasHandlers():
    logger.handlers.clear()

# 创建 console handler
console_handler = logging.StreamHandler()

# 使用 JSON 格式（推荐，避免 KeyError）
formatter = jsonlogger.JsonFormatter(
    '%(asctime)s %(name)s %(levelname)s %(trace_id)s %(message)s',
    rename_fields={
        'asctime': 'timestamp',
        'levelname': 'level',
        'name': 'logger'
    }
)
console_handler.setFormatter(formatter)

# 添加 filter
console_handler.addFilter(TraceIdFilter())
logger.addHandler(console_handler)

# 同时配置 uvicorn 访问日志
logging.getLogger("uvicorn.access").addFilter(TraceIdFilter())

# ========== 2. 初始化 OpenTelemetry ==========
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

# ========== 3. FastAPI 应用 ==========
try:
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified")
except Exception as e:
    logger.warning(f"Database tables already exist or error: {e}")

app = FastAPI(title="Shortener Service", version="1.0.0")

# Prometheus 指标
Instrumentator().instrument(app).expose(app)

# ========== 4. 依赖注入 ==========
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_redis():
    redis_client = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    try:
        yield redis_client
    finally:
        redis_client.close()

# ========== 5. API ==========
@app.post("/shorten")
def shorten(original_url: str, db: Session = Depends(get_db)):
    short_code = utils.generate_short_code()
    crud.create_url(db, short_code, original_url)
    
    logger.info(f"Short URL created", extra={"short_code": short_code, "original_url": original_url})
    return {"short_code": short_code, "short_url": f"http://localhost:8000/{short_code}"}

@app.get("/{short_code}")
def redirect(short_code: str, request: Request, db: Session = Depends(get_db), redis_client: Redis = Depends(get_redis)):
    # 生成一个请求 ID 用于追踪
    request_id = str(uuid.uuid4())[:8]
    
    try:
        with tracer.start_as_current_span("redirect-flow") as span:
            if span:
                span.set_attribute("http.short_code", short_code)
                span.set_attribute("http.request_id", request_id)
            
            # 1. 查缓存
            cache_key = f"short_url:{short_code}"
            cached_url = redis_client.get(cache_key)
            
            if cached_url:
                original_url = cached_url.decode()
                logger.info(f"Cache hit", extra={"short_code": short_code, "request_id": request_id})
                if span:
                    span.set_attribute("cache.hit", True)
            else:
                # 2. 查 DB
                if span:
                    with tracer.start_as_current_span("db-query"):
                        url_map = crud.get_url_by_code(db, short_code)
                        if not url_map:
                            logger.warning(f"Short code not found", extra={"short_code": short_code, "request_id": request_id})
                            raise HTTPException(status_code=404, detail="短链不存在")
                        original_url = url_map.original_url
                else:
                    url_map = crud.get_url_by_code(db, short_code)
                    if not url_map:
                        logger.warning(f"Short code not found", extra={"short_code": short_code, "request_id": request_id})
                        raise HTTPException(status_code=404, detail="短链不存在")
                    original_url = url_map.original_url
                
                if span:
                    span.set_attribute("cache.hit", False)
                
                # 3. 写缓存
                redis_client.setex(cache_key, 300, original_url)
                logger.info(f"Cache miss, loaded from DB", extra={"short_code": short_code, "request_id": request_id})
            
            # 4. 增加点击量
            crud.increment_click_count(db, short_code)
            
            return RedirectResponse(url=original_url)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing redirect: {e}", extra={"short_code": short_code, "request_id": request_id})
        raise HTTPException(status_code=500, detail="Internal server error")
    

@app.get("/health")
def health():
    return {"status": "ok"}