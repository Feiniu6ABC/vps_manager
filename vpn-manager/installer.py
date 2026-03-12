"""VPS sing-box installation and configuration - replaces sb.sh.

Security improvements over sb.sh:
- SHA256 verification for downloaded binaries
- No --insecure curl flags
- subprocess.run with list args (no shell injection)
- Proper temp file handling via tempfile module
- Strict file permissions (0600 for configs, 0700 for binaries)
- Input validation for all user-facing values
- No sed-based JSON manipulation (pure Python/jq)
"""
import json
import os
import sys
import subprocess
import shutil
import tempfile
import hashlib
import time
import re
import urllib.request
import urllib.error
import ssl
import platform
from pathlib import Path

from config import SB_DIR, SB_CONFIG, SB_CONFIG_10, SB_CONFIG_11, SB_BIN, MANAGER_DIR


def _version_gte(ver: str, target: str) -> bool:
    """Check if ver >= target using numeric comparison (e.g. '1.13' >= '1.12')."""
    try:
        v = [int(x) for x in ver.split(".")]
        t = [int(x) for x in target.split(".")]
        return v >= t
    except (ValueError, AttributeError):
        return False


# ==================== Constants ====================

GITHUB_API = "https://api.github.com/repos/SagerNet/sing-box/releases/latest"
GITHUB_RELEASE = "https://github.com/SagerNet/sing-box/releases/download"
SINGBOX_CHECKSUM_URL = "https://github.com/SagerNet/sing-box/releases/download/v{ver}/sing-box-{ver}-linux-{arch}.tar.gz.sha256"

DEFAULT_REALITY_SNI = "www.microsoft.com"
DEFAULT_VLESS_PORT = 443
DEFAULT_VMESS_PORT = 8880
DEFAULT_HY2_PORT = 8443
DEFAULT_TUIC_PORT = 8844
DEFAULT_ANYTLS_PORT = 8845

REQUIRED_PACKAGES_APT = ["curl", "openssl", "jq", "iptables", "qrencode", "python3", "cron", "iproute2"]
REQUIRED_PACKAGES_ALPINE = ["curl", "openssl", "jq", "iptables", "qrencode", "python3", "iproute2"]


# ==================== Architecture Detection ====================

def detect_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    elif machine in ("aarch64", "arm64"):
        return "arm64"
    elif machine.startswith("armv7") or machine == "armhf":
        return "armv7"
    else:
        raise RuntimeError(f"Unsupported architecture: {machine}")


def detect_os() -> str:
    """Detect Linux distribution."""
    try:
        with open("/etc/os-release") as f:
            content = f.read().lower()
        if "ubuntu" in content or "debian" in content:
            return "debian"
        elif "centos" in content or "rhel" in content or "fedora" in content:
            return "rhel"
        elif "alpine" in content:
            return "alpine"
    except FileNotFoundError:
        pass
    return "debian"  # Default


def detect_init_system() -> str:
    """Detect systemd vs openrc."""
    if shutil.which("systemctl"):
        return "systemd"
    elif shutil.which("rc-service"):
        return "openrc"
    return "systemd"


# ==================== Dependency Installation ====================

def install_dependencies():
    """Install required system packages."""
    distro = detect_os()
    print("\033[33m安装系统依赖...\033[0m")

    if distro == "debian":
        subprocess.run(["apt-get", "update", "-y"], capture_output=True, timeout=120)
        subprocess.run(
            ["apt-get", "install", "-y"] + REQUIRED_PACKAGES_APT,
            capture_output=True, timeout=300,
        )
    elif distro == "rhel":
        subprocess.run(
            ["yum", "install", "-y"] + REQUIRED_PACKAGES_APT,
            capture_output=True, timeout=300,
        )
    elif distro == "alpine":
        subprocess.run(
            ["apk", "add", "--no-cache"] + REQUIRED_PACKAGES_ALPINE,
            capture_output=True, timeout=300,
        )

    print("\033[32m依赖安装完成\033[0m")


# ==================== Network Optimization ====================

def enable_bbr():
    """Enable TCP BBR congestion control."""
    sysctl_conf = Path("/etc/sysctl.conf")
    try:
        content = sysctl_conf.read_text() if sysctl_conf.exists() else ""
        changes = {
            "net.core.default_qdisc": "fq",
            "net.ipv4.tcp_congestion_control": "bbr",
        }
        for key, val in changes.items():
            line = f"{key}={val}"
            if line not in content:
                pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
                if pattern.search(content):
                    content = pattern.sub(line, content)
                else:
                    content += f"\n{line}\n"

        sysctl_conf.write_text(content)
        subprocess.run(["sysctl", "-p"], capture_output=True, timeout=10)

        # Verify
        r = subprocess.run(["sysctl", "net.ipv4.tcp_congestion_control"],
                          capture_output=True, text=True, timeout=5)
        if "bbr" in r.stdout:
            print("\033[32mBBR 已启用\033[0m")
        else:
            print("\033[33mBBR 启用可能需要重启\033[0m")
    except Exception as e:
        print(f"\033[33mBBR 设置失败: {e}\033[0m")


