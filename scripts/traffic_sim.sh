#!/bin/bash
SHORT_CODE=$(curl -s -X POST "http://localhost:8000/shorten?original_url=https://www.baidu.com" | jq -r '.short_code')

for i in {1..500}
do
    curl -sL -w "%{http_code}\n" -o /dev/null "http://localhost:8000/${SHORT_CODE}"
    sleep 0.2
done