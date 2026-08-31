# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A URL shortener service built as a **DevOps observability teaching project** for SRE/DevOps interview preparation. The shortener is intentionally minimal — the real focus is the surrounding observability, SLO-driven CI/CD, and high-availability patterns.

The project language is **Chinese** (comments, docs, commit messages, alert annotations).

## Common Commands

```bash
# Start app + MySQL + Redis only
make up

# Start everything including observability stack (Prometheus, Grafana, Loki, Alloy, Jaeger)
make up-all

# Stop all services
make down

# Rebuild app image after code changes
make build

# Functional smoke test (create short URL + follow redirect)
make test

# Generate error traffic (hit non-existent short codes)
make test-e

# Sustained traffic simulation (500 requests)
make test-sim

# Check SLO error budget burn rate against local Prometheus
make check

# View logs for specific services
make logs-app    # app
make logs-p      # prometheus
make logs-l      # loki
make logs-j      # jaeger
make logs-a      # alloy
```

There are no unit tests yet. The CI pipeline (`ci-local-build.yml`) has a placeholder for pytest.

## Architecture

### Application (app/)

A single FastAPI process with no sub-packages. All modules live flat in `app/`:

- **main.py** — FastAPI routes, OpenTelemetry initialization, Redis circuit breaker + degradation logic, Prometheus custom metrics
- **database.py** — SQLAlchemy engine/session setup, reads `DATABASE_URL` from `.env`
- **models.py** — Single `URLMap` model (short_code, original_url, click_count, created_at)
- **crud.py** — DB operations (create, lookup, increment clicks)
- **utils.py** — Short code generation, JSON logger with trace_id injection, `CircuitBreakerState` class, `safe_span_setattr` helper

### Request Flow (redirect endpoint)

`GET /{short_code}` is the critical path with observability instrumentation:

1. Validate short_code length (6 chars)
2. Start OpenTelemetry span `redirect-flow`
3. Check Redis circuit breaker → if open, degrade to DB-only
4. Try Redis cache lookup (key: `short_url:{code}`, TTL: 300s)
5. On cache miss or degradation → query MySQL via `crud.get_url_by_code`
6. Backfill Redis cache if Redis is available
7. Increment click count in DB
8. Return 302 redirect with `X-Redis-Degraded: true` header when in degraded mode

Custom Prometheus metrics are labeled with `short_code` and `cache_hit` — this is intentional for demonstrating high-cardinality metric concerns.

### Redis Circuit Breaker

Implemented in `utils.py` as `CircuitBreakerState`: opens after 3 consecutive failures, auto-recovers after 30 seconds (half-open state). Exposed as a Prometheus gauge `shortener_redis_circuit_breaker_state`.

### Observability Data Flow

```
App (OpenTelemetry SDK)
  ├── Metrics → /metrics endpoint → Prometheus scrapes app:8000
  ├── Logs (OTLP gRPC) → Alloy:4317 → Loki
  └── Traces (OTLP gRPC) → Jaeger:4317
```

- **Alloy** acts as the OTLP log receiver and forwards to Loki only (traces go directly to Jaeger)
- **Structured JSON logs** include `trace_id` injected via `TraceIdFilter` in utils.py, enabling Metrics → Logs → Traces correlation in Grafana
- **Prometheus rules** (`prometheus/rules.yml`) define SLO recording rules and alert rules

### SLO & Error Budget

Defined in `prometheus/rules.yml`:
- SLO target: 90% (`slo:target` record) — intentionally low for demo purposes
- SLI: successful redirect rate over 1h window
- Error budget burn rate: `(1 - SLI) / (1 - SLO target)` — alerts when > 10
- Alerts: `ErrorBudgetBurnRateHigh` (warning), `ErrorBudgetLow` (critical, < 30% remaining), `HighLatencySLO` (P99 > 200ms)

### Docker Compose

Two networks: `app-network` (app + MySQL + Redis) and `observability` (monitoring stack). Observability services use `profiles: ["observability"]` so they only start with `make up-all` or `docker compose --profile observability up`.

### CI/CD (GitHub Actions)

Two workflows:
- **ci-local-build.yml** — Main pipeline on `develop` push: test → deploy to dev via SSH → k6 load test → SLO gate（原独立 test.yml 的 SLO 门禁配置已并入此流水线）
- **deploy.yml** — Production deploy (manual trigger, `main` branch only, gated by `production` environment)

The SLO gate queries Prometheus for `shortener:error_budget_burn_rate` and blocks deployment if burn rate > 10. Load testing uses k6 (30 VUs, 5-30s duration).

### Environment

`.env` requires `DATABASE_URL` and `REDIS_URL`. The Docker Compose file also sets `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` and `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` for the app container.