# 离线启动

将 `sage-icpp-demo-20260926-cpu-full.tar` 放入 artifacts/，先核验随包 SHA256SUMS。执行
`sh scripts/start-local.sh` 或 Windows `scripts/start-local.ps1`。脚本自动创建 .env（已有文件不覆盖）、加载镜像并启动服务。

浏览器打开 http://localhost:18400/ui/。配置模型和验证范围见 [README](../README.md)。
