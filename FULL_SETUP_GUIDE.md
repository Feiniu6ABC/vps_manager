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

记录3 (订阅服务器 + 管理面板，不走 CF):
  类型: A
  名称: sub
  内容: 1.2.3.4
  代理: 关闭 (灰色云朵)
```

### 2.4 设置 SSL

Cloudflare 控制面板 → SSL/TLS → 选择 "Flexible"

完成后你有三个子域名:
- `vpn.myvpn123.top` → CF CDN 备用线路
- `shop.myvpn123.top` → 发卡网站
- `sub.myvpn123.top` → 订阅服务器和管理面板

---

## 第三步：安装 VPN Manager

### 3.1 上传文件

在你的本地电脑上:
```bash
scp -r vpn-manager/ root@1.2.3.4:/opt/vpn-manager/
```

### 3.2 安装 sing-box

SSH 连到 VPS:
```bash
ssh root@1.2.3.4
cd /opt/vpn-manager
python3 main.py --install
```

安装向导交互:
```
选择安装的协议:
  1. 仅 VLESS-Reality (推荐，最安全)      ← 输入 1 回车
VLESS 端口 [443]:                         ← 直接回车用默认
Reality SNI [www.microsoft.com]:          ← 直接回车用默认
```

等待自动完成 (约1-2分钟):
- 安装依赖 ✓
- 启用 BBR ✓
- 下载 sing-box (SHA256校验) ✓
- 生成密钥 ✓
- 启动服务 ✓

安装完会显示分享链接和二维码。先用手机扫码测试能不能连上 VPN。

### 3.3 配置 Cloudflare CDN 备用

```bash
vpn-manager
# 输入 16 (sing-box 管理)
# 输入 5  (配置 Cloudflare CDN 备用)
# 输入域名: vpn.myvpn123.top
```

### 3.4 启动订阅服务器 + 管理面板

```bash
vpn-manager
# 输入 12 (订阅服务器)
# 输入 1  (启动)
```

### 3.5 设置管理员密码

```bash
vpn-manager --set-admin-password
# 输入密码 (至少6位)
# 确认密码
```

### 3.6 配置服务器容量

```bash
vpn-manager
# 输入 14 (服务器容量)
# 输入你的 VPS 带宽: 例如 2500 (Mbps)
# 输入月流量: 例如 1 (TB)
```

### 3.7 设置 API 密钥 (发卡平台用)

```bash
vpn-manager
# 输入 13 (发卡平台)
# 输入 2  (设置 Webhook API 密钥)
# 直接回车自动生成
# 记下这个密钥，后面要用！比如: a1b2c3d4e5f6...
```

### 3.8 开放防火墙端口

```bash
ufw allow 443/tcp     # VLESS-Reality
ufw allow 8880/tcp    # VMess-WS (CF备用)
ufw allow 8888/tcp    # 订阅服务器 + 管理面板
ufw allow 80/tcp      # 发卡网站
ufw allow 8000/tcp    # epusdt 收款 (后面装)
ufw enable
```

### 3.9 验证

- 浏览器打开 `http://sub.myvpn123.top:8888/admin` → 应该看到登录页面
- 输入你设置的管理员密码 → 进入管理面板
- 手机客户端用分享链接连接 VPN → 应该能翻墙

---

## 第四步：创建 TRON 钱包 (收 USDT 用)

### 4.1 安装 TokenPocket

