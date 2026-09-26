# SAGE ICPP Demo — CPU 应用依赖版

本包包含 SAGE、OPC、前后端、应用源码、Python 运行时、应用声明的 CPU 依赖和数据准备工具。模型权重、CUDA/NVIDIA 运行库、用户数据和访问凭据不包含。

## 启动

Linux/macOS（需要可运行 Linux AMD64 容器的 Docker）：

```sh
sh scripts/start-local.sh
```

Windows（需要 Linux 容器环境；未在 Windows 实测）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

首次自动创建 .env，不覆盖已有文件。容器自动启动前后端，浏览器打开 http://localhost:18400/ui/。远程部署的浏览器用户无需安装 Docker。

先配置再启动可用 `sh scripts/start-local.sh --init-env`。LLM 默认 http://11.11.11.31:8000/v1，模型
Qwen2.5-3B-Instruct；reranker 默认 http://11.11.11.31:8001。embedding 留空，因为尚未确认服务。内网 IP
仅为配置默认值；无法访问内网时清空相应端点，使用 Ticket Triage、Supply Chain Alert、Data Cleaner 的离线流程。修改 .env 后重新运行启动脚本应用配置。

停止：`docker compose -p sage-icpp-demo-local -f compose.local.yaml stop`。数据保存在 Compose
数据卷，停止不删除数据；重新启动后需重新 Launch 应用实例。

## 依赖范围与限制

除原有 NumPy、scikit-learn、requests、OpenAI、FastAPI 等依赖外，加入 CPU
PyTorch/torchvision、Transformers、Accelerate、Pillow、OpenCV headless、pydicom、Segment
Anything、datasets、Hugging Face Hub、Redis/MQTT 客户端、Jinja2 和 Markdown。实际版本与哈希见源码包
requirements.lock，导入和 CPU 算子测试见 validation。

- Redis/MQTT 是客户端库，不自动部署 Redis 或消息代理。
- 视频应用仍需有效视频及相应模型权重。文本 LLM 不能代替 CLIP/MobileNet 等视觉模型。
- 医学应用仍需真实输入和配置，其源码存在未完成的模型接入/回退逻辑，不能宣称已验证诊断功能。
- Work Report 是可选的 GitHub 工作周报应用，需要 GitHub 凭据和网络；旧 sage.llm 依赖已换为已打包的 OpenAI 客户端，摘要读取
  SAGE_LLM_BASE_URL/SAGE_LLM_MODEL。
- 医学知识库的 SageVDB 集成在现有源码中被禁用（vector_db=None），不属于已实现运行路径；未从未知来源安装同名库。源码保留此限制，不能宣称向量库功能可用。
- “CPU 依赖完整”指应用声明和现有有效导入路径覆盖，不表示全部外部服务、权重、数据和业务功能无需配置可用。
- 只验证 Linux AMD64；未宣称 ARM64、Windows 容器或 GPU 运行已验证。

## 核验

先校验 SHA256SUMS，再运行脚本。核心演示顺序：Launch Ticket Triage API → Run Demo Flow → 查看结果和 metrics；Supply Chain
Alert API 同样操作；Data Cleaner 使用 Test Data 默认输入启动并检查结果文件。

已发布 Docker Hub 标签 `liujun4hust/sage-icpp-demo:20260926-cpu-full`。运行 `sh scripts/start-hub.sh`
可拉取启动；Windows 对应 `scripts/start-hub.ps1`。离线归档仍可使用。

启动文件位于 tools/icpp-demo/scripts；Hub 与离线入口共用同一目录。仓库不包含大体积镜像。
