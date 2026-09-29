# CPU 镜像发布记录

- 标签：`liujun4hust/sage-icpp-demo:20260926-cpu-full`
- Manifest：`sha256:36e316a2c3c70960a0bdc9c340dc7c968cb67c38a1f96c4042251a7ed674c84d`
- 镜像 ID：`sha256:83f1c8310b087a3169f13cdbc6e0deba2fc458fb410c03362cf380a2c270d4be`
- 平台：Linux AMD64。
- Hub 压缩层总量：680,198,309 字节，约 649 MiB；镜像未压缩约 1.79 GiB。
- 从 user@server4 经用户提供的 SOCKS 代理，使用官方 crane v0.22.1 推送。未修改 Docker daemon 全局代理，未重启已有服务。临时 Hub
  认证目录在上传脚本退出时清理。
- 推送后匿名读取 manifest，SHA256 和 config digest 均与已测试镜像一致；公开 Docker Hub 标签 API 也返回相同 manifest digest。
- 同一镜像的独立容器已通过 CPU 算子、342 个模块导入、pip check、104 入口样例审计及代表应用验证。此次未重新从 Hub 下载所有层，不宣称 server4 的 Docker
  daemon 已完成代理配置或直接 docker pull 实测。
- 不包含 CUDA、模型权重和访问凭据。3 个应用仍需数据/模型/凭据，不能将依赖齐全解释为所有业务功能均已验证。
