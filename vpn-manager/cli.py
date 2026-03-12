"""Interactive CLI menu with Chinese UI."""
import time
import subprocess
import sys

import database as db
import services
from config import SB_DIR, SUBS_DIR, load_server_params
from utils import bytes_to_gb


# ==================== Colors ====================

def red(s): print(f"\033[31m\033[01m{s}\033[0m")
def green(s): print(f"\033[32m\033[01m{s}\033[0m")
def yellow(s): print(f"\033[33m\033[01m{s}\033[0m")
def blue(s): print(f"\033[36m\033[01m{s}\033[0m")
def white(s): print(f"\033[37m\033[01m{s}\033[0m")

def prompt(msg: str) -> str:
    return input(f"\033[33m{msg}\033[0m")


# ==================== Installation ====================

def action_install():
    """Interactive sing-box installation wizard - all-in-one setup."""
    from installer import (
        full_install, detect_arch, detect_os, get_installed_version,
        open_firewall_port, validate_domain,
        DEFAULT_VLESS_PORT, DEFAULT_REALITY_SNI,
        DEFAULT_VMESS_PORT, DEFAULT_HY2_PORT, DEFAULT_TUIC_PORT, DEFAULT_ANYTLS_PORT,
    )
    import singbox

    existing = get_installed_version()
    if existing:
        from installer import service_status
        status = service_status()
        yellow(f"\n检测到已安装 sing-box v{existing} ({status})")
        print("  重新安装将:")
        print("    - 停止当前 sing-box 服务")
        print("    - 下载最新版 sing-box")
        print("    - 重新生成配置文件和密钥")
        print("    - 现有用户数据库保留不变")
        ans = prompt("是否重新安装？(y/N): ")
        if ans.lower() != "y":
            return

    print()
    green("=" * 60)
    green("  VPN Manager 一键安装向导")
    green("=" * 60)
    print(f"  系统: {detect_os()} | 架构: {detect_arch()}")
    print()
    yellow("  本向导将依次完成:")
    print("    [1] 安装 sing-box 代理核心")
    print("    [2] 配置 Cloudflare CDN 备用线路 (可选)")
    print("    [3] 启动订阅服务器 + Web 管理面板")
    print("    [4] 设置管理员密码")
    print()

    # ==================== Step 1: Protocol & Install ====================
    green("─" * 60)
    green("  第 1 步: 安装 sing-box")
    green("─" * 60)
    print()
    yellow("选择安装的协议:")
    print("  1. 仅 VLESS-Reality (推荐，最隐蔽)")
    print("  2. VLESS-Reality + VMess-WS (推荐，支持 CF CDN 备用)")
    print("  3. 全部协议 (VLESS + VMess + Hysteria2 + TUIC + AnyTLS)")
    print("  4. 自定义")
    proto_choice = prompt("请选择 [1-4] (默认2): ") or "2"

    vless_port = DEFAULT_VLESS_PORT
    vmess_port = 0
    hy2_port = 0
    tuic_port = 0
    anytls_port = 0

    if proto_choice in ("2", "3", "4"):
        vmess_port = DEFAULT_VMESS_PORT
    if proto_choice == "3":
        hy2_port = DEFAULT_HY2_PORT
        tuic_port = DEFAULT_TUIC_PORT
        anytls_port = DEFAULT_ANYTLS_PORT
    if proto_choice == "4":
        ans = prompt("启用 VMess-WS? (y/N): ")
        if ans.lower() == "y": vmess_port = DEFAULT_VMESS_PORT
        ans = prompt("启用 Hysteria2? (y/N): ")
        if ans.lower() == "y": hy2_port = DEFAULT_HY2_PORT
        ans = prompt("启用 TUIC? (y/N): ")
        if ans.lower() == "y": tuic_port = DEFAULT_TUIC_PORT
        ans = prompt("启用 AnyTLS? (y/N): ")
        if ans.lower() == "y": anytls_port = DEFAULT_ANYTLS_PORT

    # Port customization
    custom_port = prompt(f"\nVLESS 端口 [{vless_port}]: ")
    if custom_port:
        try:
            vless_port = int(custom_port)
        except ValueError:
            pass

    # Reality SNI
    reality_sni = prompt(f"Reality SNI [{DEFAULT_REALITY_SNI}]: ") or DEFAULT_REALITY_SNI

    # Install
    try:
        result = full_install(
            vless_port=vless_port,
            reality_sni=reality_sni,
            vmess_port=vmess_port,
            hy2_port=hy2_port,
            tuic_port=tuic_port,
            anytls_port=anytls_port,
        )
    except Exception as e:
        red(f"\n安装失败: {e}")
        return

    # Display results
    _show_install_result(result)

    # ==================== Step 2: Cloudflare CDN + 域名配置 ====================
    print()
    green("─" * 60)
    green("  第 2 步: Cloudflare 域名配置 (CDN 备用 + HTTPS 管理)")
    green("─" * 60)
    print()
    print("  配置 Cloudflare 域名后可获得:")
    print("    - VPN 备用线路: IP 被墙时通过 CF CDN 中转")
    print("    - 管理面板 HTTPS: 隐藏服务器 IP，加密访问")
    print("    - 发卡网站: 买家通过域名访问")
    print()
    print("  需要提前准备:")
    print("    1. 一个域名 (推荐 Namesilo / Cloudflare 购买)")
    print("    2. 域名已托管到 Cloudflare (使用 Cloudflare 的 DNS)")
    print()

    if vmess_port > 0:
        cf_input = prompt("  输入你的域名 (如 example.com 或 vpn.example.com，回车跳过): ").strip()
        if cf_input and validate_domain(cf_input):
            # Extract base domain: vpn.example.com → example.com
            parts = cf_input.split(".")
            if len(parts) > 2:
                base_domain = ".".join(parts[-2:])
            else:
                base_domain = cf_input
            cf_vpn_domain = f"vpn.{base_domain}"
            cf_admin_domain = f"admin.{base_domain}"
            cf_shop_domain = f"shop.{base_domain}"

            # Store all domains
            db.set_config("base_domain", base_domain)

            print()
            yellow(f"  域名: {base_domain}")
            print(f"    vpn.{base_domain}   → VPN CDN 备用线路")
            print(f"    admin.{base_domain} → 管理面板 (HTTPS, 端口 2096)")
            print(f"    shop.{base_domain}  → 发卡网站 (HTTP, 端口 80)")
            print()
            yellow("  请在 Cloudflare 添加以下 DNS 记录:")
            print()
            print(f"    ┌──────┬────────┬─────────────────┬───────────────────────┐")
            print(f"    │ 类型 │ 名称   │ 内容            │ 代理状态              │")
            print(f"    ├──────┼────────┼─────────────────┼───────────────────────┤")
            print(f"    │ A    │ vpn    │ {result['server_ip']:<15} │ 已代理 (橙色云朵)     │")
            print(f"    │ A    │ admin  │ {result['server_ip']:<15} │ 已代理 (橙色云朵)     │")
            print(f"    │ A    │ shop   │ {result['server_ip']:<15} │ 仅DNS (灰色,不代理)   │")
            print(f"    └──────┴────────┴─────────────────┴───────────────────────┘")
            print()
            yellow("  注意: shop 必须用灰色云朵 (仅 DNS)!")
            print(f"    因为端口 443 已被 VPN 占用，CF 代理会路由到错误端口")
            print()
            yellow("  SSL/TLS 设置:")
            print(f"    Cloudflare → SSL/TLS → 加密模式选 Full")
            print(f"    (服务器已自带 SSL 证书，不要选 Flexible)")
            print()

            try:
                from installer import add_cf_backup
                add_cf_backup(cf_vpn_domain)
                import json as _json
                protos = db.get_config_json("protocols", ["vless-reality"])
                if "vmess-ws" not in protos:
                    protos.append("vmess-ws")
                    db.set_config("protocols", _json.dumps(protos))
                green("  CF CDN 备用已配置!")
            except Exception as e:
                red(f"  CF 配置失败: {e}")
                yellow("  可稍后在菜单 [16. sing-box 管理] 中配置")
        elif cf_input:
            red("  域名格式无效，已跳过")
            yellow("  可稍后在菜单 [16. sing-box 管理] → [5. 配置 CF 备用] 中配置")
        else:
            yellow("  已跳过，可稍后在菜单 [16. sing-box 管理] 中配置")
    else:
        yellow("  未启用 VMess-WS 协议，CF CDN 备用需要 VMess-WS")
        yellow("  如需启用，可重新安装时选择选项 2 或 3")

    # ==================== Step 3: Subscription Server ====================
    print()
    green("─" * 60)
    green("  第 3 步: 启动订阅服务器 + Web 管理面板")
    green("─" * 60)
    print()
    print("  订阅服务器用于:")
    print("    - 分发用户订阅链接 (客户端自动更新节点)")
    print("    - Web 管理面板 (浏览器管理用户/查看统计)")
    print("    - 发卡平台 API 接口 (对接自动售卡)")
    print()
    print("  端口 2096 是 Cloudflare 支持的 HTTPS 端口")
    print("  配合 CF 代理，管理面板可通过 HTTPS 访问，且隐藏服务器 IP")
    print()

    from installer import DEFAULT_SUB_PORT, check_port_conflict, find_available_cf_https_port

    # Find best available port
    used_ports = {vless_port, vmess_port, hy2_port, tuic_port, anytls_port}
    default_sub = str(find_available_cf_https_port(used_ports))
    sub_port = db.get_config("sub_port", default_sub)
    # If old default 8888, suggest switching to CF HTTPS port
    if sub_port == "8888":
        sub_port = default_sub

    new_port = prompt(f"  订阅服务器端口 [{sub_port}] (推荐 CF HTTPS 端口): ").strip()
    if new_port:
        try:
            port_int = int(new_port)
            conflict = check_port_conflict(port_int)
            if conflict:
                red(f"  端口 {port_int} 已被占用: {conflict}")
                yellow(f"  使用默认端口 {sub_port}")
            else:
                sub_port = new_port
        except ValueError:
            yellow("  端口无效，使用默认值")

    db.set_config("sub_port", sub_port)

    # Open firewall for subscription server
    open_firewall_port(int(sub_port), "tcp")

    # Start subscription server
    _install_and_start_service()
    print()
    base_domain = db.get_config("base_domain", "")
    if base_domain:
        green(f"  管理面板: https://admin.{base_domain}:{sub_port}/admin")
    print(f"  管理面板 (直连): http://{result['server_ip']}:{sub_port}/admin")
    print()

    # ==================== Step 4: Admin Password ====================
    print()
    green("─" * 60)
    green("  第 4 步: 设置管理员密码")
    green("─" * 60)
    print()
    print("  管理员密码用于登录 Web 管理面板")
    print(f"  面板地址: http://{result['server_ip']}:{sub_port}/admin")
    print()

    while True:
        admin_pwd = prompt("  设置管理员密码 (至少6位): ").strip()
        if not admin_pwd:
            yellow("  已跳过，可稍后通过菜单 [15. Web 管理面板] 设置")
            break
        if len(admin_pwd) < 6:
            red("  密码至少6位，请重新输入")
            continue
        admin_pwd2 = prompt("  确认密码: ").strip()
        if admin_pwd != admin_pwd2:
            red("  两次密码不一致，请重新输入")
            continue
        from dashboard import hash_password
        db.set_config("admin_password", hash_password(admin_pwd))
        green("  管理员密码已设置!")
        break

    # ==================== Step 5: USDT Payment (epusdt) ====================
    print()
    green("─" * 60)
    green("  第 5 步: 部署 USDT 自动收款 (epusdt)")
    green("─" * 60)
    print()
    print("  epusdt 是开源的 USDT-TRC20 收款网关")
    print("  买家付 USDT → epusdt 检测到账 → 通知发卡平台发货")
    print()
    print("  需要: Docker (自动安装)、MySQL、Redis (自动部署)")
    print()
    ans = prompt("  是否现在部署 epusdt？(y/N): ").strip()
    epusdt_deployed = False
    if ans.lower() == "y":
        from installer import deploy_epusdt
        tron_addr = prompt("  TRON 钱包地址 (回车跳过，稍后配置): ").strip()
        tron_key = ""
        if tron_addr:
            tron_key = prompt("  TRON 钱包私钥: ").strip()
        epusdt_token = prompt("  epusdt API 密钥 (回车自动生成): ").strip()
        if not epusdt_token:
            import secrets as _s
            epusdt_token = _s.token_hex(16)
        if deploy_epusdt(tron_addr, tron_key, epusdt_token, result['server_ip']):
            db.set_config("epusdt_token", epusdt_token)
            epusdt_deployed = True
            green(f"  epusdt API 密钥: {epusdt_token}")
            if tron_addr:
                green(f"  TRON 钱包: {tron_addr}")
            else:
                print()
                yellow("  TRON 钱包未配置，请稍后在数据库中添加")
    if not epusdt_deployed:
        yellow("  已跳过，可稍后在菜单 [13. 发卡平台] 部署")

    # ==================== Step 6: Card Platform (独角数卡) ====================
    print()
    green("─" * 60)
    green("  第 6 步: 部署发卡网站 (独角数卡)")
    green("─" * 60)
    print()
    print("  独角数卡是开源发卡平台，买家在这里选套餐、付款、自动收到订阅链接")
    print()
    ans = prompt("  是否现在部署独角数卡？(y/N): ").strip()
    djk_deployed = False
    djk_port = 80
    if ans.lower() == "y":
        from installer import install_docker, deploy_dujiaoka
        if not install_docker():
            red("  Docker 安装失败，跳过")
        else:
            djk_port_in = prompt("  发卡网站端口 [80]: ").strip()
            djk_port = int(djk_port_in) if djk_port_in else 80
            djk_user = ""
            while not djk_user:
                djk_user = prompt("  管理员用户名 (必填): ").strip()
                if not djk_user:
                    red("  用户名不能为空")
            djk_pass = ""
            while not djk_pass:
                djk_pass = prompt("  管理员密码 (必填): ").strip()
                if not djk_pass:
                    red("  密码不能为空")
            if deploy_dujiaoka(djk_port, djk_user, djk_pass):
                djk_deployed = True
                print()
                green("  独角数卡已自动初始化完成!")
                print(f"    后台地址: http://{result['server_ip']}:{djk_port}/admin")
                print(f"    用户名: {djk_user}")
                print(f"    密码: {djk_pass}")
                print()
                epusdt_token = db.get_config("epusdt_token", "")
                if epusdt_token:
                    green("  epusdt 支付方式已自动配置!")
                else:
                    yellow("  后台配置支付 (对接 epusdt):")
                    print(f"    后台 → 支付设置 → 添加支付方式")
                print()
                green("  VPN 商品已自动创建!")
                yellow("  添加库存 (卡密):")
                print(f"    方式1: vpn-manager 菜单 [13. 发卡平台] → [1. 批量生成卡密]")
                print(f"    方式2: 独角数卡后台 → 商品管理 → 卡密 → 手动添加")
                print()
                yellow("  管理商品:")
                print(f"    独角数卡后台 → 商品管理 (可修改名称/价格/描述)")
    if not djk_deployed:
        yellow("  已跳过，可稍后手动部署 (参考部署指南第六步)")

    # ==================== Final Summary ====================
    print()
    green("=" * 60)
    green("  安装全部完成!")
    green("=" * 60)
    print()
    print(f"  服务器 IP:      {result['server_ip']}")
    print(f"  sing-box:       v{result['version']} (运行中)")
    print(f"  VLESS 端口:     {result['vless_port']}")
    if result['vmess_port']:
        print(f"  VMess-WS 端口:  {result['vmess_port']}")

    base_domain = db.get_config("base_domain", "")
    cf_domain = db.get_config("cf_domain", "")

    if base_domain:
        print()
        yellow("  域名配置:")
        print(f"    CF CDN 备用:  vpn.{base_domain}")
        print(f"    管理面板:     https://admin.{base_domain}:{sub_port}/admin")
        if djk_deployed:
            print(f"    发卡网站:     http://shop.{base_domain}")
    elif cf_domain:
        derived_base = cf_domain.split(".", 1)[-1] if "." in cf_domain else cf_domain
        print()
        yellow("  域名配置:")
        print(f"    CF CDN 备用:  {cf_domain}")
        print(f"    管理面板:     https://admin.{derived_base}:{sub_port}/admin")
        if djk_deployed:
            print(f"    发卡网站:     http://shop.{derived_base}")

    print()
    yellow("  直连地址 (无域名也可用):")
    print(f"    管理面板:     http://{result['server_ip']}:{sub_port}/admin")
    if epusdt_deployed:
        print(f"    epusdt 收款:  http://{result['server_ip']}:8000")
    if djk_deployed:
        print(f"    发卡网站:     http://{result['server_ip']}:{djk_port}")

    if base_domain:
        print()
        yellow("  重要: 确保 Cloudflare 已完成以下配置:")
        print(f"    1. DNS: vpn 和 admin → 已代理 (橙色云朵)")
        print(f"           shop → 仅 DNS (灰色云朵)")
        print(f"    2. SSL/TLS 加密模式: Full")

    print()
    yellow("  常用命令:")
    print("    vpn-manager          # 进入管理菜单")
    print("    vpn-manager --status # 查看服务状态")
    print()
    green("=" * 60)


