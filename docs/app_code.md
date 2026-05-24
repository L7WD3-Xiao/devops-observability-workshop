# 说明

业务代码指 `/app` 中的代码，仅 `requirements.txt` 和 `main.py` 需要改动，其余业务代码和项目中提供的保持一致

# 一、最小项目

requirements.txt

```txt
fastapi
SQLAlchemy
pymysql
redis
python-dotenv
pydantic
uvicorn
```

注：若想简化后续完整项目 `dockerfile` 的镜像构建过程，可直接用完整项目的依赖

---

main.py

```python
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from redis import Redis
import json
import logging
import os

from database import SessionLocal, engine, Base
import crud, utils, models

# 建表
Base.metadata.create_all(bind=engine)

app = FastAPI()

# 日志（结构化）
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 依赖：DB Session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 依赖：Redis
def get_redis():
    redis_client = Redis.from_url(os.getenv("REDIS_URL"))
    try:
        yield redis_client
    finally:
        redis_client.close()


@app.post("/shorten")
def shorten(original_url: str, db: Session = Depends(get_db)):
    short_code = utils.generate_short_code()
    crud.create_url(db, short_code, original_url)
    
    # 写日志（预留 trace_id）
    logger.info(f"shorten created", extra={"short_code": short_code, "original_url": original_url})
    
    return {"short_code": short_code, "short_url": f"http://localhost:8000/{short_code}"}


@app.get("/{short_code}")
def redirect(short_code: str, request: Request, db: Session = Depends(get_db), redis_client: Redis = Depends(get_redis)):
    # 1. 尝试从 Redis 获取
    cache_key = f"short_url:{short_code}"
    cached_url = redis_client.get(cache_key)
    
    if cached_url:
        original_url = cached_url.decode()
        logger.info(f"cache hit", extra={"short_code": short_code})
    else:
        # 2. 查 DB
        url_map = crud.get_url_by_code(db, short_code)
        if not url_map:
            raise HTTPException(status_code=404, detail="短链不存在")
        original_url = url_map.original_url
        # 3. 写缓存（5分钟）
        redis_client.setex(cache_key, 300, original_url)
        logger.info(f"cache miss", extra={"short_code": short_code})
    
    # 4. 异步增加点击量（简单起见同步）
    crud.increment_click_count(db, short_code)
    
    return RedirectResponse(url=original_url)

@app.get("/health")
def health():
    return {"status": "ok"}
```



# 二、完整项目

requirements.txt

```txt
fastapi
SQLAlchemy
pymysql
redis
python-dotenv
pydantic
uvicorn
prometheus-fastapi-instrumentator
opentelemetry-distro
opentelemetry-instrumentation-fastapi
opentelemetry-instrumentation-sqlalchemy
opentelemetry-instrumentation-redis
opentelemetry-exporter-otlp
opentelemetry-sdk
python-json-logger
```

main.py

```python
import os
import logging
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from redis import Redis
from prometheus_fastapi_instrumentator import Instrumentator
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
import uuid
import json

from .database import SessionLocal, engine
from . import crud, utils, models

# ========== 1. 初始化 OpenTelemetry ==========
resource = Resource(attributes={SERVICE_NAME: "shortener-service"})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint="http://tempo:4318/v1/traces"))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)

# 自动埋点
FastAPIInstrumentor().instrument()
SQLAlchemyInstrumentor().instrument(engine=engine)
RedisInstrumentor().instrument()

tracer = trace.get_tracer(__name__)

# ========== 2. 结构化日志（带 trace_id）==========
class TraceIdFilter(logging.Filter):
    def filter(self, record):
        span = trace.get_current_span()
        if span:
            ctx = span.get_span_context()
            if ctx.is_valid:
                record.trace_id = format(ctx.trace_id, '032x')
            else:
                record.trace_id = "no-trace"
        else:
            record.trace_id = "no-trace"
        return True

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | trace_id=%(trace_id)s | %(message)s'
)
logger = logging.getLogger(__name__)
logger.addFilter(TraceIdFilter())

# ========== 3. FastAPI 应用 ==========
Base.metadata.create_all(bind=engine)
app = FastAPI()

# Prometheus 指标自动埋点
Instrumentator().instrument(app).expose(app)

# ========== 4. 依赖注入 ==========
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_redis():
    redis_client = Redis.from_url(os.getenv("REDIS_URL"))
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
    with tracer.start_as_current_span("redirect-flow") as span:
        span.set_attribute("http.short_code", short_code)
        
        # 1. 查缓存
        cache_key = f"short_url:{short_code}"
        cached_url = redis_client.get(cache_key)
        
        if cached_url:
            original_url = cached_url.decode()
            logger.info(f"Cache hit", extra={"short_code": short_code})
            span.set_attribute("cache.hit", True)
        else:
            # 2. 查 DB
            with tracer.start_as_current_span("db-query"):
                url_map = crud.get_url_by_code(db, short_code)
                if not url_map:
                    logger.warning(f"Short code not found", extra={"short_code": short_code})
                    raise HTTPException(status_code=404, detail="短链不存在")
                original_url = url_map.original_url
                span.set_attribute("cache.hit", False)
            
            # 3. 写缓存
            redis_client.setex(cache_key, 300, original_url)
            logger.info(f"Cache miss, loaded from DB", extra={"short_code": short_code})
        
        # 4. 增加点击量
        crud.increment_click_count(db, short_code)
        
        return RedirectResponse(url=original_url)
```