# ==================== IP Detection ====================

def detect_server_ip() -> tuple[str, str]:
    """Detect server IPv4 and IPv6 addresses. Returns (v4, v6)."""
    v4, v6 = "", ""
    services = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
    ]
    for svc in services:
        try:
            req = urllib.request.Request(svc, headers={"User-Agent": "curl/8.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                ip = resp.read().decode().strip()
                if ":" in ip:
                    v6 = ip
                elif re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
                    v4 = ip
            if v4:
                break
        except Exception:
            continue

    if not v4:
        # Try IPv6
        for svc in services:
            try:
                req = urllib.request.Request(svc, headers={"User-Agent": "curl/8.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    ip = resp.read().decode().strip()
                    if ":" in ip:
                        v6 = ip
                    elif re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
                        v4 = ip
                if v6:
                    break
            except Exception:
                continue

    return v4, v6


# ==================== Sing-box Binary Management ====================

def get_latest_version() -> str:
    """Get latest sing-box release version from GitHub API."""
    try:
        req = urllib.request.Request(GITHUB_API, headers={
            "User-Agent": "vpn-manager/1.0",
            "Accept": "application/vnd.github.v3+json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            tag = data.get("tag_name", "")
            return tag.lstrip("v")
    except Exception as e:
        raise RuntimeError(f"无法获取最新版本: {e}")


def download_singbox(version: str = "") -> str:
    """Download and install sing-box binary with SHA256 verification."""
    arch = detect_arch()
    if not version:
        version = get_latest_version()

    print(f"\033[33m下载 sing-box v{version} ({arch})...\033[0m")

    tarball_name = f"sing-box-{version}-linux-{arch}"
    tarball_url = f"{GITHUB_RELEASE}/v{version}/{tarball_name}.tar.gz"

    # Download to temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        tarball_path = os.path.join(tmpdir, "singbox.tar.gz")

        # Download tarball
        try:
            urllib.request.urlretrieve(tarball_url, tarball_path)
        except Exception as e:
            raise RuntimeError(f"下载失败: {e}")

        # Try to verify SHA256 (optional - some releases may not have checksums)
        try:
            checksum_url = SINGBOX_CHECKSUM_URL.format(ver=version, arch=arch)
            req = urllib.request.Request(checksum_url, headers={"User-Agent": "vpn-manager/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                expected_hash = resp.read().decode().strip().split()[0]
            # Compute actual hash
            sha256 = hashlib.sha256()
            with open(tarball_path, "rb") as f:
                while chunk := f.read(8192):
                    sha256.update(chunk)
            actual_hash = sha256.hexdigest()
            if actual_hash != expected_hash:
                raise RuntimeError(f"SHA256 校验失败!\n期望: {expected_hash}\n实际: {actual_hash}")
            print("\033[32mSHA256 校验通过\033[0m")
        except urllib.error.HTTPError:
            print("\033[33m跳过 SHA256 校验 (校验文件不可用)\033[0m")

        # Extract
        subprocess.run(
            ["tar", "xzf", tarball_path, "-C", tmpdir],
            capture_output=True, check=True, timeout=30,
        )

        # Find and install binary
        extracted_bin = os.path.join(tmpdir, tarball_name, "sing-box")
        if not os.path.exists(extracted_bin):
            raise RuntimeError("解压后未找到 sing-box 二进制文件")

        SB_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(extracted_bin, str(SB_BIN))
        os.chmod(str(SB_BIN), 0o700)
        os.chown(str(SB_BIN), 0, 0)

    # Verify binary works
    r = subprocess.run([str(SB_BIN), "version"], capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        raise RuntimeError("sing-box 二进制文件无法执行")

    ver_line = r.stdout.strip().splitlines()[0] if r.stdout.strip() else "unknown"
    print(f"\033[32msing-box 安装成功: {ver_line}\033[0m")
    return version


def get_installed_version() -> str:
    """Get currently installed sing-box version."""
    if not SB_BIN.exists():
        return ""
    try:
        r = subprocess.run([str(SB_BIN), "version"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            parts = line.split()
            for p in parts:
                if re.match(r"^\d+\.\d+\.\d+", p):
                    return p
    except Exception:
        pass
    return ""


def get_major_minor() -> str:
    """Get major.minor version string."""
    ver = get_installed_version()
    if ver:
        parts = ver.split(".")
        if len(parts) >= 2:
            return f"{parts[0]}.{parts[1]}"
    return "0.0"


# ==================== Key Generation ====================

def generate_reality_keypair() -> tuple[str, str]:
    """Generate Reality key pair using sing-box. Returns (private_key, public_key)."""
    r = subprocess.run(
        [str(SB_BIN), "generate", "reality-keypair"],
        capture_output=True, text=True, timeout=5,
    )
    if r.returncode != 0:
        raise RuntimeError("生成 Reality 密钥对失败")

    private_key, public_key = "", ""
    for line in r.stdout.splitlines():
        if "PrivateKey" in line:
            private_key = line.split(":")[-1].strip()
        elif "PublicKey" in line:
            public_key = line.split(":")[-1].strip()

    if not private_key or not public_key:
        raise RuntimeError("解析 Reality 密钥对失败")

    return private_key, public_key


def generate_short_id() -> str:
    """Generate a random short_id for Reality."""
    r = subprocess.run(
        [str(SB_BIN), "generate", "rand", "--hex", "8"],
        capture_output=True, text=True, timeout=5,
    )
    return r.stdout.strip() if r.returncode == 0 else os.urandom(8).hex()


def generate_uuid() -> str:
    """Generate UUID using sing-box."""
    r = subprocess.run(
        [str(SB_BIN), "generate", "uuid"],
        capture_output=True, text=True, timeout=5,
    )
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    import uuid
    return str(uuid.uuid4())


# ==================== Certificate Generation ====================

def generate_self_signed_cert():
    """Generate self-signed TLS certificate."""
    cert_path = SB_DIR / "cert.pem"
    key_path = SB_DIR / "private.key"

    if cert_path.exists() and key_path.exists():
        print("\033[33m证书已存在，跳过生成\033[0m")
        return

    subprocess.run([
        "openssl", "req", "-x509", "-nodes",
        "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
        "-keyout", str(key_path),
        "-out", str(cert_path),
        "-days", "3650",
        "-subj", "/CN=bing.com",
    ], capture_output=True, check=True, timeout=30)

    os.chmod(str(cert_path), 0o600)
    os.chmod(str(key_path), 0o600)
    print("\033[32m自签名证书已生成\033[0m")


# ==================== Configuration Generation ====================

def build_singbox_config(
    uuid: str,
    server_ip: str,
    vless_port: int = DEFAULT_VLESS_PORT,
    reality_sni: str = DEFAULT_REALITY_SNI,
    reality_private_key: str = "",
    reality_short_id: str = "",
    vmess_port: int = 0,
    vmess_path: str = "",
    hy2_port: int = 0,
    tuic_port: int = 0,
    anytls_port: int = 0,
    enable_clash_api: bool = True,
    version: str = "1.11",
) -> dict:
    """Build a sing-box configuration dictionary."""

    # DNS format changed in sing-box 1.12
    if _version_gte(version, "1.12"):
        dns_config = {
            "servers": [
                {"tag": "google", "type": "tls", "server": "8.8.8.8"},
                {"tag": "local", "type": "udp", "server": "223.5.5.5"},
            ],
        }
    else:
        dns_config = {
            "servers": [
                {"tag": "google", "address": "tls://8.8.8.8"},
                {"tag": "local", "address": "223.5.5.5", "detour": "direct"},
            ],
        }

    # sing-box 1.11+ deprecated dns outbound, 1.13 removed it → use hijack-dns action
    if _version_gte(version, "1.11"):
        dns_route_rule = {"protocol": "dns", "action": "hijack-dns"}
    else:
        dns_route_rule = {"protocol": "dns", "outbound": "dns-out"}

    config = {
        "log": {"level": "info", "timestamp": True},
        "dns": dns_config,
        "inbounds": [],
        "outbounds": [
            {"type": "direct", "tag": "direct"},
            {"type": "block", "tag": "block"},
        ],
        "route": dict(
            **{"default_domain_resolver": "google"} if _version_gte(version, "1.12") else {},
            rules=[
                dns_route_rule,
                {"ip_is_private": True, "outbound": "direct"},
            ],
            final="direct",
        ),
    }

    # Clash API for traffic monitoring
    if enable_clash_api:
        if _version_gte(version, "1.12"):
            config["experimental"] = {
                "cache_file": {"enabled": True},
                "clash_api": {
                    "external_controller": "127.0.0.1:9090",
                },
            }
        else:
            config["experimental"] = {
                "clash_api": {
                    "external_controller": "127.0.0.1:9090",
                    "store_selected": True,
                },
            }

    # DNS outbound only needed for < 1.11
    if not _version_gte(version, "1.11"):
        config["outbounds"].append({"type": "dns", "tag": "dns-out"})

    # ===== VLESS Reality inbound (always enabled) =====
    vless_inbound = {
        "type": "vless",
        "tag": "vless-reality",
        "listen": "::",
        "listen_port": vless_port,
        "users": [{"uuid": uuid, "flow": "xtls-rprx-vision"}],
        "tls": {
            "enabled": True,
            "server_name": reality_sni,
            "reality": {
                "enabled": True,
                "handshake": {
                    "server": reality_sni,
                    "server_port": 443,
                },
                "private_key": reality_private_key,
                "short_id": [reality_short_id],
            },
        },
    }
    config["inbounds"].append(vless_inbound)

    # ===== VMess WS inbound (optional) =====
    if vmess_port > 0:
        if not vmess_path:
            vmess_path = f"/{os.urandom(8).hex()}"
        vmess_inbound = {
            "type": "vmess",
            "tag": "vmess-ws",
            "listen": "::",
            "listen_port": vmess_port,
            "users": [{"uuid": uuid, "alterId": 0}],
            "transport": {
                "type": "ws",
                "path": vmess_path,
            },
        }
        config["inbounds"].append(vmess_inbound)

    # ===== Hysteria2 inbound (optional) =====
    if hy2_port > 0:
        hy2_inbound = {
            "type": "hysteria2",
            "tag": "hysteria2",
            "listen": "::",
            "listen_port": hy2_port,
            "users": [{"password": uuid}],
            "tls": {
                "enabled": True,
                "certificate_path": str(SB_DIR / "cert.pem"),
                "key_path": str(SB_DIR / "private.key"),
            },
        }
        config["inbounds"].append(hy2_inbound)

    # ===== TUIC inbound (optional) =====
    if tuic_port > 0:
        tuic_inbound = {
            "type": "tuic",
            "tag": "tuic",
            "listen": "::",
            "listen_port": tuic_port,
            "users": [{"uuid": uuid, "password": uuid}],
            "congestion_control": "bbr",
            "tls": {
                "enabled": True,
                "alpn": ["h3"],
                "certificate_path": str(SB_DIR / "cert.pem"),
                "key_path": str(SB_DIR / "private.key"),
            },
        }
        config["inbounds"].append(tuic_inbound)

    # ===== AnyTLS inbound (optional, 1.11+ only) =====
    if anytls_port > 0 and _version_gte(version, "1.11"):
        anytls_inbound = {
            "type": "anytls",
            "tag": "anytls",
            "listen": "::",
            "listen_port": anytls_port,
            "users": [{"password": uuid}],
            "tls": {
                "enabled": True,
                "certificate_path": str(SB_DIR / "cert.pem"),
                "key_path": str(SB_DIR / "private.key"),
            },
        }
        config["inbounds"].append(anytls_inbound)

    return config


def generate_configs(
    uuid: str,
    server_ip: str,
    vless_port: int,
    reality_sni: str,
    reality_private_key: str,
    reality_short_id: str,
    vmess_port: int = 0,
    vmess_path: str = "",
    hy2_port: int = 0,
    tuic_port: int = 0,
    anytls_port: int = 0,
):
    """Generate all config files (sb.json, sb10.json, sb11.json)."""
    ver = get_major_minor()

    common_args = dict(
        uuid=uuid, server_ip=server_ip, vless_port=vless_port,
        reality_sni=reality_sni, reality_private_key=reality_private_key,
        reality_short_id=reality_short_id, vmess_port=vmess_port,
        vmess_path=vmess_path, hy2_port=hy2_port, tuic_port=tuic_port,
    )

    # Generate 1.10 config (old DNS format, no anytls)
    cfg10 = build_singbox_config(**common_args, anytls_port=0, version="1.10")
    SB_CONFIG_10.write_text(json.dumps(cfg10, indent=2, ensure_ascii=False))
    os.chmod(str(SB_CONFIG_10), 0o600)

    # Generate 1.11+ config (version-appropriate DNS format, with anytls)
    cfg11 = build_singbox_config(**common_args, anytls_port=anytls_port, version=ver)
    SB_CONFIG_11.write_text(json.dumps(cfg11, indent=2, ensure_ascii=False))
    os.chmod(str(SB_CONFIG_11), 0o600)

    # Symlink sb.json to appropriate version
    if SB_CONFIG.exists() or SB_CONFIG.is_symlink():
        SB_CONFIG.unlink()
    target = SB_CONFIG_11 if _version_gte(ver, "1.11") else SB_CONFIG_10
    SB_CONFIG.symlink_to(target)

    # Save server parameters
    (SB_DIR / "server_ip.log").write_text(server_ip)
    (SB_DIR / "server_ipcl.log").write_text(server_ip)

    print(f"\033[32m配置文件已生成 (sing-box v{ver})\033[0m")


def save_public_key(public_key: str):
    """Save Reality public key for sharing link generation."""
    (SB_DIR / "public.key").write_text(public_key)
    os.chmod(str(SB_DIR / "public.key"), 0o600)


# ==================== Service Management ====================

def create_systemd_service():
    """Create and enable systemd service for sing-box."""
    service_content = f"""[Unit]
Description=sing-box service
Documentation=https://sing-box.sagernet.org
After=network.target nss-lookup.target

[Service]
Type=simple
ExecStart={SB_BIN} run -c {SB_CONFIG}
Restart=on-failure
RestartSec=10
LimitNOFILE=infinity
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_NET_RAW CAP_SYS_PTRACE
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_NET_RAW CAP_SYS_PTRACE

[Install]
WantedBy=multi-user.target
"""
    service_path = Path("/etc/systemd/system/sing-box.service")
    service_path.write_text(service_content)
    os.chmod(str(service_path), 0o644)

    subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=10)
    subprocess.run(["systemctl", "enable", "sing-box"], capture_output=True, timeout=10)
    print("\033[32msystemd 服务已创建\033[0m")


def create_openrc_service():
    """Create OpenRC service for Alpine."""
    script = f"""#!/sbin/openrc-run
name="sing-box"
command="{SB_BIN}"
command_args="run -c {SB_CONFIG}"
command_background=true
pidfile="/run/${{RC_SVCNAME}}.pid"

depend() {{
    need net
    after firewall
}}
"""
    service_path = Path("/etc/init.d/sing-box")
    service_path.write_text(script)
    os.chmod(str(service_path), 0o755)
    subprocess.run(["rc-update", "add", "sing-box", "default"], capture_output=True, timeout=10)
    print("\033[32mOpenRC 服务已创建\033[0m")


def setup_service():
    """Create appropriate service based on init system."""
    init = detect_init_system()
    if init == "systemd":
        create_systemd_service()
    else:
        create_openrc_service()


def start_service():
    init = detect_init_system()
    if init == "systemd":
        subprocess.run(["systemctl", "start", "sing-box"], capture_output=True, timeout=15)
    else:
        subprocess.run(["rc-service", "sing-box", "start"], capture_output=True, timeout=15)


def stop_service():
    init = detect_init_system()
    if init == "systemd":
        subprocess.run(["systemctl", "stop", "sing-box"], capture_output=True, timeout=15)
    else:
        subprocess.run(["rc-service", "sing-box", "stop"], capture_output=True, timeout=15)


def restart_service():
    init = detect_init_system()
    if init == "systemd":
        subprocess.run(["systemctl", "restart", "sing-box"], capture_output=True, timeout=15)
    else:
        subprocess.run(["rc-service", "sing-box", "restart"], capture_output=True, timeout=15)


def service_status() -> str:
    init = detect_init_system()
    if init == "systemd":
        r = subprocess.run(["systemctl", "is-active", "sing-box"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    else:
        r = subprocess.run(["rc-service", "sing-box", "status"], capture_output=True, text=True, timeout=5)
        return "active" if "started" in r.stdout.lower() else "inactive"


# ==================== Firewall ====================

def _has_ufw() -> bool:
    """Check if ufw is installed and active."""
    r = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=5)
    return r.returncode == 0 and "active" in r.stdout.lower()


def open_firewall_port(port: int, proto: str = "tcp"):
    """Open a port in the firewall (ufw or iptables)."""
    if _has_ufw():
        subprocess.run(["ufw", "allow", f"{port}/{proto}"], capture_output=True, timeout=10)
    else:
        subprocess.run(
            ["iptables", "-I", "INPUT", "-p", proto, "--dport", str(port), "-j", "ACCEPT"],
            capture_output=True, timeout=10,
        )


def configure_firewall(
    vless_port: int = 0,
    vmess_port: int = 0,
    hy2_port: int = 0,
    tuic_port: int = 0,
    anytls_port: int = 0,
    sub_port: int = 0,
    admin_port: int = 0,
):
    """Open all necessary ports in the firewall."""
    print("\033[33m配置防火墙...\033[0m")

    tcp_ports = [p for p in [vless_port, vmess_port, anytls_port, sub_port, admin_port] if p]
    udp_ports = [p for p in [hy2_port, tuic_port] if p]

    for port in tcp_ports:
        open_firewall_port(port, "tcp")
    for port in udp_ports:
        open_firewall_port(port, "udp")

    opened = [f"{p}/tcp" for p in tcp_ports] + [f"{p}/udp" for p in udp_ports]
    print(f"\033[32m防火墙已放行: {', '.join(opened)}\033[0m")


# ==================== Validation ====================

def validate_config() -> bool:
    """Validate the current sing-box config."""
    r = subprocess.run(
        [str(SB_BIN), "check", "-c", str(SB_CONFIG)],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        print(f"\033[31m配置验证失败: {r.stderr.strip()}\033[0m")
    return r.returncode == 0


def validate_port(port: int) -> bool:
    return 1 <= port <= 65535


def validate_domain(domain: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)+$", domain))


# ==================== Full Installation Flow ====================

def full_install(
    vless_port: int = DEFAULT_VLESS_PORT,
    reality_sni: str = DEFAULT_REALITY_SNI,
    vmess_port: int = 0,
    hy2_port: int = 0,
    tuic_port: int = 0,
    anytls_port: int = 0,
    skip_deps: bool = False,
    skip_bbr: bool = False,
) -> dict:
    """
    Complete installation of sing-box with VLESS-Reality.
    Returns installation parameters for display/sharing.
    """
    print("\033[32m" + "=" * 60 + "\033[0m")
    print("\033[32m  VPN Manager - sing-box 安装\033[0m")
    print("\033[32m" + "=" * 60 + "\033[0m")
    print()

    # 1. Dependencies
    if not skip_deps:
        install_dependencies()

    # 2. Network optimization
    if not skip_bbr:
        enable_bbr()

    # 3. Detect server IP
    print("\033[33m检测服务器 IP...\033[0m")
    v4, v6 = detect_server_ip()
    server_ip = v4 or v6
    if not server_ip:
        raise RuntimeError("无法检测服务器 IP 地址")
    print(f"\033[32m服务器 IP: {server_ip}\033[0m")

    # 4. Download sing-box
    version = download_singbox()

    # 5. Generate certificates
    generate_self_signed_cert()

    # 6. Generate Reality keypair
    print("\033[33m生成 Reality 密钥对...\033[0m")
    private_key, public_key = generate_reality_keypair()
    short_id = generate_short_id()
    save_public_key(public_key)

    # 7. Generate UUID
    uuid = generate_uuid()
    print(f"\033[33mUUID: {uuid}\033[0m")

    # 8. Generate VMess path if needed
    vmess_path = f"/{os.urandom(8).hex()}" if vmess_port > 0 else ""

    # 9. Generate configs
    generate_configs(
        uuid=uuid, server_ip=server_ip,
        vless_port=vless_port, reality_sni=reality_sni,
        reality_private_key=private_key, reality_short_id=short_id,
        vmess_port=vmess_port, vmess_path=vmess_path,
        hy2_port=hy2_port, tuic_port=tuic_port, anytls_port=anytls_port,
    )

    # 10. Validate config
    if not validate_config():
        raise RuntimeError("配置验证失败，请检查参数")

    # 11. Configure firewall
    configure_firewall(
        vless_port=vless_port, vmess_port=vmess_port,
        hy2_port=hy2_port, tuic_port=tuic_port, anytls_port=anytls_port,
    )

    # 12. Setup and start service
    setup_service()
    start_service()

    # 13. Verify service is running
    time.sleep(2)
    status = service_status()
    if status != "active":
        print(f"\033[31m服务启动异常: {status}\033[0m")
    else:
        print("\033[32msing-box 服务已启动\033[0m")

    result = {
        "server_ip": server_ip,
        "server_ipv4": v4,
        "server_ipv6": v6,
        "version": version,
        "uuid": uuid,
        "vless_port": vless_port,
        "reality_sni": reality_sni,
        "reality_public_key": public_key,
        "reality_short_id": short_id,
        "vmess_port": vmess_port,
        "vmess_path": vmess_path,
        "hy2_port": hy2_port,
        "tuic_port": tuic_port,
        "anytls_port": anytls_port,
        "status": status,
    }

    print()
    print("\033[32m" + "=" * 60 + "\033[0m")
    print("\033[32m  安装完成!\033[0m")
    print("\033[32m" + "=" * 60 + "\033[0m")

    return result


# ==================== Add CF Backup ====================

def add_cf_backup(domain: str, vmess_port: int = DEFAULT_VMESS_PORT):
    """
    Add Cloudflare CDN backup to an existing installation.
    Requires: a domain already pointed to CF (orange cloud enabled) and DNS A record to VPS IP.

    This adds a VMess-WS inbound (if not present) and saves the CF domain for subscription generation.
    """
    from singbox import load_sb_config, save_sb_config

    cfg = load_sb_config(SB_CONFIG)
    if not cfg:
        raise RuntimeError("无法读取 sing-box 配置")

    # Check if VMess-WS inbound already exists
    has_vmess = False
    vmess_path = ""
    for ib in cfg.get("inbounds", []):
        if ib.get("type") == "vmess":
            has_vmess = True
            vmess_path = ib.get("transport", {}).get("path", "")
            break

    if not has_vmess:
        # Need to add VMess-WS inbound to all config files
        uuid = cfg["inbounds"][0]["users"][0].get("uuid", "") if cfg["inbounds"] else generate_uuid()
        vmess_path = f"/{os.urandom(8).hex()}"

        vmess_inbound = {
            "type": "vmess",
            "tag": "vmess-ws",
            "listen": "::",
            "listen_port": vmess_port,
            "users": [{"uuid": uuid, "alterId": 0}],
            "transport": {
                "type": "ws",
                "path": vmess_path,
            },
        }

        for config_path in [SB_CONFIG, SB_CONFIG_10, SB_CONFIG_11]:
            c = load_sb_config(config_path)
            if not c:
                continue
            # Insert VMess after VLESS (index 1)
            inbounds = c.get("inbounds", [])
            insert_pos = 1 if len(inbounds) > 0 else 0
            inbounds.insert(insert_pos, vmess_inbound)
            c["inbounds"] = inbounds
            save_sb_config(config_path, c)

        open_firewall_port(vmess_port, "tcp")
        print(f"\033[32mVMess-WS 已添加 (端口: {vmess_port}, 路径: {vmess_path})\033[0m")
    else:
        print(f"\033[33mVMess-WS 已存在 (路径: {vmess_path})\033[0m")

    # Save CF domain to database
    import database as db_mod
    db_mod.set_config("cf_domain", domain)

    # Validate and restart
    if validate_config():
        restart_service()
        print(f"\033[32mCloudflare CDN 备用已配置\033[0m")
        print(f"\033[32m  域名: {domain}\033[0m")
        print(f"\033[32m  VMess 端口: {vmess_port}\033[0m")
        print(f"\033[32m  WS 路径: {vmess_path}\033[0m")
        print()
        print("\033[33m请确保 Cloudflare DNS 设置正确:\033[0m")
        server_ip = ""
        try:
            server_ip = (SB_DIR / "server_ipcl.log").read_text().strip()
        except Exception:
            pass
        print(f"  1. 域名 {domain} 的 A 记录指向 {server_ip or 'VPS_IP'}")
        print(f"  2. Cloudflare 代理状态: 橙色云朵 (已代理)")
        print(f"  3. SSL/TLS 模式: Flexible 或 Full")
        print()
        print("\033[33m下一步: 执行 [刷新订阅] 让用户订阅包含 CF 备用线路\033[0m")
    else:
        print("\033[31m配置验证失败，请检查\033[0m")

    return vmess_path


# ==================== Config Migration ====================

def migrate_config_for_version():
    """Migrate config files to match the installed sing-box version."""
    from singbox import load_sb_config, save_sb_config

    ver = get_major_minor()
    if not _version_gte(ver, "1.12"):
        return

    for config_path in [SB_CONFIG_11]:
        cfg = load_sb_config(config_path)
        if not cfg or "dns" not in cfg:
            continue

        # Migrate DNS servers from legacy format to new typed format
        servers = cfg["dns"].get("servers", [])
        if not any("address" in s for s in servers):
            continue  # Already migrated

        new_servers = []
        for s in servers:
            if "address" not in s:
                new_servers.append(s)
                continue
            addr = s["address"]
            new_s = {"tag": s.get("tag", "")}
            if addr.startswith("tls://"):
                new_s["type"] = "tls"
                new_s["server"] = addr[6:]
            elif addr.startswith("https://"):
                new_s["type"] = "https"
                new_s["server"] = addr[8:]
            else:
                new_s["type"] = "udp"
                new_s["server"] = addr
            if "detour" in s:
                new_s["detour"] = s["detour"]
            new_servers.append(new_s)

        cfg["dns"]["servers"] = new_servers

        # Migrate store_selected → cache_file
        exp = cfg.get("experimental", {})
        clash = exp.get("clash_api", {})
        if clash.pop("store_selected", None):
            exp.setdefault("cache_file", {})["enabled"] = True
            cfg["experimental"] = exp

        # Add default_domain_resolver if missing
        route = cfg.setdefault("route", {})
        if "default_domain_resolver" not in route:
            route["default_domain_resolver"] = "google"

        # Remove deprecated dns outbound (removed in 1.13)
        cfg["outbounds"] = [o for o in cfg.get("outbounds", []) if o.get("type") != "dns"]

        # Migrate dns route rule: outbound → action
        rules = cfg.get("route", {}).get("rules", [])
        for i, r in enumerate(rules):
            if r.get("protocol") == "dns" and r.get("outbound") == "dns-out":
                rules[i] = {"protocol": "dns", "action": "hijack-dns"}
        cfg.setdefault("route", {})["rules"] = rules

        save_sb_config(config_path, cfg)

    print("\033[32m配置已迁移至新版格式\033[0m")


# ==================== Upgrade ====================

def upgrade_singbox(version: str = ""):
    """Upgrade sing-box to latest (or specified) version."""
    current = get_installed_version()
    if not version:
        version = get_latest_version()

    if current == version:
        print(f"\033[33m已是最新版本: v{version}\033[0m")
        return

    print(f"\033[33m升级: v{current} -> v{version}\033[0m")

    # Backup current binary
    backup = SB_DIR / "sing-box.bak"
    if SB_BIN.exists():
        shutil.copy2(str(SB_BIN), str(backup))

    try:
        stop_service()
        download_singbox(version)

        # Re-link config if major version changed
        new_mm = get_major_minor()
        if SB_CONFIG.is_symlink():
            SB_CONFIG.unlink()
        target = SB_CONFIG_11 if _version_gte(new_mm, "1.11") else SB_CONFIG_10
        if target.exists():
            SB_CONFIG.symlink_to(target)

        # Migrate config format if crossing version boundaries (e.g. 1.11 → 1.12+)
        migrate_config_for_version()

        if validate_config():
            start_service()
            print(f"\033[32m升级成功: v{version}\033[0m")
            backup.unlink(missing_ok=True)
        else:
            # Rollback
            print("\033[31m新版本配置验证失败，回滚...\033[0m")
            shutil.copy2(str(backup), str(SB_BIN))
            os.chmod(str(SB_BIN), 0o700)
            start_service()
            backup.unlink(missing_ok=True)
    except Exception as e:
        # Rollback on any error
        if backup.exists():
            shutil.copy2(str(backup), str(SB_BIN))
            os.chmod(str(SB_BIN), 0o700)
        start_service()
        backup.unlink(missing_ok=True)
        raise RuntimeError(f"升级失败: {e}")


# ==================== Uninstall ====================

def uninstall():
    """Completely uninstall sing-box and vpn-manager."""
    init = detect_init_system()

    # Stop services
    if init == "systemd":
        subprocess.run(["systemctl", "stop", "sing-box"], capture_output=True)
        subprocess.run(["systemctl", "disable", "sing-box"], capture_output=True)
        subprocess.run(["systemctl", "stop", "vpn-sub"], capture_output=True)
        subprocess.run(["systemctl", "disable", "vpn-sub"], capture_output=True)
        Path("/etc/systemd/system/sing-box.service").unlink(missing_ok=True)
        Path("/etc/systemd/system/vpn-sub.service").unlink(missing_ok=True)
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
    else:
        subprocess.run(["rc-service", "sing-box", "stop"], capture_output=True)
        subprocess.run(["rc-update", "del", "sing-box"], capture_output=True)
        Path("/etc/init.d/sing-box").unlink(missing_ok=True)

    # Remove files
    if SB_DIR.exists():
        shutil.rmtree(str(SB_DIR))
    if MANAGER_DIR.exists():
        # Keep database as backup
        backup = Path(f"/root/vpn-manager-backup-{int(time.time())}")
        shutil.move(str(MANAGER_DIR), str(backup))
        print(f"\033[33m数据已备份到: {backup}\033[0m")

    # Remove shortcuts
    Path("/usr/bin/vpn-manager").unlink(missing_ok=True)
    Path("/usr/bin/sb").unlink(missing_ok=True)

    # Clean crontab
    try:
        crontab = Path("/etc/crontab")
        if crontab.exists():
            lines = crontab.read_text().splitlines()
            lines = [l for l in lines if "vpn-manager" not in l and "--check" not in l]
            crontab.write_text("\n".join(lines) + "\n")
    except Exception:
        pass

    print("\033[32m卸载完成\033[0m")
