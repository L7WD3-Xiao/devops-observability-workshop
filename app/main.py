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

# 1. 生成短链
@app.post("/shorten")
def shorten(original_url: str, db: Session = Depends(get_db)):
    short_code = utils.generate_short_code()
    crud.create_url(db, short_code, original_url)
    
    # 写日志（预留 trace_id）
    logger.info(f"shorten created", extra={"short_code": short_code, "original_url": original_url})
    
    return {"short_code": short_code, "short_url": f"http://localhost:8000/{short_code}"}

# 2. 跳转（核心逻辑）
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