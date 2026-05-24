#!/bin/bash
# check_slo.sh - 检查短链服务的错误预算消耗率是否超标

set -e

# 配置
PROMETHEUS_URL="http://localhost:9090"
THRESHOLD=0.1          # 10% 错误预算消耗率阈值
QUERY='(1 - sum(rate(shortener:redirect_success_total[5m])) / sum(rate(shortener:redirect_total[5m])))'

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

# 检查依赖
if ! command -v curl &> /dev/null; then
    echo "ERROR: curl is required"
    exit 1
fi

if ! command -v jq &> /dev/null; then
    echo "ERROR: jq is required (apt install jq)"
    exit 1
fi

# 查询 Prometheus
response=$(curl -s "${PROMETHEUS_URL}/api/v1/query?query=${QUERY}")
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to query Prometheus at ${PROMETHEUS_URL}"
    exit 1
fi

# 提取结果值
status=$(echo "$response" | jq -r '.status')
if [ "$status" != "success" ]; then
    echo "ERROR: Prometheus query unsuccessful. Response: $response"
    exit 1
fi

result=$(echo "$response" | jq -r '.data.result[0].value[1]')
if [ "$result" == "null" ] || [ -z "$result" ]; then
    echo "WARNING: No data points for the query, assuming 0 burn rate"
    result=0
fi

# 比较
error_rate=$(echo "$result" | bc -l)
if (( $(echo "$error_rate > $THRESHOLD" | bc -l) )); then
    echo -e "${RED}FAIL: Request error rate is ${error_rate} (threshold ${THRESHOLD})${NC}"
    exit 1
else
    echo -e "${GREEN}OK: Request error rate is ${error_rate} (threshold ${THRESHOLD})${NC}"
    exit 0
fi