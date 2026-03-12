# 完整部署指南：从零开始到收款

## 你需要准备的东西

| 项目 | 说明 | 费用 |
|------|------|------|
| VPS 一台 | 推荐 RackNerd / BandwagonHost | $5-50/月 |
| 域名一个 | Namesilo 买 .top 域名 | ~$1/年 |
| Cloudflare 账户 | 免费 | 0 |
| TokenPocket 钱包 | 手机安装 | 0 |
| 币安账户 | 提现用 | 0 |
| 少量 TRX | 链上手续费 | ~¥15 |

---

## 第一步：购买 VPS

去 RackNerd (便宜) 或 BandwagonHost (稳定) 购买一台 VPS。

要求:
- 系统选 Ubuntu 22.04 或 24.04
- 内存 >= 512MB
- 建议美国/日本/新加坡机房

拿到后你会得到:
- VPS IP 地址 (比如 `1.2.3.4`)
- root 密码
- SSH 端口 (通常 22)

用终端连接:
```bash
ssh root@1.2.3.4
```

---

## 第二步：购买域名 + 配置 Cloudflare

### 2.1 买域名

1. 打开 https://www.namesilo.com
2. 搜一个便宜的 .top 域名 (比如 `myvpn123.top`，约 $0.99/年)
3. 注册账号 → 购买 → 开启 WHOIS 隐私保护

### 2.2 配置 Cloudflare

1. 打开 https://dash.cloudflare.com → 注册免费账户
2. 点 "添加站点" → 输入你的域名 `myvpn123.top` → 选免费计划
3. Cloudflare 会给你两个 NS 服务器地址，类似:
   ```
   anna.ns.cloudflare.com
   bob.ns.cloudflare.com
   ```
4. 回到 Namesilo → 域名管理 → 修改 NS 为 Cloudflare 给的这两个
5. 等几分钟到几小时生效

### 2.3 添加 DNS 记录

在 Cloudflare 控制面板 → DNS → 添加记录:

```
记录1 (VPN 用，走 CF CDN):
  类型: A
  名称: vpn
  内容: 1.2.3.4    (你的 VPS IP)
  代理: 开启 (橙色云朵)

记录2 (发卡网站用，也走 CF CDN):
  类型: A
  名称: shop
  内容: 1.2.3.4
  代理: 开启 (橙色云朵)

记录3 (订阅服务器 + 管理面板，走 CF 获得 HTTPS):
  类型: A
  名称: admin
  内容: 1.2.3.4
  代理: 开启 (橙色云朵)
```

### 2.4 设置 SSL/TLS 加密模式

这一步很重要，选错了 CF CDN 备用线路会连不上。

```
1. 登录 Cloudflare → 选择你的域名 myvpn123.top
2. 左侧菜单 → 点击 "SSL/TLS"
3. 点击 "Overview" (概述)
4. 找到加密模式 (Encryption mode)，点击 "Configure" (配置)
5. 选择 "Flexible"
6. 保存

如果界面不同 (Cloudflare 经常改版)，也可以试:
  SSL/TLS → Configuration (配置) → 加密模式下拉框 → Flexible
```

**为什么选 Flexible？**
- Flexible = 访客→Cloudflare 加密，Cloudflare→你的服务器 不加密
- 因为 VMess-WS 监听的是 HTTP 端口 (8880)，没有 TLS
- 如果选 Full/Strict，Cloudflare 会尝试用 HTTPS 连你服务器，但服务器没有 HTTPS，会报 502 错误

**注意**：如果你看到 "Off / Flexible / Full / Full (strict)" 四个选项，选 Flexible 就对了。

完成后你有三个子域名:
- `vpn.myvpn123.top` → CF CDN 备用线路 (被墙时用)
- `shop.myvpn123.top` → 发卡网站 (买家访问)
- `admin.myvpn123.top` → 订阅服务器 + 管理面板 (HTTPS，端口 2096)

---

## 第三步：安装 VPN Manager

### 3.1 上传文件

在你的本地电脑上:
```bash
scp -r vpn-manager/ root@1.2.3.4:/opt/vpn-manager/
```

### 3.2 一键安装

SSH 连到 VPS:
```bash
ssh root@1.2.3.4
cd /opt/vpn-manager
python3 main.py --install
```

安装向导会分 4 步自动引导你完成所有配置:

**第 1 步：安装 sing-box**
```
选择安装的协议:
  1. 仅 VLESS-Reality (最隐蔽)
  2. VLESS-Reality + VMess-WS (推荐，支持 CF CDN 备用)  ← 输入 2 回车
  3. 全部协议
  4. 自定义

VLESS 端口 [443]:                    ← 直接回车用默认
Reality SNI [www.microsoft.com]:     ← 直接回车用默认
```

等待自动完成 (约1-2分钟):
- 安装系统依赖 ✓
- 启用 BBR 加速 ✓
- 下载 sing-box (SHA256校验) ✓
- 生成密钥和证书 ✓
- 配置防火墙 (自动开放端口) ✓
- 启动服务 ✓

