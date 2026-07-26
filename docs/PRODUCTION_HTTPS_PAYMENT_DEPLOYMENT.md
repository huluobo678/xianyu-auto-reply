# 生产域名、HTTPS 与兑换码收费上线步骤

本文用于 `D:\xianyu-auto-reply` 的正式上线。业务代码仍以 GitHub 提交为唯一来源；服务器只拉取已验证提交并应用生产 Compose 覆盖文件。

第一版采用兑换码收费，不接入支付宝在线支付。支付宝相关代码保留但默认关闭，不配置真实支付宝密钥，不进行支付宝真实支付测试。

## 1. 上线前需要准备

- 一个可控制 DNS 的域名，例如 `xianyu.example.com`。
- 域名 A 记录指向服务器公网 IP `154.202.118.102`。
- 云防火墙和系统防火墙允许 TCP `80`、TCP `443` 和 UDP `443`。
- 已在链动小铺配置好对应商品，并开通自动发码。
- 管理员已规划兑换码批次（套餐、加量包、月付/季付、数量与有效期）。

兑换码 HMAC 密钥、支付宝密钥等敏感内容禁止提交到 Git，也不要粘贴到公开聊天记录、文档或测试输出。

## 2. 生产入口结构

生产环境使用：

- `deploy/docker-compose.production.yml`：增加 Caddy，并把原服务端口收紧到 `127.0.0.1`。
- `deploy/Caddyfile`：自动申请和续期 HTTPS 证书。
- 公网只开放 `80/443`。
- CORS、后端公开 URL 和前端公开 URL 自动收敛到 `https://${APP_DOMAIN}`。
- `/api`、`/static`、`/health` 直接代理到 `backend-web:8089`。
- 其他请求代理到 `frontend:80`。
- WebSocket 位于 `/api` 路径下，由 Caddy 自动处理连接升级。
- `Content-Security-Policy` 仅允许 `frame-src 'self' https://pay.ldxp.cn`。

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

## 5. 上线流程

第一版上线流程：

1. 配置真实域名。
2. 配置 DNS。
3. 部署 HTTPS。
4. 配置 `redemption_store_url`。
5. 管理员生成兑换码批次。
6. 在链动小铺配置对应商品。
7. 用户通过链动小铺购买兑换码。
8. 用户回平台兑换。
9. 验收兑换、套餐、AI 额度和权益流水。
10. 清理测试兑换码、测试用户、测试订阅、测试额度和测试流水。
11. 正式上线。

## 6. 链动小铺与商城 iframe

链动小铺地址：

```text
https://pay.ldxp.cn/shop/5LWTNV9V
```

平台与链动小铺的边界：

1. 平台不接链动小铺 API。
2. 平台不依赖链动小铺订单回调。
3. 平台不向链动小铺发送用户隐私信息。
4. 商城页面使用 iframe 内嵌链动小铺，并保留新窗口打开降级入口。

当 iframe 出现白屏、支付跳转失败、手机端受限或第三方 Cookie 被阻止时，前端提示用户使用新窗口打开商城。

## 7. redemption_store_url 配置

配置键：

```text
redemption_store_url
```

写入校验由 `backend-web/app/api/routes/system_settings.py` 与 `backend-web/app/services/system_setting_service.py` 强制执行：

1. 协议必须为 `https`。
2. host 必须严格等于 `pay.ldxp.cn`。
3. 不允许显式端口、userinfo 或其它域名，避免端口或域名绕过。
4. 默认值为 `https://pay.ldxp.cn/shop/5LWTNV9V`。

仅管理员可修改该配置。

## 8. Caddy CSP 配置

`deploy/Caddyfile` 的 CSP 只允许：

```text
Content-Security-Policy "frame-src 'self' https://pay.ldxp.cn"
```

- 不放开 `script-src`、`connect-src` 等其它策略。
- 不引入支付宝域名或其它第三方域名。
- iframe 域名白名单只有本站与 `https://pay.ldxp.cn`，不得任意扩展。

## 9. 支付宝默认关闭

支付宝总开关：

```text
alipay.enabled = false
```

第一版支付宝策略：

1. 支付宝总开关 `alipay.enabled` 默认为 `false`。
2. 第一版不配置真实支付宝密钥。
3. 第一版不进行支付宝真实支付测试。
4. 现有支付宝代码保留，但默认关闭；`alipay_guard` 在入口处拦截，不调用支付宝 SDK、不创建真实支付订单、不发起真实回调。
5. 受守卫入口在 `alipay.enabled=false` 时返回 503 / 未开通，不向用户暴露在线支付入口。
6. 域名和 HTTPS 仍然是正式上线必需条件。

支付宝真实参数、真实回调地址和真实小额支付验收属于后续阶段，需用户单独批准后再进行。

## 10. 兑换码上线验收

1. 管理员按套餐 / 加量包 / 月付 / 季付生成兑换码批次，并导出供链动小铺上架。
2. 在链动小铺配置对应商品并开通自动发码。
3. 使用真实兑换码（非测试码）在平台兑换。
4. 验收套餐立即生效、账号上限更新、AI 月度额度或无限权益到账。
5. 验收加量包额度到账与有效期。
6. 验收权益流水和额度流水形成正确记录。
7. 验收同月续费只延长有效期、不重复发放当月额度。
8. 验收兑换码一次性核销，重放不重复发放权益。
9. 清理测试兑换码、测试用户、测试订阅、测试额度和测试流水。
10. 清理完成后再次运行相关验证，确认无残留测试数据。

不要把测试兑换码、HMAC 密钥或兑换码导出文件提交到仓库。

## 11. 回滚

代码回滚应恢复部署前 Git 提交和备份，再无缓存重建受影响服务。不要直接编辑服务器业务代码。
