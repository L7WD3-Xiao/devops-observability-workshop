# 四、CI/CD

## 1.项目结构

```text
shortener/
├── .github/
│   └── workflows/
│       ├── ci.yml          ← 持续集成流程
│       └── deploy.yml      ← 部署流程
├── (其他内容)
├── .gitignore
└── README.md
```

## 2.准备工作

### 2.1.环境准备

- github账号
- 暴露公网IP的服务器，准备好Docker环境
- 为主机配置SSH连接
- 确保主机上已经运行了 Prometheus + Grafana 等可观测性服务（可以用之前配置的 `docker-compose.yml` 启动全套）
- 设置安全组，开放端口，并限制来源IP（可选）

| 端口 | 服务                | 备注                                                         |
| :--- | :------------------ | :----------------------------------------------------------- |
| 22   | SSH (Linux远程登录) | 限制来源IP（可选，因为可能会拦截Github Action出口IP），但确保仅允许密钥认证。 |
| 3000 | Grafana管理面板     | 限制来源IP，管理面板应用经常有历史漏洞，绝不能公网暴露。     |
| 8000 | app短链端口         | 业务端口，无来源限制。但会时不时有扫描器光顾，在本项目中非预期请求均返回404 |
| 9090 | Prometheus          | 临时开放，供Github Action调用                                |

（进阶：可用**动态临时加白名单**的方式临时向Github Action出口IP开放端口，目前先不限制IP开放端口）

### 2.2.配置 Secrets 环境变量

**在 GitHub 仓库 Settings → Secrets and variables → Actions 中添加**

| Secret 名称       | 说明                                                         |
| :---------------- | :----------------------------------------------------------- |
| `DOCKER_USERNAME` | （可选）Docker Hub 用户名                                    |
| `DOCKER_PASSWORD` | （可选）Docker Hub 密码或 Access Token                       |
| `DEV_HOST`        | 开发服务器的 IP 或域名                                       |
| `PROD_HOST`       | （可选）生产服务器的 IP 或域名                               |
| `SSH_USER`        | 服务器的登录用户名（如 root 或 ubuntu）                      |
| `SSH_PRIVATE_KEY` | SSH 私钥（用于免密登录）                                     |
| `PROMETHEUS_DEV`  | 开发环境 Prometheus 的访问地址（例如 `http://dev-server:9090`） |

## 3.整体流水线设计

目标

> **从 `git push` → 自动构建镜像 → 推送到镜像仓库 → 部署到测试环境 → 运行冒烟测试 + 性能测试 → 如果SLO通过，再部署到生产环境**

标准流程

```yml
name: CI/CD Pipeline

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main ]

env:
  REGISTRY: docker.io   # 或阿里云ACR、GHCR
  IMAGE_NAME: yourusername/shortener

jobs:
  # 1. 测试 & 构建
  build-and-test:
    runs-on: ubuntu-latest
    steps:
      - checkout
      - 单元测试
      - 构建Docker镜像
      - 推送到镜像仓库

  # 2. 部署到dev环境
  deploy-dev:
    needs: build-and-test
    runs-on: ubuntu-latest
    steps:
      - 在dev服务器上拉取新镜像并重启
      - 运行冒烟测试（curl验证）

  # 3. 性能测试 + SLO校验（关键）
  performance-test:
    needs: deploy-dev
    runs-on: ubuntu-latest
    steps:
      - 使用k6或wrk对短链API施压
      - 查询Prometheus计算错误燃烧率
      - 如果错误燃烧率过高，则失败流水线

  # 4. 部署到生产（需手动触发或自动）
  deploy-prod:
    needs: performance-test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    environment: production   # GitHub环境，可加审批
    steps:
      - 部署到生产环境
```

## 4.简化版本

此处对上面流水线进行简化，仅进行前三个Job，即构建镜像、部署到测试环境、SLO校验，不部署到生产环境。

同时，简化向 Docker Hub 推送镜像到流程（考虑到国内网络），改成由测试环境直接拉取最新仓库，并在本地构建镜像（就像我们之前做的）。

### 简化版流水线