安装完会显示分享链接和二维码。先用手机扫码测试能不能连上 VPN。

**第 2 步：配置 Cloudflare CDN 备用** (需要先完成第二步的 CF 设置)
```
输入你的 CF 域名 (如 vpn.myvpn123.top，回车跳过): vpn.myvpn123.top
```
如果还没配好 CF，直接回车跳过，之后可以在菜单 [16. sing-box 管理] → [5. 配置 CF 备用] 中设置。

**第 3 步：启动订阅服务器 + Web 管理面板**
```
订阅服务器端口 [8888]:    ← 直接回车用默认
```
自动启动订阅服务器，包含用户订阅分发、管理面板、发卡 API。

**第 4 步：设置管理员密码**
```
设置管理员密码 (至少6位): xxxxxx
确认密码: xxxxxx
```

**安装完成后自动显示汇总信息并进入管理菜单。**

### 3.3 后续配置 (在管理菜单中操作)

安装完成后自动进入管理菜单，你可以进行以下操作:

**配置服务器容量** (菜单 14):
```
vpn-manager
# 输入 14 (服务器容量)
# 输入你的 VPS 带宽: 例如 2500 (Mbps)
# 输入月流量: 例如 1 (TB)
```

**设置 API 密钥** (菜单 13，发卡平台对接用):
```
vpn-manager
# 输入 13 (发卡平台)
# 输入 2  (设置 Webhook API 密钥)
# 直接回车自动生成
# 记下这个密钥，后面要用！比如: a1b2c3d4e5f6...
```

**开放发卡网站和收款端口** (如果之后要部署独角数卡和 epusdt):
```bash
ufw allow 80/tcp      # 发卡网站
ufw allow 8000/tcp    # epusdt 收款
```
注意: VPN 相关端口 (443/8880/8888) 安装时已自动开放，无需手动操作。

### 3.4 验证

- 手机客户端用二维码/分享链接连接 VPN → 应该能翻墙
- 浏览器打开 `https://admin.myvpn123.top:2096/admin` → HTTPS 访问管理面板
  (或直连: `http://1.2.3.4:2096/admin`)
- 输入管理员密码 → 进入管理面板
- 之后可以用 `vpn-manager` 命令随时进入管理菜单

---

## 第四步：创建 TRON 钱包 (收 USDT 用)

**在安装向导第 5 步之前完成此步骤。**

### 4.1 安装 TokenPocket

