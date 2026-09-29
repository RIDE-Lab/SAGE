# SAGE ICPP Demo — CPU 完整依赖版

公开镜像：`liujun4hust/sage-icpp-demo:20260926-cpu-full`，Linux AMD64。Hub 压缩下载约 649 MiB，未压缩约 1.79 GiB。镜像包含
SAGE、OPC、前后端和 CPU 应用依赖，不包含 CUDA、模型权重、用户数据或凭据。

## 拉取并启动

```sh
git clone https://github.com/intellistream/SAGE.git
cd SAGE/tools/icpp-demo
sh scripts/start-hub.sh
```

Windows Linux 容器环境对应运行
`powershell -ExecutionPolicy Bypass -File scripts/start-hub.ps1`。脚本拉取镜像、创建数据卷、启动前后端并等待健康检查，浏览器打开
http://localhost:18400/ui/。本地运行不需要 SSH 或机房网络；首次拉取需要能访问 Docker Hub。远程网页用户无需安装 Docker。

## 模型配置

首次自动从 .env.example 创建 .env，不覆盖已有配置。可先运行 `sh scripts/start-hub.sh --init-env`（PowerShell 参数
`-InitEnvOnly`），编辑后正常启动。

内网默认 LLM 为 http://11.11.11.31:8000/v1、Qwen2.5-3B-Instruct；reranker 为
http://11.11.11.31:8001；embedding 留空，因为未确认有效服务。无法访问内网时清空端点，使用 Ticket Triage、Supply Chain Alert、Data
Cleaner 的真实离线演示。更改 .env 后重新运行脚本以重建容器。reranker 不能替代 embedding。

若没有现成的 embedding 后端，可在宿主机安装 `sentence-transformers`、FastAPI 和 Uvicorn，使用
`scripts/embedding_server.py` 启动 CPU-only OpenAI 兼容服务。默认模型为
`BAAI/bge-small-en-v1.5`，可通过 `SAGE_LOCAL_EMBEDDING_MODEL` 覆盖。服务应只绑定回环地址或 Docker
网桥，不应直接暴露公网；再将 `SAGE_EMBEDDING_BASE_URL` 指向容器可访问的 `/v1` 地址。访问宿主机服务时，
Compose 默认使用 `host.docker.internal:host-gateway`；不支持该别名的 Docker daemon 可在 `.env` 中将
`SAGE_HOST_GATEWAY` 设为实际网桥地址。

本机 GLM 和 CPU embedding 的配置示例：

```dotenv
SAGE_LLM_BASE_URL=http://host.docker.internal:18001/v1
SAGE_LLM_MODEL=zai-org/GLM-4-32B-0414
SAGE_EMBEDDING_BASE_URL=http://host.docker.internal:18002/v1
SAGE_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
SAGE_EMBEDDING_PROVIDER=openai
```

在 Linux 上可将 embedding 服务只绑定到 Docker 网桥地址，例如
`python -m uvicorn scripts.embedding_server:app --host 172.17.0.1 --port 18002`。

停止并保留数据：`docker compose -p sage-icpp-demo-hub -f compose.hub.yaml stop`。重启容器后需在网页重新 Launch 应用实例。

## 离线镜像

将独立提供的 `sage-icpp-demo-20260926-cpu-full.tar` 放到 `artifacts/`，执行 `sh scripts/start-local.sh` 或
PowerShell `scripts/start-local.ps1`。支持相同的 .env 初始化和模型配置。切换 Hub/离线 Compose 项目前先停止原项目，避免同一端口冲突。Git
仓库不包含大镜像文件。

## 验证范围

84 个锁定 Python 包、342 个应用模块导入全部通过；CPU 张量计算、torchvision 原生算子、图像编解码、DICOM 像素读取及 pip check 通过。在独立断网容器审计
104 个入口：92 个文件输出样例、2 个 API 流程通过，7 个有 CLI 执行证据，3 个仍需外部条件。

Ticket 10 条输入/5 条高优先级、Supply 10 条输入/8 条告警、Cleaner 3 条输入和重复标记通过。视频、医疗、Work Report 仍需数据/权重/凭据，详见
[CPU 版限制](docs/CPU-FULL.md)。依赖完整不表示所有业务功能已验证。

发布后匿名读取的 manifest 与已测试镜像 ID 一致，公开 Hub 标签 API 也核验通过。本次未重新完整下载 Hub 所有镜像层；server4 的 Docker daemon
未配置全局代理，不能宣称该宿主直接 docker pull 已测试。Windows、macOS、ARM64 运行未实测。

准确摘要与发布方式见 [发布记录](docs/CPU-FULL-PUBLICATION.md)。网页端口默认仅绑定 127.0.0.1。

## Authenticated cloud models

See [Cloudflare model configuration and full-flow checks](docs/CLOUDFLARE-MODELS.md). Secrets belong
in the private `.env`, never in the repository.
