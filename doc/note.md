测试短链

```sh
# 1. 启动所有服务
docker-compose up -d

# 2. 创建短链
curl -X POST "http://localhost:8000/shorten?original_url=https://www.google.com"

# 返回: {"short_code":"aBc123","short_url":"http://localhost:8000/aBc123"}

# 3. 访问跳转（第一次 miss，输出容器日志而非客户端）
# 最后替换为实际得到的short_code
curl -L "http://localhost:8000/[short_code]"

# 4. 第二次访问（命中缓存），通过容器日志查看
docker compose logs app
```

应该能看到日志内容

```sh
shortener-app  | INFO:     172.25.0.1:38972 - "POST /shorten?original_url=https://www.baidu.com HTTP/1.1" 200 OK
shortener-app  | INFO:main:cache miss
shortener-app  | INFO:     172.25.0.1:38656 - "GET /2T4vUl HTTP/1.1" 307 Temporary Redirect
shortener-app  | INFO:main:cache hit
shortener-app  | INFO:     172.25.0.1:38982 - "GET /2T4vUl HTTP/1.1" 307 Temporary Redirect
```


