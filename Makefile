# Makefile
.PHONY: up up-all down down-obs restart logs build test

up:
	docker compose up -d

up-all:
	docker compose --profile observability up -d
	@echo "✅ All services started with observability"

down:
	docker compose --profile observability down
	docker compose down

down-obs:
	docker compose stop prometheus tempo loki grafana

restart-obs:
	docker compose --profile observability restart prometheus tempo loki grafana

logs-app:
	docker compose logs -f app

logs-l:
	docker compose logs -f loki

logs-t:
	docker compose logs -f tempo

status:
	docker compose ps

build:
	docker compose build

test:
	curl -X POST "http://localhost:8000/shorten?original_url=https://www.baidu.com"