# 闲鱼本地连接器 0.3.0

闲鱼连接器在 Windows 本机运行，负责扫码登录、风险验证、闲鱼 Token 获取、WebSocket 连接和最终消息发送。

## 一键安装

1. 从正式 SaaS 的“本地连接器”页面下载安装包。
2. 正常安装；安装完成后连接器会自动启动。
3. 连接器自动打开系统默认浏览器中的官方授权页。
4. 登录或注册 SaaS 后，点击一次“绑定此电脑”。
5. 返回连接器，扫码登录闲鱼。

普通用户不需要填写服务器地址，不需要复制绑定码、设备令牌或 SaaS 密码。

## 安全边界

- 官方 SaaS 地址固定为 `https://xy.yunshuzhilian.asia`。
- 配对会话短期有效、一次性消费，服务端仅保存 pairing token 哈希。
- 设备凭据、闲鱼 Cookie 和 Token 使用 Windows DPAPI 保存在本机。
- 人脸、滑块和风控验证仅在本机执行。
- 闲鱼 Token 获取、WebSocket 连接和最终消息发送均从本机网络执行。
- Cookie、闲鱼 Token 和验证链接不会上传云端。
- 电脑或连接器离线时自动回复暂停，不切换到云端连接。

## 本地开发

```powershell
python -m pip install -r connector/requirements.txt
python -m connector.main --self-test
python -m connector.main
```

也可以运行：

```powershell
run_connector_dev.bat
```

## 更新与卸载

- 连接器只从 SaaS 发布记录读取更新，下载后校验 SHA-256。
- 覆盖升级默认保留 `%LOCALAPPDATA%\XianyuConnector` 中的 DPAPI 凭据和发送去重数据库。
- 卸载时会明确询问是否保留本机数据。
- 当前安装包未进行代码签名，发布报告必须标记 `signed=false`。