手机下载 TokenPocket (官网: https://www.tokenpocket.pro)，无需手机号注册。

### 4.2 创建钱包

```
打开 App
→ "我没有钱包" → 创建钱包
→ 选择 TRON 链 (不是TRX币，是TRON链)
→ 设置钱包名称和密码
→ 备份助记词 (12个英文单词，写在纸上！不要截图！)
→ 验证助记词
→ 创建完成
```

### 4.3 添加 USDT

```
钱包主页 → 右上角 "+" → 搜索 "USDT" → 选择 USDT (TRC-20) → 添加
```

### 4.4 记录两个关键信息 (安装向导第5步需要)

```
收款地址:
  主页 → 点 TRX 或 USDT → 收款 → 复制地址
  得到类似: TJfKxxxxxxxxxxxxxxxxxxxxxxxxxx

私钥:
  我的 → 管理钱包 → 选你的钱包 → 导出私钥 → 输入密码 → 复制
  得到一串很长的十六进制字符串
```

把这两个值记到安全的地方，安装脚本第 5 步会用到。

### 4.5 充值少量 TRX (链上手续费)

```
打开币安 App
→ 资金 → 提现 → 搜索 TRX
→ 地址填你 TokenPocket 的地址 (T 开头那个)
→ 网络选 TRC-20
→ 数量填 50 (约 ¥15，够用很久)
→ 确认提现
```

等几分钟，TokenPocket 里会显示 50 TRX。

---

## 第五步：部署 epusdt + 独角数卡 (自动收款 + 发卡网站)

**安装脚本已集成此步骤。** 如果之前安装时跳过了，可以在管理菜单中部署:

```bash
vpn-manager
# 输入 13 (发卡平台)
# 输入 4  (部署 epusdt)
#   → 输入 TRON 收款地址和私钥
#   → 自动安装 Docker、部署 epusdt、开放端口
#
# 输入 5  (部署独角数卡)
#   → 自动部署，然后按提示在浏览器完成初始化
```

### 5.1 epusdt 部署后

epusdt 会自动:
- 安装 Docker (如果没有)
- 拉取 epusdt 镜像并启动
- 开放 8000 端口
- 生成 API 密钥

### 5.2 独角数卡初始化 (需要在浏览器操作)

部署完成后，打开浏览器完成初始化:

1. 打开 `http://你的IP:80`
2. 按安装向导设置:
   - 网站名称: 比如 "VPN Store"
   - 管理员账号密码: 自己设
   - 数据库选 SQLite (最简单)
3. 安装完成后登录后台: `http://你的IP:80/admin`

### 5.3 配置支付 (对接 epusdt)

安装脚本会显示具体的密钥值，按提示在独角数卡后台填入:

```
后台 → 系统设置 → 支付设置 → 添加支付方式:
  名称: USDT
  支付通道: 自定义 / epusdt
  商户ID: 随便填
  商户密钥: (安装脚本显示的 epusdt API 密钥)
  支付网关: http://127.0.0.1:8000
  启用: 是
```

### 5.4 创建商品 (对接 vpn-manager API)

安装脚本同样会显示 API 密钥和地址，按提示创建三个商品:

```
后台 → 商品管理 → 添加商品:

商品1: VPN 单日套餐
├── 价格: 2.00 (元)
├── 发货方式: 第三方API发货
├── API 地址: http://127.0.0.1:2096/api/create
├── 请求参数: {"secret": "安装脚本显示的密钥", "plan_id": 1}
├── 返回提取字段: sub_url
└── 显示给买家: "您的VPN订阅链接 (复制到V2rayN等客户端使用)"

商品2: VPN 月卡
├── 价格: 15.00
├── 请求参数: {"secret": "同上", "plan_id": 2}
└── 其他同上

商品3: VPN 高级月卡
├── 价格: 25.00
├── 请求参数: {"secret": "同上", "plan_id": 3}
└── 其他同上
```

也可以随时在菜单 13 → 3 查看完整的 API 配置信息。

---

## 第七步：测试完整流程

### 7.1 模拟买家购买

1. 打开 `http://shop.myvpn123.top` (你的发卡网站)
2. 选择 "VPN 单日套餐 ¥2.00"
3. 输入邮箱 (随便填) → 点购买
4. 选择 USDT 支付
5. 页面显示一个 TRON 地址和金额 (约 0.28 USDT)
6. 用另一个钱包 (或币安) 转这个金额的 USDT 到显示的地址
7. 等待 10-30 秒确认
8. 页面自动跳转显示: "您的订阅链接: http://admin.myvpn123.top:2096/sub/xxxxxx"

### 7.2 验证订阅可用

1. 复制订阅链接
2. 手机 V2rayN/Clash 里添加订阅
3. 更新订阅 → 应该出现两个节点:
   - `VPN-vl-reality` (直连)
   - `VPN-CF备用` (CF CDN)
4. 连接测试

### 7.3 检查管理面板

打开 `http://admin.myvpn123.top:2096/admin`:
- 概览页应该显示 1 个活跃用户
- 营收显示 ¥2.00
- 操作日志有记录

### 7.4 检查钱包

打开 TokenPocket → USDT 余额应该增加了 0.28 USDT

---

## 第八步：日常运维

### 查看状态
```bash
vpn-manager --status
```
或者打开管理面板 `http://admin.myvpn123.top:2096/admin`

### 提现到币安
```
TokenPocket → USDT → 转账
→ 地址填币安的 USDT (TRC-20) 充值地址
→ 输入金额
→ 确认 (会扣少量 TRX 手续费)

币安收到后:
→ 交易 → 卖出 USDT → 提现到银行卡
```

### 升级 sing-box
```bash
vpn-manager --upgrade
```

### 备份
```bash
cp /etc/vpn-manager/vpn-manager.db /root/backup-$(date +%Y%m%d).db
```

---

## 完整架构总览

```
买家手机
  │
  ├── 浏览器访问 shop.myvpn123.top ──→ 独角数卡 (:80)
  │     │                                  │
  │     │  选择套餐，点购买                  │
  │     ▼                                  ▼
  │   USDT付款 ────────────────→ epusdt (:8000) 检测到账
  │                                        │
  │                              通知独角数卡"已付款"
  │                                        │
  │                              独角数卡调用 API ──→ vpn-manager (:2096)
  │                                                      │
  │                              返回订阅链接 ←──────────┘
  │     ▲                            │
  │     │  页面显示订阅链接            │
  │     └────────────────────────────┘
  │
  ├── V2rayN/Clash 导入订阅链接
  │     │
  │     ├── 节点1: VLESS-Reality ──直连──→ sing-box (:443) ──→ 互联网
  │     └── 节点2: CF备用 ──→ Cloudflare CDN ──→ sing-box (:8880) ──→ 互联网
  │
  └── 正常上网 ✓

你 (管理员)
  │
  ├── 管理面板: http://admin.myvpn123.top:2096/admin
  │     └── 查看用户、营收、在线状态、系统健康
  │
  ├── SSH 命令行: vpn-manager
  │     └── 全部管理功能
  │
  └── 提现: TokenPocket USDT → 币安 → 银行卡
```

---

## 所有服务端口总结

| 端口 | 服务 | 说明 |
|------|------|------|
| 443 | sing-box VLESS-Reality | 用户VPN连接 (直连) |
| 8880 | sing-box VMess-WS | 用户VPN连接 (CF CDN备用) |
| 2096 | vpn-manager 订阅服务器 | 订阅分发 + 管理面板 + API (CF HTTPS) |
| 80 | 独角数卡 | 发卡网站 (买家访问) |
| 8000 | epusdt | USDT 收款网关 |
| 22 | SSH | 你的管理连接 |
