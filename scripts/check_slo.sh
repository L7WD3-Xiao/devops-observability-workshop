#!/bin/bash

# 查询Prometheus（内网地址）
PROMETHEUS_URL="localhost:9090"

# 查询主机上的 Prometheus （需要主机 Prometheus 端口可被 GitHub Actions 访问）
RESULT=$(curl -s "http://$PROMETHEUS_URL/api/v1/query?query=shortener:error_budget_burn_rate" | jq -r '.data.result[0].value[1]')
if [ -z "$RESULT" ] || [ "$RESULT" = "null" ]; then
    echo "错误预算燃烧率获取失败: $RESULT"
    exit 1
fi

# 使用 awk 进行数值比较，更安全
if awk "BEGIN {exit(!($RESULT > 10))}"; then
    echo "错误预算燃烧率过高: $RESULT，流水线失败，阻止部署到生产"
    exit 1
else
    echo "SLO 正常，错误预算燃烧率: $RESULT"
    exit 0
fi