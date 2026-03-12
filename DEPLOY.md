# VPN Manager 部署指南

## 系统要求

- **操作系统**: Ubuntu 22.04 / 24.04 LTS (推荐), Debian 11+, CentOS 8+, Alpine 3.16+
- **架构**: amd64 (x86_64), arm64, armv7
- **Python**: 3.10+ (Ubuntu 22.04 自带 3.10, Ubuntu 24.04 自带 3.12)
- **权限**: root
- **网络**: 需要能访问 GitHub (下载 sing-box 二进制)

## 一、快速部署 (全新 VPS)

### 1. 上传文件到 VPS

将 `vpn-manager/` 目录上传到 VPS 的任意位置，推荐 `/opt/vpn-manager/`:

```bash
# 方法一: scp 上传
scp -r vpn-manager/ root@你的VPS_IP:/opt/vpn-manager/

# 方法二: 在 VPS 上 git clone (如果你把代码放到了 GitHub)
cd /opt && git clone https://你的仓库地址.git vpn-manager
```

### 2. 一键安装

```bash
cd /opt/vpn-manager
python3 main.py --install
```

安装向导会引导你:
- 选择协议 (推荐仅 VLESS-Reality)
- 配置端口 (默认 443)
- 设置 Reality SNI (默认 www.microsoft.com)

安装完成后自动:
- 下载并校验 sing-box 二进制
- 生成 Reality 密钥对和 UUID
- 创建配置文件和 systemd 服务
- 启动 sing-box
- 显示分享链接和二维码

### 3. 启动订阅服务器和管理面板

```bash
# 进入交互菜单
vpn-manager

# 选择 12 -> 1 启动订阅服务器
# 或者直接命令行启动:
vpn-manager --server &
```

### 4. 设置管理员密码

```bash
vpn-manager --set-admin-password
```

或者首次访问 `http://VPS_IP:8888/admin` 时在网页上设置。

### 5. 配置服务器容量

```bash
vpn-manager
# 选择 14 -> 输入你的 VPS 带宽和月流量
# 例如: 2500 Mbps, 1 TB
```

## 二、命令参考

```bash
# 交互式管理菜单
vpn-manager

# 安装 / 卸载 / 升级
vpn-manager --install              # 安装 sing-box
vpn-manager --upgrade              # 升级到最新版
vpn-manager --upgrade 1.12.0       # 升级到指定版本
vpn-manager --uninstall            # 卸载 (数据会备份)

# 订阅和管理
vpn-manager --server               # 启动订阅服务器 + Web 管理面板
vpn-manager --sync                 # 同步用户到 sing-box 配置
vpn-manager --check                # 检查流量和过期 (cron 每3分钟自动运行)
vpn-manager --gen-subs             # 重新生成所有订阅文件

# 管理
vpn-manager --set-admin-password   # 设置/重置管理员密码
vpn-manager --status               # 查看 sing-box 状态
```

## 三、防火墙配置

确保以下端口开放:

```bash
# VLESS-Reality (默认 443)
ufw allow 443/tcp

# 订阅服务器 + 管理面板 (默认 8888)
ufw allow 8888/tcp

# 如果启用了其他协议:
# ufw allow 8880/tcp   # VMess-WS
# ufw allow 8443/udp   # Hysteria2
# ufw allow 8844/udp   # TUIC
```

## 四、Web 管理面板

访问: `http://你的VPS_IP:8888/admin`

功能:
- 概览: 用户数、在线数、营收、带宽/流量利用率、系统状态
- 用户管理: 添加/删除/禁用/续费
- 实时监控: 在线用户、连接数、客户端 IP (每5秒刷新)
- 营收统计: 按日/月/总计，按套餐/来源分类，趋势图
- 操作日志: 所有管理操作的审计记录
- 系统设置: 套餐编辑、容量配置、API 密钥

## 五、发卡平台对接 (如何卖卡收款)

### 什么是发卡平台？

发卡平台是自动售卖虚拟商品(如订阅链接、卡密)的网站系统。买家付款后，平台自动发放一个订阅链接给买家。你不需要手动操作。

### 推荐的发卡平台

| 平台 | 特点 | 费用 |
|------|------|------|
| **独角数卡** (dujiaoka) | 开源自部署，无手续费 | 免费，需自己部署 |
| **发卡网** (faka.wiki) | 托管式，开箱即用 | 按交易收费 |
| **card.cm** | 托管式，界面简洁 | 按交易收费 |

推荐 **独角数卡** (开源免费，可部署在同一 VPS 或另一台 VPS 上)。

### 对接方式 (两种)

#### 方式一: 批量导入卡密 (简单，适合小规模)

1. 在 vpn-manager 菜单选择 `13. 发卡平台` → `1. 批量生成卡密`
2. 选择套餐，输入数量 (比如 50 张)
3. 系统生成文件如 `/etc/vpn-manager/cards_plan1_20260312_143000.txt`
4. 文件内容每行一个订阅链接:
   ```
   http://1.2.3.4:8888/sub/a1b2c3d4e5f6...
   http://1.2.3.4:8888/sub/f6e5d4c3b2a1...
   ...
   ```
5. 在发卡平台后台创建商品 → 选择「卡密模式」→ 导入这个文件
6. 买家付款后自动收到一个订阅链接

**优点**: 简单，不需要 API 对接
**缺点**: 需要提前生成库存，卖完要手动补货

#### 方式二: Webhook 自动发卡 (推荐，全自动)

1. 设置 API 密钥:
   ```bash
   vpn-manager
   # 选择 13 -> 2 设置 Webhook API 密钥
   # 记下生成的密钥，例如: a1b2c3d4e5f6789012345678901234567890abcd
   ```

