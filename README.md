# Northstar Locate

这是一个独立的响应式单页网站，参考了位置请求类产品的页面结构，当前不连接 Stripe、短信或定位服务。

## 本地启动

在此目录执行：

```powershell
python server.py
```

浏览器打开 `http://127.0.0.1:4243/`。

这个独立网站默认使用 4243 端口，原来的 Stripe 演示仍可继续使用 4242 端口。

```powershell
$env:PORT=4244
python server.py
```

然后打开 `http://127.0.0.1:4244/`。

## 当前功能

- 响应式首页、导航和移动端菜单
- 国家/地区区号选择与手机号格式校验
- 本地演示状态：已创建、等待收件人确认
- 功能介绍、三步流程、价格卡、隐私说明和 FAQ 折叠
- 所有输入仅在当前浏览器页面显示，不会发送到外部服务

## 后续接入

付款、短信发送、收件人同意页和真实位置服务应在确认业务流程与合规要求后，再分别接入后端接口。

## 部署到公网（推荐 Render）

当前项目是一个 Python Web 服务，推荐部署到 Render Web Service，这样以后接 Stripe 后仍可复用同一个后端地址。

1. 把 `wayfinder-site` 文件夹上传到你自己的 GitHub 仓库，或把包含它的仓库连接到 Render。
2. 在 Render 选择 **New + > Web Service**，如果仓库根目录是整个项目，设置 Root Directory 为 `stripe-checkout-elements-demo/wayfinder-site`。
3. 设置 Build Command 为 `python -m py_compile server.py`，设置 Start Command 为 `python server.py`。
4. 添加环境变量 `HOST=0.0.0.0`，Render 会自动提供 `PORT`，不要手动固定公网端口。
5. 创建服务后，Render 会生成一个 `https://你的服务名.onrender.com` 地址，这个地址可以直接发给别人访问。

免费服务可能在一段时间没有访问后休眠，第一次打开会慢一些；需要稳定运行时再升级到付费实例。

## 绑定自己的域名

域名不是现在必须购买的，先用 Render 免费地址即可。

以后购买域名后，在 Render 的 **Settings > Custom Domains** 添加域名，再按页面提示在域名服务商处添加 DNS 记录，Render 会自动配置 HTTPS。

## 上线前必须补齐的功能

当前页面只是 UI 演示，真实短信、收件人同意、位置查询、数据库和 Stripe 付款仍未接入。

正式接 Stripe 时，需要在 Render 的 Environment 中配置 live 密钥，设置 `PUBLIC_BASE_URL` 为实际 HTTPS 地址，并在 Stripe 创建指向公网 `/webhook` 的 Webhook。