def _show_install_result(result: dict):
    """Display installation results with sharing links."""
    print()
    green("=" * 60)
    green("  安装成功! 节点信息:")
    green("=" * 60)
    print(f"  服务器 IP: {result['server_ip']}")
    print(f"  sing-box:  v{result['version']}")
    print(f"  UUID:      {result['uuid']}")
    print()

    yellow("  VLESS-Reality:")
    print(f"    端口: {result['vless_port']}")
    print(f"    SNI:  {result['reality_sni']}")
    print(f"    公钥: {result['reality_public_key']}")
    print(f"    SID:  {result['reality_short_id']}")
    print()

    # Generate sharing link
    from singbox import gen_vless_link
    params = {
        "server_ip": result["server_ip"],
        "vl_port": result["vless_port"],
        "vl_sni": result["reality_sni"],
        "vl_sid": result["reality_short_id"],
        "public_key": result["reality_public_key"],
    }
    link = gen_vless_link(result["uuid"], "VPN", params)
    if link:
        yellow("  分享链接:")
        print(f"  {link}")
        print()
        try:
            subprocess.run(["qrencode", "-t", "ansiutf8", link], timeout=5)
        except Exception:
            yellow("  (安装 qrencode 可显示二维码)")

    if result["vmess_port"]:
        print(f"\n  VMess-WS: 端口 {result['vmess_port']}, 路径 {result['vmess_path']}")
    if result["hy2_port"]:
        print(f"  Hysteria2: 端口 {result['hy2_port']}")
    if result["tuic_port"]:
        print(f"  TUIC: 端口 {result['tuic_port']}")
    if result["anytls_port"]:
        print(f"  AnyTLS: 端口 {result['anytls_port']}")

    green("=" * 60)


