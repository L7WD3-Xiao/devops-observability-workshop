## 三、改进方向

---

### 3.1 测试覆盖

**现状：** 没有任何单元测试或集成测试，CI 中的 test stage 是占位符。

**改进：**
- 用 `pytest` + `httpx.AsyncClient` 为 `/shorten` 和 `/{code}` 编写单元/集成测试
- 用 `testcontainers-python` 在测试中启动 MySQL 和 Redis 容器，实现隔离的集成测试
- 为熔断器编写单元测试：模拟连续失败 → 验证熔断打开 → 等待超时 → 验证半开 → 模拟成功 → 验证关闭

**面试价值：** "我的 CI 中有完整的 pytest 测试套件，包括用 testcontainers 启动的集成测试，确保每次提交都验证核心业务逻辑和降级逻辑。"

---

### 3.2 短码生成策略

**现状：** `random.choices` 生成 6 位随机字符串（大小写字母 + 数字 = 62 种字符），存在碰撞可能。

**问题：**
- 62^6 ≈ 568 亿，看似很大，但根据生日悖论，约 7500 万次生成后碰撞概率达 50%
- 碰撞后没有重试机制，`short_code` 的 `UNIQUE` 约束会导致 `IntegrityError`

**改进：**
- 方案 A：改为自增 ID + Base62 编码（如 `hashids` 库），保证无碰撞
- 方案 B：保留随机生成，加入碰撞重试（最多 3 次）
- 方案 C：使用 nanoid 算法，更安全且可配置长度

---

### 3.3 指标高基数治理

**现状：** `shortener_redirect_duration_seconds` 和 `shortener_redirect_requests_total` 使用 `short_code` 作为标签。

**改进：**
- 添加指标清理机制：定期删除不活跃短链的时间序列（Prometheus 的 `drop` 规则或 `tsdb` API）
- 将 `short_code` 标签替换为分桶标签（如按短链热度分 top / normal / cold），在保留分析能力的同时控制基数
- 为 Prometheus 配置 `series_limit` 防止 OOM

---

### 3.4 AlertManager

**现状：** Prometheus 配置了告警规则，但没有 AlertManager，告警只停留在 Prometheus UI 中。

**改进：**
- 部署 AlertManager 容器，配置告警路由（如发往企业微信/钉钉/邮件）
- 配置告警分组、抑制和静默策略
- 演示完整的告警链路：错误预算燃烧率超标 → Prometheus 触发告警 → AlertManager 路由 → 钉钉群收到通知

---

### 3.5 负载均衡与服务多实例

**现状：** 只有一个 app 实例，没有负载均衡。

**改进：**

- 在 docker-compose 中配置 `deploy.replicas: 2`，前置 Nginx 做反向代理和负载均衡
- 演示滚动更新：`docker compose up -d --no-deps --scale app=2 app`
- 配置 Nginx 的 upstream 健康检查，自动摘除不健康实例

**面试价值：** 体现对"服务可水平扩展"的实际验证，而不只是口头说"无状态所以可扩展"。

---

### 3.6 Redis 哨兵与 MySQL 主从

**现状：** Redis 单节点 + MySQL 单节点，高可用只在代码层面做了降级。

**改进：**

- 在 docker-compose 中部署 Redis 哨兵拓扑（1 主 2 从 + 3 哨兵），app 使用 `redis.sentinel.Sentinel` 连接
- 可选：配置 MySQL 主从复制 + 读写分离（短链跳转是读操作，可走从库）
- 容灾演练升级：停掉 Redis 主节点 → 观察哨兵自动选举新主 → 应用层自动重连

---

### 3.7 镜像管理

**现状：** CI 流程中不推送镜像到 Registry，而是在目标主机上 `git clone + docker compose build`。

**改进：**
- 在 CI 中构建镜像并推送到 Docker Hub 或阿里云 ACR
- 使用语义化 tag（如 `shortener:v1.2.3-<commit-sha>`）
- 生产部署改为 `docker compose pull + up -d`，避免在服务器上构建（节省资源 + 可复现）

---

### 3.8 安全性

**现状：** `.env` 中硬编码了数据库密码（`123456`），docker-compose 中也有明文密码。

**改进：**

- 使用 Docker Secrets 管理敏感配置
- `.env` 加入 `.gitignore`（已配置），提供 `.env.example` 作为模板
- CI/CD 中使用 GitHub Secrets 传递环境变量，不在仓库中存储
- 为 MySQL 配置非 root 用户，限制 app 的数据库权限

---

### 3.9 其他可选项

| 改进项                        | 说明                                       | 面试加分点                                              |
| ----------------------------- | ------------------------------------------ | ------------------------------------------------------- |
| **缓存预热**                  | 定时任务扫描高延迟短链，主动加载到 Redis   | "我实现了自动预热脚本，热门短链 P99 从 300ms 降到 20ms" |
| **金丝雀发布**                | 先将 10% 流量导入新版本，观察 SLO 后再全量 | 体现发布风险控制能力                                    |
| **Prometheus 联邦**           | 多实例 Prometheus 聚合，支持多环境监控     | 体现规模化监控思维                                      |
| **Grafana Dashboard as Code** | 用 JSON 文件定义 Dashboard，纳入版本管理   | 体现 Infrastructure as Code 理念                        |
| **限流/防刷**                 | 对 IP 或 short_code 做请求频率限制         | 短链服务常见的安全需求                                  |