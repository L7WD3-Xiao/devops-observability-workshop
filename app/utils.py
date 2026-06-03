import random
import string
import logging
import time
from pythonjsonlogger import jsonlogger
from opentelemetry import trace

def generate_short_code(length: int = 6) -> str:
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))

def safe_span_setattr(span, key, value):
    if span:
        span.set_attribute(key, value)

def json_logger_with_trace_id_filter():
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
    return logger

def get_circuit_breaker(logger):
    class CircuitBreakerState:
        """简单的熔断器状态（可选进阶功能）"""
        def __init__(self):
            self.consecutive_failures = 0
            self.last_failure_time = 0
            self.is_open = False
        
        def record_failure(self):
            self.consecutive_failures += 1
            self.last_failure_time = time.time()
            if self.consecutive_failures >= 3:  # 连续3次失败触发熔断
                self.is_open = True
                logger.warning("Redis circuit breaker OPEN")
        
        def record_success(self):
            self.consecutive_failures = 0
            if self.is_open:
                self.is_open = False
                logger.info("Redis circuit breaker CLOSED")
        
        def should_allow(self) -> bool:
            if not self.is_open:
                return True
            # 熔断后30秒尝试恢复
            if time.time() - self.last_failure_time > 30:
                self.is_open = False
                logger.info("Redis circuit breaker HALF-OPEN (attempting recovery)")
                return True
            return False

    circuit_breaker = CircuitBreakerState()
    return circuit_breaker