# ==================== Display Helpers ====================

def show_plans():
    plans = services.list_plans()
    print()
    white("+" + "-"*4 + "+" + "-"*18 + "+" + "-"*8 + "+" + "-"*8 + "+" + "-"*10 + "+" + "-"*8 + "+" + "-"*6 + "+")
    print(f"| {'ID':>2} | {'名称':<16} | {'时长':>6} | {'流量':>6} | {'带宽':>8} | {'价格':>6} | {'连接':>4} |")
    white("+" + "-"*4 + "+" + "-"*18 + "+" + "-"*8 + "+" + "-"*8 + "+" + "-"*10 + "+" + "-"*8 + "+" + "-"*6 + "+")
    for p in plans:
        price = f"{p.get('price', 0):.0f}元"
        max_c = p.get('max_connections', 5)
        print(f"| {p['id']:>2} | {p['name']:<14} | {p['duration_hours']:>4}h | {p['traffic_gb']:>4.0f}GB | {p['bandwidth_mbps']:>5}Mbps | {price:>6} | {max_c:>4} |")
    white("+" + "-"*4 + "+" + "-"*18 + "+" + "-"*8 + "+" + "-"*8 + "+" + "-"*10 + "+" + "-"*8 + "+" + "-"*6 + "+")


def show_users():
    users = services.list_users()
    if not users:
        yellow("暂无用户")
        return
    print()
    white(f"用户列表 (共 {len(users)} 个):")
    print("-" * 90)
    print(f"{'ID':<12} {'备注':<10} {'套餐':<6} {'状态':<8} {'过期时间':<14} {'已用':>8} {'限额':>8} {'连接':>4}")
    print("-" * 90)
    for u in users:
        exp = time.strftime("%m-%d %H:%M", time.localtime(u["expires_at"]))
        used = f"{bytes_to_gb(u['traffic_used_bytes'])}GB"
        limit = "无限" if u["traffic_limit_bytes"] == 0 else f"{bytes_to_gb(u['traffic_limit_bytes'])}GB"
        max_c = u.get("max_connections", "-")
        status_colors = {"active": "\033[32m在线\033[0m", "expired": "\033[31m过期\033[0m",
                         "overlimit": "\033[31m超额\033[0m", "disabled": "\033[33m禁用\033[0m"}
        status = status_colors.get(u["status"], u["status"])
        print(f"{u['id']:<12} {u['remark']:<10} {u.get('plan_id', '?'):<6} {status:<16} {exp:<14} {used:>8} {limit:>8} {str(max_c):>4}")
    print("-" * 90)


