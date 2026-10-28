# Northstar Locate

这是一个独立的响应式网站，包含邮箱加密码账号、Whop CheckoutElement 付款入口和管理员账号查询页面。

## 本地启动

在此目录执行：

```powershell
Copy-Item .env.example .env
# 编辑 .env，至少填写两个 Whop plan ID、WHOP_CHECKOUT_SIGNING_SECRET 和 ADMIN_PASSWORD
python server.py
```

浏览器打开 `http://127.0.0.1:4243/`。

管理员页面是 `http://127.0.0.1:4243/admin`。

## 账号和付款流程

1. 用户点击月度或年度方案。
2. 未登录用户先在本站注册或登录，当前注册不要求邮箱验证码。
3. 服务端为当前用户签发短期签名令牌，前端把令牌放进 Whop Checkout 的 metadata。
4. Whop CheckoutElement 在本站内打开付款界面。
5. Whop 将 `payment.succeeded` Webhook 发到 `/webhook`，服务端校验签名后把账号的 `subscription_status` 设为 `active`，并保存 `monthly` 或 `yearly`。
6. 管理员打开 `/admin`，可以查看邮箱、订阅状态、订阅方案、Whop payment ID 和注册时间。

付款结果页面只展示回跳结果，不能替代 Webhook；真正的开通状态以服务器收到并验证的 Whop Webhook 为准。

## Whop 配置

在 Whop 中创建月度和年度计划，把两个 `plan_...` ID 分别填入：

```text
WHOP_MONTHLY_PLAN_ID=plan_...
WHOP_YEARLY_PLAN_ID=plan_...
```

在 Whop 的 Standard Webhooks 中把地址设置为：

```text
https://你的域名/webhook
```

把 Whop 生成的完整 `ws_...` 填到 `WHOP_WEBHOOK_SECRET`，保留前缀，不要自行解码或重新编码。

`WHOP_CHECKOUT_SIGNING_SECRET` 是本网站自己使用的随机密钥，建议使用至少 32 个随机字节，不要与 Whop Webhook 密钥重复。

## Render 部署

Render Environment 至少设置：

- `HOST=0.0.0.0`
- `PORT` 留给 Render 自动注入
- `PUBLIC_BASE_URL=https://你的域名`
- `WHOP_MONTHLY_PLAN_ID`
- `WHOP_YEARLY_PLAN_ID`
- `WHOP_CHECKOUT_SIGNING_SECRET`
- `WHOP_WEBHOOK_SECRET`
- `ADMIN_PASSWORD`
- `DATABASE_PATH=/var/data/northstar.sqlite3`

如果使用 Render 持久磁盘，把磁盘挂载到 `/var/data`，否则服务重启或重新部署可能丢失账号和订阅记录。

构建命令可以使用：

```text
python -m py_compile server.py
```

启动命令：

```text
python server.py
```

## 邮箱验证码

当前代码只验证邮箱格式，不发送验证码。

如果要启用邮箱验证码，需要接入 Resend、Postmark、SendGrid 或 Amazon SES 等邮件服务，服务端增加验证码生成、哈希保存、过期时间、失败次数限制和 `email_verified_at` 字段，并在注册及登录流程中检查验证状态。
