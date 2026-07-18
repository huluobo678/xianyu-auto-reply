# 生产域名、HTTPS 与支付宝套餐支付上线步骤

本文用于 `D:\xianyu-auto-reply` 的正式上线。业务代码仍以 GitHub 提交为唯一来源；服务器只拉取已验证提交并应用生产 Compose 覆盖文件。

## 1. 上线前需要准备

- 一个可控制 DNS 的域名，例如 `xianyu.example.com`。
- 域名 A 记录指向服务器公网 IP `154.202.118.102`。
- 云防火墙和系统防火墙允许 TCP `80`、TCP `443` 和 UDP `443`。
- 支付宝开放平台应用已审核，并开通当面付。
- 支付宝 `APP_ID`、应用私钥、支付宝公钥和商户 `seller_id`。

应用私钥、支付宝公钥等敏感内容禁止提交到 Git，也不要粘贴到公开聊天记录。

## 2. 生产入口结构

生产环境使用：

- `deploy/docker-compose.production.yml`：增加 Caddy，并把原服务端口收紧到 `127.0.0.1`。
- `deploy/Caddyfile`：自动申请和续期 HTTPS 证书。
- 公网只开放 `80/443`。
- CORS、后端公开 URL 和前端公开 URL 自动收敛到 `https://${APP_DOMAIN}`。
- `/api`、`/static`、`/health` 直接代理到 `backend-web:8089`。
- 其他请求代理到 `frontend:80`。
- WebSocket 位于 `/api` 路径下，由 Caddy 自动处理连接升级。

## 3. 配置检查

在服务器源码目录执行：

```bash
cd /opt/xianyu-auto-reply/source
printf '\nAPP_DOMAIN=xianyu.example.com\n' >> .env
docker compose \
  -f docker-compose.yml \
  -f deploy/docker-compose.production.yml \
  config --quiet
```

必须把示例域名替换为真实域名。若 Compose 报 `APP_DOMAIN is required`，立即停止，不要启动服务。

## 4. 启动 HTTPS 入口

确认 DNS 已解析到服务器后执行：

```bash
cd /opt/xianyu-auto-reply/source
docker compose \
  -f docker-compose.yml \
  -f deploy/docker-compose.production.yml \
  up -d caddy frontend backend-web websocket scheduler
```

验证：

```bash
docker compose \
  -f docker-compose.yml \
  -f deploy/docker-compose.production.yml \
  ps
curl -I https://xianyu.example.com/
curl -fsS https://xianyu.example.com/health
```

停止条件：

- Caddy 未达到 `healthy`。
- HTTPS 证书申请失败。
- 首页或 `/health` 不是 HTTP 200。
- 后端、WebSocket 或调度器出现 ERROR、CRITICAL、Traceback。

## 5. 支付宝套餐支付配置

在管理后台敏感系统设置中配置：

- `alipay.app_id`
- `alipay.private_key`
- `alipay.alipay_public_key`
- `alipay.seller_id`
- `alipay.billing_notify_url`

套餐支付回调地址固定为：

```text
https://xianyu.example.com/api/v1/billing/alipay/notify
```

余额充值使用的 `alipay.notify_url` 与套餐支付回调分开配置，不要混用。

## 6. 支付闭环验收

1. 调用 `GET /api/v1/billing/payment-readiness`，确认 `payment_ready=true`。
2. 使用临时测试用户创建套餐订单。
3. 生成支付宝二维码并完成小额实付。
4. 验证支付宝回调签名、商户号、金额和交易号。
5. 验证订单变为 `paid`、权益状态变为 `granted`。
6. 验证订阅、账号上限和 AI 套餐额度同时到账。
7. 重放同一回调，确认不会重复发放权益。
8. 清理临时用户、订单、订阅、额度和权益流水测试数据。

未完成真实支付回调前，不能把“二维码已生成”当作支付功能已经上线。

## 7. 回滚

代码回滚应恢复部署前 Git 提交和备份，再无缓存重建受影响服务。不要直接编辑服务器业务代码。