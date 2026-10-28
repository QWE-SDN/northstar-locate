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
2. 未登录用户先在本站注册或登录；启用邮箱验证后，新用户必须输入邮件里的六位验证码才能创建账号。
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

支持通过 Resend 发送六位注册验证码，不需要额外安装 Python 依赖。

1. 在 Resend 的 Domains 添加发信域名，把该页面显示的 DNS 记录原样添加到域名注册商，等待域名显示 Verified。
2. 在 Resend 的 API Keys 创建有该域名 Sending access 权限的密钥，仅填写到 Render 的 Environment。
3. 在 Render 新增并保存以下变量，重新部署：

```text
EMAIL_VERIFICATION_REQUIRED=true
RESEND_API_KEY=填入你的 Resend 发信密钥
EMAIL_FROM=Northstar Locate <verify@northstarlocate.com>
```

`EMAIL_FROM` 必须使用 Resend 已验证域名下的地址；保留现有 `WHOP_CHECKOUT_SIGNING_SECRET`，网站复用它来保护验证码摘要，不需要新增随机密钥。

新用户填写邮箱和 8 至 128 位密码后，先收到验证码，再在原弹窗输入验证码；通过后自动登录，并继续之前选择的付款方案。

验证码 10 分钟有效，60 秒后才能重发，每个验证码最多尝试 5 次。每邮箱每小时最多发送 5 次，每来源每小时 20 次，全站每小时 100 次。重发成功后旧验证码失效，重发失败时已收到且未过期的旧验证码仍可使用；注册验证窗口最长保留 1 小时，之后需重新填写邮箱和密码。

数据库仅保存密码哈希、验证码 HMAC 和验证令牌摘要；验证码不返回给浏览器，也不写入日志。发信失败或配置缺失时拒绝创建账号，不跳过验证。

数据库启动时会自动补充验证表和 `users.email_verified_at` 列，已有账号和订阅继续保留，已有账号仍可直接登录；只有验证通过的新账号标记为邮箱已验证。

未设置或关闭 `EMAIL_VERIFICATION_REQUIRED` 时保留原有直接注册模式。开启后请用自己的新邮箱完整测试收信、输入验证码和登录，不要用虚构邮箱测试真实发信。

发送后原弹窗会显示六位验证码输入框，重发按钮与下方倒计时逐秒更新；发送失败会保留待验证状态，倒计时结束后可重试，不会跳过验证创建账号。

若 Resend 日志显示 `Invalid from field`，将 Render 的 `EMAIL_FROM` 设为 `verify@northstarlocate.com`，或使用完整格式 `Northstar Locate <verify@northstarlocate.com>`；不要写成姓名加空格再加邮箱。发信失败日志只记录 HTTP 状态和固定错误分类（例如 `EMAIL_FROM_INVALID`），不记录密钥、收件人、验证码或原始响应。
