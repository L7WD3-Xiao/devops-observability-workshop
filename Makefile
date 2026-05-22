# Makefile
.PHONY: up up-all down down-obs restart logs build test

up:
	docker compose up -d

up-all:
	docker compose --profile observability up -d
	@echo "✅ All services started with observability"

down:
	docker compose --profile observability down

down-app:
	docker compose stop app

down-obs:
	docker compose stop prometheus alloy loki grafana jaeger

restart:
	docker compose --profile observability restart prometheus alloy loki grafana jaeger

status:
	docker compose ps -a

build:
	docker compose build

test-0:
	curl -X POST "http://localhost:8000/shorten?original_url=https://www.baidu.com"

test:
	sh scripts/test.sh

test-e:
	sh scripts/test_error.sh

test-sim:
	sh scripts/traffic_sim.sh

logs-app:
	docker compose logs -f app

logs-l:
	docker compose logs -f loki

logs-t:
	docker compose logs -f tempo

logs-a:
	docker compose logs -f alloy

logs-j:
	docker compose logs -f jaeger

logs-p:
	docker compose logs -f prometheus