def show_inventory():
    inv = services.get_inventory_status()
    print()
    green("=" * 60)
    green("  服务器容量与库存状态")
    green("=" * 60)
    print(f"  服务器带宽:   {inv['server_bandwidth_mbps']} Mbps")
    print(f"  月流量配额:   {inv['server_monthly_traffic_tb']} TB")
    print(f"  活跃用户数:   {inv['active_users']}")
    print()
    print(f"  已分配带宽:   {inv['total_bw_allocated_mbps']} / {inv['server_bandwidth_mbps']} Mbps ({inv['bw_utilization_pct']}%)")
    print(f"  已分配流量:   {inv['total_traffic_allocated_gb']} GB / {inv['server_monthly_traffic_tb']*1024:.0f} GB ({inv['traffic_utilization_pct']}%)")
    print(f"  已用总流量:   {inv['total_traffic_used_gb']} GB")
    print()

    bw_bar = _bar(inv["bw_utilization_pct"])
    tr_bar = _bar(inv["traffic_utilization_pct"])
    print(f"  带宽利用率:  {bw_bar} {inv['bw_utilization_pct']}%")
    print(f"  流量利用率:  {tr_bar} {inv['traffic_utilization_pct']}%")
    print()

    yellow("  各套餐可售库存:")
    for pid, cap in inv["plan_capacity"].items():
        color = "\033[32m" if cap["available"] > 5 else "\033[33m" if cap["available"] > 0 else "\033[31m"
        print(f"    套餐{pid} [{cap['plan_name']}]: {color}{cap['available']}\033[0m 个可售 "
              f"(带宽限: {cap['bw_slots']}, 流量限: {cap['traffic_slots']})")
    green("=" * 60)


def _bar(pct: float, width: int = 30) -> str:
    filled = int(width * min(pct, 100) / 100)
    color = "\033[32m" if pct < 70 else "\033[33m" if pct < 90 else "\033[31m"
    return f"{color}[{'#' * filled}{'-' * (width - filled)}]\033[0m"


# ==================== Actions ====================

def action_add_user():
    show_plans()
    show_inventory()
    pid = prompt("\n选择套餐 [1-3]: ")
    try:
        pid = int(pid)
    except ValueError:
        red("无效输入"); return

    can, reason = services.check_can_sell(pid)
    if not can:
        red(f"无法创建: {reason}"); return

    remark = prompt("用户备注 (回车跳过): ")
    try:
        user = services.add_user(pid, remark)
    except Exception as e:
        red(str(e)); return

    plan = services.get_plan(pid)
    sub_url = services.get_sub_url(user["token"])
    print()
    green("=" * 50)
    green("  用户创建成功!")
    green("=" * 50)
    print(f"  用户ID:    {user['id']}")
    print(f"  UUID:      {user['uuid']}")
    print(f"  备注:      {user['remark']}")
    print(f"  套餐:      {plan['name']} ({plan.get('price', 0)}元)")
    print(f"  带宽限制:  {plan['bandwidth_mbps']}Mbps (所有设备共享)")
    print(f"  最大连接:  {plan.get('max_connections', 5)}")
    print(f"  过期时间:  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(user['expires_at']))}")
    print(f"  流量限额:  {user['traffic_gb']}GB")
    print()
    yellow("  订阅链接:")
    print(f"  {sub_url}")
    print()
    try:
        subprocess.run(["qrencode", "-t", "ansiutf8", sub_url], timeout=5)
    except Exception:
        yellow("  (qrencode 未安装，跳过二维码)")


def action_del_user():
    show_users()
    uid = prompt("\n输入要删除的用户ID: ")
    user = services.get_user(uid)
    if not user:
        red("用户不存在"); return
    confirm = prompt(f"确认删除 [{user['remark']}] ({uid})? (y/N): ")
    if confirm.lower() != "y":
        yellow("已取消"); return
    services.delete_user(uid)
    green(f"用户 {uid} 已删除")