2. 在发卡平台后台配置:
   - 创建商品 → 选择「API/回调模式」
   - 回调地址: `http://你的VPS_IP:8888/api/create`
   - 请求方式: `POST`
   - 请求头: `Content-Type: application/json`
   - 请求体:
     ```json
     {"secret": "你的API密钥", "plan_id": 1}
     ```
     (plan_id: 1=单日, 2=单月, 3=高级版)
   - 从返回 JSON 中提取 `sub_url` 字段显示给买家

3. 完整的请求/响应示例:
   ```bash
   # 请求
   curl -X POST http://1.2.3.4:8888/api/create \
     -H "Content-Type: application/json" \
     -d '{"secret":"a1b2c3d4...","plan_id":1}'

   # 成功响应
   {
     "success": true,
     "sub_url": "http://1.2.3.4:8888/sub/abc123...",
     "user_id": "u_12345678",
     "plan": "单日套餐",
     "traffic_gb": 10,
     "bandwidth_mbps": 20,
     "expires": "2026-03-13 14:30:00"
   }

   # 库存不足响应 (HTTP 409)
   {
     "success": false,
     "error": "库存不足: 带宽不足 (剩余可售 0 个)"
   }
   ```

4. 买家看到的流程: 访问你的发卡网站 → 选择套餐 → 付款 → 自动收到订阅链接

**优点**: 全自动，无需补货，实时库存检测
**缺点**: 需要配置 API 回调

### 收款方式

发卡平台支持多种收款渠道:

| 收款方式 | 说明 | 推荐 |
|----------|------|------|
| **支付宝当面付** | 个人可申请，扫码付款 | 适合国内买家 |
| **微信支付** | 需要商户号 | 适合国内买家 |
| **USDT/加密货币** | 通过第三方支付网关 (如 epusdt) | 适合匿名/海外买家 |
| **易支付** (epay) | 第三方聚合支付 | 简单接入 |
| **PayPal** | 国际支付 | 海外买家 |

#### 推荐方案: 独角数卡 + USDT

1. 部署独角数卡 (可以在同一 VPS):
   ```bash
   # Docker 一键部署
   docker run -d --name dujiaoka -p 80:80 \
     -v /data/dujiaoka:/data \
     stilleshan/dockerfiles:dujiaoka
   ```

2. 在独角数卡后台:
   - 设置 → 支付 → 添加 USDT 收款 (使用 epusdt 等)
   - 商品管理 → 新建商品 → 选择「第三方API」发货方式
   - 配置回调地址为 `http://VPS_IP:8888/api/create`

3. 买家流程:
   ```
   访问你的发卡网站 → 选择"月卡 15元" → USDT 付款 → 自动收到订阅链接
   ```

#### 方案二: 支付宝收款

1. 申请支付宝当面付 (个人账户即可)
2. 在发卡平台配置支付宝支付
3. 买家扫码付款后自动发卡

### 独角数卡详细对接步骤

1. **创建商品**:
   - 名称: "VPN 单日套餐 (24小时/10GB)"
   - 价格: 2.00
   - 发货方式: 选择「第三方API发货」

2. **配置发货API**:
   - API 地址: `http://VPS_IP:8888/api/create`
   - 请求方法: POST
   - 请求参数:
     ```json
     {"secret": "你的密钥", "plan_id": 1}
     ```
   - 提取返回值: `sub_url`
   - 展示给买家的文案: "您的专属订阅链接 (复制到V2rayN/Clash中使用)"

3. **每个套餐创建一个商品** (plan_id 分别为 1, 2, 3)

## 六、客户端使用说明 (告知买家)

买家收到订阅链接后:

### Windows / macOS
1. 下载 V2rayN 或 Clash Verge
2. 导入 → 粘贴订阅链接 → 更新订阅
3. 选择节点 → 开启系统代理

### iOS
1. App Store 下载 Shadowrocket (需海外 Apple ID)
2. 设置 → 订阅 → 添加链接
3. 开启连接

### Android
1. 下载 V2rayNG
2. 订阅设置 → 添加链接 → 更新
3. 选择节点连接

## 七、日常运维

### 查看状态
```bash
vpn-manager --status          # sing-box 状态
```
或访问 Web 面板 `http://VPS_IP:8888/admin`

### 备份
数据库位于 `/etc/vpn-manager/vpn-manager.db`，定期备份:
```bash
cp /etc/vpn-manager/vpn-manager.db /root/vpn-manager-backup.db
```

### 升级 sing-box
```bash
vpn-manager --upgrade
```

### 查看日志
```bash
journalctl -u sing-box -n 50    # sing-box 日志
journalctl -u vpn-sub -n 50     # 订阅服务器日志
```

### 流量重置 (每月)
过期用户流量会自动重置 (续费时)。服务器总流量需要在月初手动查看。

## 八、常见问题

**Q: 安装后客户端连不上？**
检查防火墙: `ufw status` 确认端口开放。

**Q: 管理面板打不开？**
确认订阅服务器运行中: `systemctl status vpn-sub`

**Q: 忘记管理员密码？**
```bash
vpn-manager --set-admin-password
```

**Q: 如何迁移到新 VPS？**
1. 备份 `/etc/vpn-manager/vpn-manager.db` 和 `/etc/s-box/`
2. 在新 VPS 执行 `vpn-manager --install`
3. 覆盖数据库文件，重启服务

**Q: 发卡平台和 VPN 可以部署在同一台 VPS 吗？**
可以。发卡平台通常用 80/443 端口，VPN 用其他端口，互不冲突。