手机下载 TokenPocket (官网: https://www.tokenpocket.pro)

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

### 4.4 记录两个关键信息

```
收款地址:
  主页 → 点 TRX 或 USDT → 收款 → 复制地址
  得到类似: TJfKxxxxxxxxxxxxxxxxxxxxxxxxxx

私钥:
  我的 → 管理钱包 → 选你的钱包 → 导出私钥 → 输入密码 → 复制
  得到一串很长的十六进制字符串
```

把这两个值记到安全的地方，下一步要用。

### 4.5 充值少量 TRX (手续费)

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

## 第五步：部署 epusdt (USDT 自动收款)

### 5.1 安装 Docker (如果没有)

SSH 连到 VPS:
```bash
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker
```

### 5.2 创建 epusdt 配置

```bash
mkdir -p /opt/epusdt && cd /opt/epusdt

cat > .env << 'EOF'
app_name=epusdt
app_uri=http://sub.myvpn123.top:8000
app_debug=false

# 数据库 (用 SQLite，最简单)
db_driver=sqlite

# 你的 TRON 钱包信息 (从 TokenPocket 获取的)
tron_address=TJfKxxxxxxxxxxxxxxxxxxxxxxxxxx
tron_private_key=你的私钥粘贴在这里

# API 密钥 (随便设一个，独角数卡连接用)
api_auth_token=epusdt_secret_123

# 汇率
usdt_rate=7.2

# 订单过期时间 (秒)
order_expiration_time=600
EOF
```

### 5.3 启动 epusdt

```bash
docker run -d \
  --name epusdt \
  --restart always \
  -p 8000:8000 \
  -v /opt/epusdt/.env:/app/.env \
  -v /opt/epusdt/data:/app/data \
  dontcry/epusdt:latest
```

### 5.4 验证

浏览器打开 `http://1.2.3.4:8000` → 应该看到 epusdt 页面

---

## 第六步：部署独角数卡 (发卡网站)

### 6.1 安装

```bash
mkdir -p /opt/dujiaoka && cd /opt/dujiaoka

# docker-compose.yml
cat > docker-compose.yml << 'EOF'
version: '3'
services:
  web:
    image: stilleshan/dujiaoka:latest
    ports:
      - "80:80"
    volumes:
      - ./data:/dujiaoka
    environment:
      - INSTALL=true
    restart: always
EOF

docker compose up -d
```

### 6.2 初始化

1. 浏览器打开 `http://shop.myvpn123.top`
2. 按安装向导设置:
   - 网站名称: 比如 "VPN Store"
   - 管理员账号密码: 自己设
   - 数据库选 SQLite (最简单)
3. 安装完成后登录后台: `http://shop.myvpn123.top/admin`

### 6.3 配置支付 (对接 epusdt)

```
后台 → 系统设置 → 支付设置 → 添加支付方式:
  名称: USDT
  支付通道: 自定义 / epusdt
  商户ID: 随便填
  商户密钥: epusdt_secret_123  (和 epusdt 的 api_auth_token 一致)
  支付网关: http://127.0.0.1:8000   (epusdt 地址)
  启用: 是
```

### 6.4 创建商品 (对接 vpn-manager API)

创建三个商品，对应三个套餐:

```
商品1: VPN 单日套餐
├── 价格: 2.00 (元)
├── 发货方式: 第三方API发货
├── API 地址: http://127.0.0.1:8888/api/create
├── 请求方式: POST
├── 请求参数 (JSON):
│   {"secret": "你的vpn-manager密钥", "plan_id": 1}
├── 返回提取字段: sub_url
└── 显示给买家: "您的VPN订阅链接 (复制到V2rayN等客户端使用)"

商品2: VPN 月卡
├── 价格: 15.00
├── API 地址: http://127.0.0.1:8888/api/create
├── 请求参数: {"secret": "你的vpn-manager密钥", "plan_id": 2}
└── 其他同上

商品3: VPN 高级月卡
├── 价格: 25.00
├── API 地址: http://127.0.0.1:8888/api/create
├── 请求参数: {"secret": "你的vpn-manager密钥", "plan_id": 3}
└── 其他同上
```

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
8. 页面自动跳转显示: "您的订阅链接: http://sub.myvpn123.top:8888/sub/xxxxxx"

### 7.2 验证订阅可用

1. 复制订阅链接
2. 手机 V2rayN/Clash 里添加订阅
3. 更新订阅 → 应该出现两个节点:
   - `VPN-vl-reality` (直连)
   - `VPN-CF备用` (CF CDN)
4. 连接测试

### 7.3 检查管理面板

打开 `http://sub.myvpn123.top:8888/admin`:
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
或者打开管理面板 `http://sub.myvpn123.top:8888/admin`

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
  │                              独角数卡调用 API ──→ vpn-manager (:8888)
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
  ├── 管理面板: http://sub.myvpn123.top:8888/admin
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
| 8888 | vpn-manager 订阅服务器 | 订阅分发 + 管理面板 + API |
| 80 | 独角数卡 | 发卡网站 (买家访问) |
| 8000 | epusdt | USDT 收款网关 |
| 22 | SSH | 你的管理连接 |