def action_user_info():
    uid = prompt("输入用户ID: ")
    user = services.get_user(uid)
    if not user:
        red("用户不存在"); return
    print()
    green("=" * 50)
    green(f"  用户详情: {user['remark']}")
    green("=" * 50)
    print(f"  用户ID:     {user['id']}")
    print(f"  UUID:       {user['uuid']}")
    print(f"  套餐:       {user.get('plan_name', '管理员')}")
    print(f"  带宽限制:   {user.get('bandwidth_mbps', '无限')}Mbps (所有设备共享)")
    print(f"  最大连接:   {user.get('max_connections', '无限')}")
    print(f"  状态:       {user['status']}")
    print(f"  创建时间:   {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(user['created_at']))}")
    print(f"  过期时间:   {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(user['expires_at']))}")
    limit_str = "无限" if user["traffic_limit_bytes"] == 0 else f"{bytes_to_gb(user['traffic_limit_bytes'])}GB"
    print(f"  流量限额:   {limit_str}")
    print(f"  已用流量:   {bytes_to_gb(user['traffic_used_bytes'])}GB "
          f"(上行: {bytes_to_gb(user['traffic_up_bytes'])}GB / 下行: {bytes_to_gb(user['traffic_down_bytes'])}GB)")
    print()
    sub_url = services.get_sub_url(user["token"])
    yellow("  订阅链接:")
    print(f"  {sub_url}")
    print()
    try:
        subprocess.run(["qrencode", "-t", "ansiutf8", sub_url], timeout=5)
    except Exception:
        pass


def action_renew():
    show_users()
    uid = prompt("\n输入要续费的用户ID: ")
    user = services.get_user(uid)
    if not user:
        red("用户不存在"); return
    show_plans()
    pid = prompt("选择新套餐 [1-3]: ")
    try:
        services.renew_user(uid, int(pid))
        plan = services.get_plan(int(pid))
        green(f"用户 {uid} 已续费: {plan['name']}")
    except Exception as e:
        red(str(e))


def action_set_traffic():
    uid = prompt("输入用户ID: ")
    user = services.get_user(uid)
    if not user:
        red("用户不存在"); return
    yellow(f"当前已用流量: {bytes_to_gb(user['traffic_used_bytes'])}GB")
    gb = prompt("设置已用流量 (GB, 输入0重置): ")
    try:
        services.set_traffic(uid, float(gb))
        green(f"已更新为 {gb}GB")
    except Exception as e:
        red(str(e))


def action_toggle_user():
    uid = prompt("输入用户ID: ")
    try:
        new_status = services.toggle_user(uid)
        green(f"用户 {uid} 已{'禁用' if new_status == 'disabled' else '启用'}")
    except Exception as e:
        red(str(e))


def action_batch_add():
    show_plans()
    show_inventory()
    pid = prompt("\n选择套餐 [1-3]: ")
    count = prompt("批量创建数量: ")
    try:
        pid, count = int(pid), int(count)
    except ValueError:
        red("无效输入"); return

    can, reason = services.check_can_sell(pid, count)
    if not can:
        red(f"无法创建: {reason}"); return

    users = services.batch_add(pid, count)
    sub_port = db.get_config("sub_port", "8888")
    server_ip = services.get_sub_url("x").rsplit("/sub/", 1)[0]
    green(f"\n批量创建 {count} 个用户完成:")
    print("-" * 70)
    for u in users:
        print(f"  {u['id']}  {server_ip}/sub/{u['token']}")
    print("-" * 70)


def action_export():
    users = services.list_users()
    if not users:
        yellow("暂无用户"); return
    print()
    for u in users:
        sub_url = services.get_sub_url(u["token"])
        exp = time.strftime("%Y-%m-%d %H:%M", time.localtime(u["expires_at"]))
        print(f"{u['id']} | {u['remark']} | 套餐{u['plan_id']} | {u['status']} | {exp} | "
              f"{bytes_to_gb(u['traffic_used_bytes'])}GB | {sub_url}")


def action_edit_plan():
    show_plans()
    pid = prompt("\n输入要修改的套餐ID (1-3): ")
    plan = services.get_plan(int(pid))
    if not plan:
        red("无效套餐"); return
    name = prompt(f"套餐名称 [{plan['name']}]: ") or plan["name"]
    hours = prompt(f"有效时长(小时) [{plan['duration_hours']}]: ") or str(plan["duration_hours"])
    gb = prompt(f"流量限额(GB) [{plan['traffic_gb']}]: ") or str(plan["traffic_gb"])
    bw = prompt(f"带宽限制(Mbps) [{plan['bandwidth_mbps']}]: ") or str(plan["bandwidth_mbps"])
    price = prompt(f"价格(元) [{plan.get('price', 0)}]: ") or str(plan.get("price", 0))
    max_c = prompt(f"最大连接数 [{plan.get('max_connections', 5)}]: ") or str(plan.get("max_connections", 5))
    services.update_plan(int(pid), name=name, duration_hours=int(hours),
                         traffic_gb=float(gb), bandwidth_mbps=int(bw),
                         price=float(price), max_connections=int(max_c))
    green(f"套餐 {pid} 已更新")


def action_protocol_menu():
    protos = db.get_config_json("protocols", ["vless-reality"])
    yellow(f"\n当前启用: {', '.join(protos)}")
    print("1. 仅 VLESS-Reality (推荐)")
    print("2. VLESS-Reality + VMess-WS")
    print("3. 全部协议")
    print("4. 自定义")
    print("0. 返回")
    c = prompt("请选择: ")
    new = None
    if c == "1":
        new = ["vless-reality"]
    elif c == "2":
        new = ["vless-reality", "vmess-ws"]
    elif c == "3":
        new = ["vless-reality", "vmess-ws", "hysteria2", "tuic", "anytls"]
    elif c == "4":
        s = prompt("输入协议 (逗号分隔): ")
        new = [x.strip() for x in s.split(",") if x.strip()]
    if new:
        import json as _json
        db.set_config("protocols", _json.dumps(new))
        green(f"协议已更新: {', '.join(new)}")
        yellow("请执行 [刷新订阅] 使更改生效")