```yml
name: CI/CD (Local Build)

on:
  push:
    branches: [ develop ]

# 全局环境变量，后续步骤可以直接使用 ${{ env.XXX }}
# 实际并不使用，而是使用的是配置在secrets中的变量
# env:
#   DEV_HOST: your-dev-server.com
#   SSH_USER: your-ssh-username

jobs:
  # 1. 代码检查 + 单元测试（可选）
  test:
    runs-on: ubuntu-latest
    steps:
      - checkout
      - 单元测试

  # 2. 部署到dev环境
  deploy-dev:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - 在dev服务器上拉取最新仓库并构建镜像
      - 重启app
      - 运行冒烟测试（curl验证）

  # 3. 性能测试 + SLO校验（关键）
  performance-test:
    needs: deploy-dev
    runs-on: ubuntu-latest
    steps:
      - 使用k6或wrk对短链API施压
      - 查询Prometheus计算错误率 
      - 如果错误率 > 10%，则失败流水线

# 4. 部署到生产（仅展示逻辑，单独拉个文件配置手动触发）
name: Deploy

# 开启手动触发
on:
  workflow_dispatch:

jobs:
  deploy-prod:
```

具体配置和执行代码可参考文件

### Q&A

#### 1.SLO校验中，通过命令查 Prometheus 显示 400 Bad Request

例如下面的命令

```bash
curl -s "${PROMETHEUS_URL}/api/v1/query?query=${QUERY}"
返回：400 Bad Request
```

这个 400 错误通常是因为 **PromQL 查询语句中的特殊字符没有正确 URL 编码**。 `${QUERY}` 里包含 `>`、`/`、`(` 等符号，curl 默认不会自动编码。

改用下面的查询命令

```bash
curl -s -G "${PROMETHEUS_URL}/api/v1/query" --data-urlencode "query=${QUERY}"
```

#### 2.（弃用）SLO校验中，发现查 Prometheus 很慢，超过2m且查不到数据

检查安全组设置，是否开放了9090端口

**备注：**现版本的配置没这个问题，校验逻辑从 Action主机直接调用 9090 端口查数据 变为**CI通过SSH到测试主机 → 在测试主机本地查询Prometheus → 把结果echo回CI**。

这样做的好处：

- ✅ Prometheus 完全不暴露公网
- ✅ 利用现有的SSH通道（你已经在用ssh部署）
- ✅ 符合**零信任网络**原则

## 5.动态配置防火墙规则

在使用 **GitHub Actions** 进行 CI/CD 部署时，如果目标服务器或防火墙需要配置 IP 白名单，就必须获取 GitHub 托管运行器的出口 IP 范围。GitHub 官方提供了 **Meta API** 来动态获取这些 IP。

**关键点**

- IP 范围采用 **CIDR** 表示法（如 *192.30.252.0/22*），可用在线工具转换为具体 IP 段。
- 列表会随时间变化，需定期更新，避免因 IP 变更导致部署失败。
- 这些 IP 涵盖 **内容分发、Webhook、GitHub Actions 构建** 等服务，但不保证包含 LFS、Packages 等全部服务。需开放的端口：**22（SSH）、80（HTTP）、443（HTTPS）**。

（因轻量应用服务器API不支持对 CIDR 来源限制，下面给出使用 API 配置 ECS 安全组的示例，**仅供参考**）

```yaml
- name: Add temporary security group rule
  run: |
    ip=${{ steps.ip.outputs.ipv4 }}
    aliyun ecs AuthorizeSecurityGroup \
      --RegionId cn-hangzhou \
      --SecurityGroupId sg-xxxxxx \
      --IpProtocol tcp \
      --PortRange 8080/8080 \
      --SourceCidrIp ${ip}/32

- name: Your deployment or test steps
  run: echo "Running tasks..."

- name: Revoke temporary rule
  run: |
  	ip=${{ steps.ip.outputs.ipv4 }}
    aliyun ecs RevokeSecurityGroup \
      --RegionId cn-hangzhou \
      --SecurityGroupId sg-xxxxxx \
      --IpProtocol tcp \
      --PortRange 8080/8080 \
      --SourceCidrIp ${ip}/32
```
