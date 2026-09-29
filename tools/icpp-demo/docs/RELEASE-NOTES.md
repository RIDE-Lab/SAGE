# 20260926 修复版

这次修复针对 20260921 镜像的默认演示路径。最终结果以随包 validation/summary.json 和逐项日志为准；不是所有入口都支持无需配置直接运行。

- 兼容 sink 的 teardown 收尾，修复结果不落盘；工作线程失败及时返回；输出保存失败记录 failed。
- 修正默认样例契约、目录参数和 output-format 参数类型。样例均为明确标注的合成输入，不是模型响应或业务运行结果。
- Student Improvement 在后台运行两次考试的一次性真实流程；终端仍可交互。
- 修正 Auto Scaling Chat 把闲置 tick 当作批处理结束的问题。这是流量模拟，不是生产集群自动扩容。
- 补齐 requests、NumPy、scikit-learn、BeautifulSoup、lxml 等 CPU 依赖。
- 结果输出默认放 /data 数据卷；选择 Instance ID 后定位详情；DAG 默认折叠。

## 必须另行满足的条件

Video Intelligence：当前 CPU 基础镜像不含
torch/torchvision/Pillow/transformers/OpenCV、视觉权重和有效视频。源代码保留，启动前显示缺少的模块。应为该应用单独构建包含其视觉依赖的派生镜像，挂载真实视频和权重，并复测。未宣称该应用已修复到可直接运行。

Medical Diagnosis：需要挂载 --image 或 --batch 对应数据，以及 --config 对应诊断模型配置。已修复外层参数直接传入内层 parser
的错误，并禁止自动生成模拟诊断输入。没有医疗数据和视觉模型的完整验证，不能宣称临床或诊断有效。

Work Report：必须配置 GITHUB_TOKEN（或 GIT_TOKEN）和有权限访问的仓库。已删除缺少令牌时自动使用模拟 GitHub 结果的回退。凭据不进入镜像。网络查询及 LLM
摘要仍须按用户仓库验证。

Article Monitoring 默认离线演示会使用明确标注的合成文章输入，排名使用词汇重叠，不是 embedding。Literature Report 支持 --input-file
的显式合成论文记录进行排序测试；不宣称这些是公开发表论文。

## 模型

通过环境变量配置，业务代码没有硬编码内网 IP。当前测试默认地址：

```dotenv
SAGE_LLM_BASE_URL=http://11.11.11.31:8000/v1
SAGE_LLM_MODEL=Qwen2.5-3B-Instruct
SAGE_OPENAI_API_KEY=EMPTY
SAGE_RERANKER_BASE_URL=http://11.11.11.31:8001
SAGE_EMBEDDING_BASE_URL=
```

8001 是 reranker，已实际测试 /rerank；没有可用 embedding 服务的验证，不用 reranker 顶替。离开内网时保留离线演示路径。无模型权重和 CUDA 推理环境。

## 启动与验证

Linux/macOS：`sh scripts/start-local.sh`；首次会生成 .env。Windows Linux
容器环境：`powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1`（PowerShell 未在 Windows
实测）。Hub 发布完成后可使用 start-hub 脚本；实际发布状态以交付报告为准。

浏览器访问本机 http://localhost:18400/ui/；远程浏览器用户访问部署者提供的网页，不需要安装 Docker。数据保存在 Compose
声明的数据卷中。容器重启不保留进程实例，样例状态和文件仍保留，重新启动应用读取。

全量检查脚本 application-audit.py 在独立空卷容器运行；不以退出码或有 metrics 单独认定业务正确。验证平台 Linux AMD64，其他平台未实测。