def _import_cards_to_dujiaoka(plan_id: int, urls: list[str]):
    """Auto-import generated card keys into dujiaoka's carmis table."""
    try:
        r = subprocess.run(
            ["docker", "ps", "--filter", "name=^dujiaoka$", "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        if not r.stdout.strip() or "Up" not in r.stdout:
            yellow("独角数卡未运行，卡密仅保存到文件")
            return
        mysql_password = db.get_config("mysql_password", "")
        if not mysql_password:
            return
        # Find matching goods_id in dujiaoka (by ord = plan_id position)
        r = subprocess.run(
            ["docker", "exec", "payment-mysql", "mysql",
             "-uroot", f"-p{mysql_password}", "dujiaoka", "-N",
             "-e", f"SELECT id FROM goods WHERE ord={plan_id} LIMIT 1"],
            capture_output=True, text=True, timeout=10,
        )
        goods_id = r.stdout.strip()
        if not goods_id:
            # Fallback: use plan_id as goods_id directly
            goods_id = str(plan_id)
        # Batch insert card keys
        values = ", ".join(
            f"({goods_id}, 1, 0, '{url}', NOW(), NOW())" for url in urls
        )
        insert_sql = (
            f"INSERT INTO carmis (goods_id, status, is_loop, carmi, created_at, updated_at) "
            f"VALUES {values}"
        )
        r = subprocess.run(
            ["docker", "exec", "payment-mysql", "mysql",
             "-uroot", f"-p{mysql_password}", "dujiaoka", "-e", insert_sql],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            green(f"已自动导入 {len(urls)} 张卡密到独角数卡")
        else:
            yellow(f"自动导入失败: {(r.stderr or '')[:100]}")
            yellow("请手动在独角数卡后台导入卡密文件")
    except Exception as e:
        yellow(f"自动导入跳过: {e}")


def action_card_platform():
    print()
    green("  发卡平台对接")
    print("  " + "-" * 50)
    print("  通过独角数卡发卡: 生成卡密 → 导入独角数卡 → 买家购买自动发货")
    print()
    print("  1. 批量生成卡密 (自动导入独角数卡)")
    print("  2. 设置 Webhook API 密钥 (外部平台对接用)")
    print("  3. 查看 API 对接说明")
    print("  4. 部署 epusdt (USDT收款网关)")
    print("  5. 部署独角数卡 (发卡网站)")
    print("  0. 返回")
    c = prompt("请选择: ")
    if c == "4":
        _deploy_epusdt_menu()
        return
    elif c == "5":
        _deploy_dujiaoka_menu()
        return
    elif c == "1":
        show_plans()
        show_inventory()
        pid = prompt("\n选择套餐: ")
        count = prompt("生成数量: ")
        try:
            pid, count = int(pid), int(count)
        except ValueError:
            red("无效输入"); return
        can, reason = services.check_can_sell(pid, count)
        if not can:
            red(f"库存不足: {reason}"); return
        urls = services.generate_cards(pid, count)
        plan = services.get_plan(pid)
        card_file = str(services.MANAGER_DIR / f"cards_plan{pid}_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        with open(card_file, "w") as f:
            f.write("\n".join(urls))
        green(f"\n生成 {count} 张 [{plan['name']}] 卡密")
        yellow(f"卡密文件: {card_file}")
        for u in urls[:5]:
            print(f"  {u}")
        if len(urls) > 5:
            print(f"  ... (共 {len(urls)} 行)")
        # Auto-import into dujiaoka if running
        _import_cards_to_dujiaoka(pid, urls)
    elif c == "2":
        current = db.get_config("api_secret", "")
        if current:
            yellow(f"当前密钥: {current}")
        new = prompt("输入新密钥 (回车自动生成): ")
        if not new:
            import secrets as _s
            new = _s.token_hex(20)
        db.set_config("api_secret", new)
        green(f"API 密钥: {new}")
        _show_api_info()
    elif c == "3":
        _show_api_info()


def _deploy_epusdt_menu():
    """Deploy epusdt from the card platform menu."""
    from installer import deploy_epusdt
    print()
    yellow("  部署 epusdt (USDT-TRC20 自动收款)")
    print("  " + "-" * 50)
    # Check Docker container status
    r = subprocess.run(
        ["docker", "ps", "--filter", "name=^epusdt$", "--format", "{{.Status}}"],
        capture_output=True, text=True, timeout=10,
    )
    if r.stdout.strip() and "Up" in r.stdout:
        green(f"  epusdt 状态: 运行中 ({r.stdout.strip()})")
        ans = prompt("  是否重新部署？(y/N): ").strip()
        if ans.lower() != "y":
            return
    print()
    print("  epusdt 需要 Docker + MySQL + Redis (自动部署)")
    print()
    tron_addr = prompt("  TRON 钱包地址 (回车跳过): ").strip()
    tron_key = ""
    if tron_addr:
        tron_key = prompt("  TRON 钱包私钥: ").strip()
    token = prompt("  API 密钥 (回车自动生成): ").strip()
    if not token:
        import secrets as _s
        token = _s.token_hex(16)
    server_ip = ""
    try:
        server_ip = (SB_DIR / "server_ipcl.log").read_text().strip()
    except Exception:
        pass
    if deploy_epusdt(tron_addr, tron_key, token, server_ip):
        db.set_config("epusdt_token", token)
        green(f"\n  epusdt 部署成功!")
        print(f"  API 密钥: {token}")
        print(f"  地址: http://{server_ip}:8000")
        if tron_addr:
            green(f"  TRON 钱包: {tron_addr}")
        yellow("\n  在独角数卡后台配置支付时填入此密钥")


def _deploy_dujiaoka_menu():
    """Deploy 独角数卡 from the card platform menu."""
    from installer import install_docker, deploy_dujiaoka
    print()
    yellow("  部署独角数卡 (发卡网站)")
    print("  " + "-" * 50)
    r = subprocess.run(["docker", "ps", "--filter", "name=dujiaoka", "--format", "{{.Status}}"],
                       capture_output=True, text=True, timeout=10)
    if r.stdout.strip():
        green(f"  独角数卡状态: {r.stdout.strip()}")
        ans = prompt("  是否重新部署？(y/N): ").strip()
        if ans.lower() != "y":
            return
    port = prompt("  端口 [80]: ").strip()
    port = int(port) if port else 80
    admin_user = ""
    while not admin_user:
        admin_user = prompt("  管理员用户名 (必填): ").strip()
        if not admin_user:
            red("  用户名不能为空")
    admin_pass = ""
    while not admin_pass:
        admin_pass = prompt("  管理员密码 (必填): ").strip()
        if not admin_pass:
            red("  密码不能为空")
    if not install_docker():
        red("  Docker 安装失败"); return
    if deploy_dujiaoka(port, admin_user, admin_pass):
        server_ip = ""
        try:
            server_ip = (SB_DIR / "server_ipcl.log").read_text().strip()
        except Exception:
            pass
        sub_port = db.get_config("sub_port", "8888")
        api_secret = db.get_config("api_secret", "")
        if not api_secret:
            import secrets as _s
            api_secret = _s.token_hex(20)
            db.set_config("api_secret", api_secret)
        print()
        green("  独角数卡部署成功! (已自动初始化)")
        print(f"    后台地址: http://{server_ip}:{port}/admin")
        print(f"    用户名: {admin_user}")
        print(f"    密码: {admin_pass}")
        print()
        epusdt_token = db.get_config("epusdt_token", "")
        if epusdt_token:
            green("  epusdt 支付方式已自动配置!")
        else:
            yellow("  后台配置支付 (对接 epusdt):")
            print(f"    支付设置 → 添加支付方式")
        print()
        yellow("  后台创建商品 (对接 vpn-manager):")
        print(f"    发货方式: 第三方API发货")
        print(f"    API 地址: http://127.0.0.1:{sub_port}/api/create")
        print(f"    请求参数: {{\"secret\": \"{api_secret}\", \"plan_id\": 1}}")
        print(f"    (plan_id: 1=单日¥2, 2=月卡¥15, 3=高级月卡¥25)")
        print(f"    返回提取字段: sub_url")


def _show_api_info():
    sub_port = db.get_config("sub_port", "8888")
    server_ip = ""
    try:
        server_ip = (SB_DIR / "server_ipcl.log").read_text().strip()
    except Exception:
        pass
    api_secret = db.get_config("api_secret", "未设置")
    print()
    yellow("发卡平台 API 配置:")
    print(f"  回调地址: http://{server_ip}:{sub_port}/api/create")
    print(f"  API密钥:  {api_secret}")
    print(f"  请求方式: POST JSON")
    print(f'  请求体:   {{"secret": "{api_secret}", "plan_id": 1}}')
    print(f"  plan_id:  1=单日, 2=单月, 3=高级")
    print()
    yellow("curl 示例:")
    print(f'  curl -X POST http://{server_ip}:{sub_port}/api/create \\')
    print(f'    -H "Content-Type: application/json" \\')
    print(f'    -d \'{{"secret":"{api_secret}","plan_id":1}}\'')
    print()
    yellow("发卡平台回调返回:")
    print('  {"success":true,"sub_url":"http://...","plan":"套餐名","traffic_gb":10,...}')
    print('  sub_url 即为客户的订阅链接，平台展示给买家即可')


def action_server_capacity():
    bw = db.get_config("server_bandwidth_mbps", "2500")
    tb = db.get_config("server_monthly_traffic_tb", "1.0")
    yellow(f"\n当前配置: 带宽 {bw}Mbps, 月流量 {tb}TB")
    new_bw = prompt(f"服务器带宽(Mbps) [{bw}]: ") or bw
    new_tb = prompt(f"月流量配额(TB) [{tb}]: ") or tb
    db.set_config("server_bandwidth_mbps", new_bw)
    db.set_config("server_monthly_traffic_tb", new_tb)
    green(f"已更新: {new_bw}Mbps / {new_tb}TB")
    show_inventory()


def action_sub_server():
    print("\n1. 启动/重启订阅服务器 (含管理面板)")
    print("2. 停止订阅服务器")
    print("3. 查看状态")
    print("4. 修改端口")
    print("0. 返回")
    c = prompt("请选择: ")
    if c == "1":
        _install_and_start_service()
    elif c == "2":
        subprocess.run(["systemctl", "stop", "vpn-sub"], capture_output=True)
        yellow("已停止")
    elif c == "3":
        r = subprocess.run(["systemctl", "is-active", "vpn-sub"], capture_output=True, text=True)
        if "active" in r.stdout:
            port = db.get_config("sub_port", "8888")
            server_ip = ""
            try:
                server_ip = (SB_DIR / "server_ipcl.log").read_text().strip()
            except Exception:
                pass
            green(f"运行中 (端口: {port})")
            yellow(f"管理面板: http://{server_ip or '服务器IP'}:{port}/admin")
        else:
            red("未运行")
    elif c == "4":
        new_port = prompt("新端口号: ")
        db.set_config("sub_port", new_port)
        _install_and_start_service()


def _install_and_start_service():
    port = db.get_config("sub_port", "8888")
    script_dir = str(__import__("pathlib").Path(__file__).parent.resolve())
    service = f"""[Unit]
Description=VPN Subscription Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 {script_dir}/main.py --server
Restart=on-failure
RestartSec=5
WorkingDirectory={script_dir}

[Install]
WantedBy=multi-user.target
"""
    __import__("pathlib").Path("/etc/systemd/system/vpn-sub.service").write_text(service)
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
    subprocess.run(["systemctl", "enable", "vpn-sub"], capture_output=True)
    subprocess.run(["systemctl", "restart", "vpn-sub"], capture_output=True)
    server_ip = ""
    try:
        server_ip = (SB_DIR / "server_ipcl.log").read_text().strip()
    except Exception:
        pass
    green(f"订阅服务器已启动 (端口: {port})")
    yellow(f"管理面板: http://{server_ip or '服务器IP'}:{port}/admin")


def action_admin_dashboard():
    sub_port = db.get_config("sub_port", "8888")
    server_ip = ""
    try:
        server_ip = (SB_DIR / "server_ipcl.log").read_text().strip()
    except Exception:
        pass
    pwd = db.get_config("admin_password", "")
    print()
    green("=" * 60)
    green("  Web 管理面板")
    green("=" * 60)
    if not pwd:
        yellow("  管理员密码未设置，首次访问面板时将提示设置")
    else:
        green("  管理员密码已设置")
    print()
    print(f"  面板地址: http://{server_ip or '服务器IP'}:{sub_port}/admin")
    print()
    print("  1. 启动/重启服务")
    print("  2. 重置管理员密码")
    print("  0. 返回")
    c = prompt("  请选择: ")
    if c == "1":
        _install_and_start_service()
    elif c == "2":
        new_pwd = prompt("  输入新密码 (至少6位): ")
        if len(new_pwd) < 6:
            red("  密码太短"); return
        from dashboard import hash_password
        db.set_config("admin_password", hash_password(new_pwd))
        green("  密码已重置")


def action_singbox_mgmt():
    """sing-box management submenu."""
    from installer import get_installed_version, service_status, upgrade_singbox
    ver = get_installed_version()
    status = service_status()
    color = "\033[32m" if status == "active" else "\033[31m"
    print(f"\n  sing-box v{ver}: {color}{status}\033[0m")
    print()
    cf_domain = db.get_config("cf_domain", "")
    cf_status = f"\033[32m已配置 ({cf_domain})\033[0m" if cf_domain else "\033[33m未配置\033[0m"
    print(f"  CF 备用: {cf_status}")
    print()
    print("  1. 重启 sing-box")
    print("  2. 停止 sing-box")
    print("  3. 升级 sing-box")
    print("  4. 查看日志")
    print("  5. 配置 Cloudflare CDN 备用")
    print("  6. 重新安装")
    print("  0. 返回")
    c = prompt("  请选择: ")
    if c == "1":
        from installer import restart_service
        restart_service()
        green("  已重启")
    elif c == "2":
        from installer import stop_service
        stop_service()
        yellow("  已停止")
    elif c == "3":
        try:
            upgrade_singbox()
        except Exception as e:
            red(f"  升级失败: {e}")
    elif c == "4":
        r = subprocess.run(["journalctl", "-u", "sing-box", "-n", "30", "--no-pager"],
                          capture_output=True, text=True, timeout=10)
        print(r.stdout)
    elif c == "5":
        action_setup_cf()
    elif c == "6":
        action_install()


def action_setup_cf():
    """Configure Cloudflare CDN backup."""
    from installer import add_cf_backup
    cf_domain = db.get_config("cf_domain", "")
    print()
    green("  Cloudflare CDN 备用线路配置")
    green("  " + "=" * 50)
    print()
    print("  原理: 当 VPS 的 IP 被墙时，流量通过 Cloudflare CDN 中转")
    print("  前提: 你有一个域名，且已托管到 Cloudflare")
    print()
    if cf_domain:
        yellow(f"  当前 CF 域名: {cf_domain}")
        print("  1. 修改域名")
        print("  2. 移除 CF 备用")
        print("  0. 返回")
        c = prompt("  请选择: ")
        if c == "1":
            domain = prompt("  输入新域名 (如 vpn.example.com): ").strip()
            if domain:
                add_cf_backup(domain)
                # Refresh subs
                services.generate_all_subs()
                green("  订阅已刷新，用户将自动获得 CF 备用线路")
        elif c == "2":
            db.set_config("cf_domain", "")
            services.generate_all_subs()
            green("  CF 备用已移除")
    else:
        print("  配置步骤:")
        print("  1. 在 Cloudflare 添加你的域名")
        print("  2. 添加 A 记录指向 VPS IP，代理状态开启 (橙色云朵)")
        print("  3. SSL/TLS 设置为 Flexible")
        print("  4. 在下方输入你的域名")
        print()
        domain = prompt("  输入域名 (如 vpn.example.com，回车跳过): ").strip()
        if domain:
            try:
                add_cf_backup(domain)
                # Also enable vmess-ws protocol for subscriptions
                import json as _json
                protos = db.get_config_json("protocols", ["vless-reality"])
                if "vmess-ws" not in protos:
                    protos.append("vmess-ws")
                    db.set_config("protocols", _json.dumps(protos))
                services.generate_all_subs()
                green("  配置完成! 订阅已包含 CF 备用线路")
            except Exception as e:
                red(f"  配置失败: {e}")


def action_view_logs():
    """View operation logs."""
    logs = services.get_recent_logs(30)
    if not logs:
        yellow("暂无操作日志")
        return
    print()
    white("操作日志 (最近30条):")
    print("-" * 80)
    for log in logs:
        ts = time.strftime("%m-%d %H:%M", time.localtime(log["timestamp"]))
        print(f"  {ts}  {log['action']:<12}  {log['detail'][:40]:<40}  [{log['operator']}]")
    print("-" * 80)


# ==================== Main Menu ====================

def main_menu():
    while True:
        print()
        green("+" + "=" * 55 + "+")
        green("|       VPN 用户订阅管理系统 (Python Edition)         |")
        green("+" + "=" * 55 + "+")
        print( "|  1. 添加用户           9. 导出用户列表             |")
        print( "|  2. 删除用户          10. 套餐配置                 |")
        print( "|  3. 用户列表          11. 协议管理                 |")
        print( "|  4. 用户详情/订阅     12. 订阅服务器               |")
        print( "|  5. 续费/换套餐       13. 发卡平台                 |")
        print( "|  6. 手动设置流量      14. 服务器容量               |")
        print( "|  7. 启用/禁用用户     15. Web 管理面板             |")
        print( "|  8. 批量添加          16. sing-box 管理            |")
        print( "| " + "-" * 53 + " |")
        print( "| 17. 刷新订阅  18. 检查流量  19. 同步 sing-box     |")
        print( "| 20. 库存状态  21. 操作日志                        |")
        print( "|  0. 退出                                          |")
        green("+" + "=" * 55 + "+")

        protos = db.get_config_json("protocols", ["vless-reality"])
        yellow(f"协议: {', '.join(protos)} | 限速: tc+iptables (全设备共享带宽)")
        choice = prompt("请选择 [0-21]: ")

        try:
            actions = {
                "1": action_add_user, "2": action_del_user, "3": show_users,
                "4": action_user_info, "5": action_renew, "6": action_set_traffic,
                "7": action_toggle_user, "8": action_batch_add, "9": action_export,
                "10": action_edit_plan, "11": action_protocol_menu,
                "12": action_sub_server, "13": action_card_platform,
                "14": action_server_capacity, "15": action_admin_dashboard,
                "16": action_singbox_mgmt,
                "17": lambda: (services.generate_all_subs(), green("已刷新")),
                "18": lambda: (services.check_traffic(), green("检查完成"), show_users()),
                "19": lambda: (services.sync_to_singbox(), green("已同步")),
                "20": show_inventory, "21": action_view_logs,
                "0": lambda: sys.exit(0),
            }
            action = actions.get(choice)
            if action:
                action()
            else:
                red("无效选择")
        except KeyboardInterrupt:
            print()
        except Exception as e:
            red(f"错误: {e}")
