"""Global configuration and constants."""
import os
import json
import subprocess
from pathlib import Path

# Paths
MANAGER_DIR = Path("/etc/vpn-manager")
DB_PATH = MANAGER_DIR / "vpn-manager.db"
SUBS_DIR = MANAGER_DIR / "subs"
LOCK_FILE = MANAGER_DIR / ".lock"

SB_DIR = Path("/etc/s-box")
SB_CONFIG = SB_DIR / "sb.json"
SB_CONFIG_10 = SB_DIR / "sb10.json"
SB_CONFIG_11 = SB_DIR / "sb11.json"
SB_BIN = SB_DIR / "sing-box"

# Default plans
DEFAULT_PLANS = [
    # id, name, hours, traffic_gb, bandwidth_mbps, price_yuan, max_connections
    (1, "单日套餐", 24, 10, 20, 2.0, 3),
    (2, "单月订阅", 720, 100, 50, 15.0, 5),
    (3, "单月会员升级版", 720, 200, 100, 25.0, 10),
]

# Default server capacity
DEFAULT_SERVER_BANDWIDTH_MBPS = 2500
DEFAULT_SERVER_MONTHLY_TRAFFIC_TB = 1.0


def get_singbox_version() -> str:
    """Get sing-box version (e.g. '1.11')."""
    try:
        r = subprocess.run([str(SB_BIN), "version"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            if "version" in line.lower():
                ver = line.split()[-1]
                parts = ver.split(".")
                if len(parts) >= 2:
                    return f"{parts[0]}.{parts[1]}"
    except Exception:
        pass
    return "0.0"


def get_default_interface() -> str:
    """Get the default network egress interface."""
    try:
        r = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5)
        parts = r.stdout.split()
        for i, p in enumerate(parts):
            if p == "dev" and i + 1 < len(parts):
                return parts[i + 1]
    except Exception:
        pass
    return "eth0"


def load_server_params() -> dict:
    """Load sing-box server parameters from config files."""
    from singbox import load_sb_config
    params = {}

    params["server_ip"] = _read_file(SB_DIR / "server_ip.log")
    params["server_ipcl"] = _read_file(SB_DIR / "server_ipcl.log")
    params["public_key"] = _read_file(SB_DIR / "public.key")
    params["argo_domain"] = _read_file(SB_DIR / "sbargoym.log")

    cfg = load_sb_config(SB_CONFIG)
    if not cfg:
        return params

    inbounds = cfg.get("inbounds", [])

    # VLESS Reality (inbound 0)
    if len(inbounds) > 0 and inbounds[0].get("type") == "vless":
        ib = inbounds[0]
        params["vl_port"] = ib.get("listen_port")
        tls = ib.get("tls", {})
        params["vl_sni"] = tls.get("server_name", "")
        reality = tls.get("reality", {})
        sid_list = reality.get("short_id", [])
        params["vl_sid"] = sid_list[0] if sid_list else ""

    # VMess WS (inbound 1)
    if len(inbounds) > 1 and inbounds[1].get("type") == "vmess":
        ib = inbounds[1]
        params["vm_port"] = ib.get("listen_port")
        params["vm_tls"] = ib.get("tls", {}).get("enabled", False)
        params["vm_sni"] = ib.get("tls", {}).get("server_name", "")
        transport = ib.get("transport", {})
        params["vm_path"] = transport.get("path", "")

    # Hysteria2 (inbound 2)
    if len(inbounds) > 2 and inbounds[2].get("type") == "hysteria2":
        ib = inbounds[2]
        params["hy2_port"] = ib.get("listen_port")
        key_path = ib.get("tls", {}).get("key_path", "")
        if key_path == "/etc/s-box/private.key":
            params["hy2_sni"] = "www.bing.com"
            params["hy2_insecure"] = 1
        else:
            ym = _read_file(Path("/root/ygkkkca/ca.log"))
            params["hy2_sni"] = ym
            params["hy2_insecure"] = 0

    # TUIC (inbound 3)
    if len(inbounds) > 3 and inbounds[3].get("type") == "tuic":
        ib = inbounds[3]
        params["tuic_port"] = ib.get("listen_port")
        key_path = ib.get("tls", {}).get("key_path", "")
        if key_path == "/etc/s-box/private.key":
            params["tuic_sni"] = "www.bing.com"
            params["tuic_insecure"] = 1
        else:
            ym = _read_file(Path("/root/ygkkkca/ca.log"))
            params["tuic_sni"] = ym
            params["tuic_insecure"] = 0

    # AnyTLS (inbound 4, sb11 only)
    if len(inbounds) > 4 and inbounds[4].get("type") == "anytls":
        ib = inbounds[4]
        params["anytls_port"] = ib.get("listen_port")
        key_path = ib.get("tls", {}).get("key_path", "")
        if key_path == "/etc/s-box/private.key":
            params["anytls_sni"] = "www.bing.com"
            params["anytls_insecure"] = 1
        else:
            ym = _read_file(Path("/root/ygkkkca/ca.log"))
            params["anytls_sni"] = ym
            params["anytls_insecure"] = 0

    return params


def _read_file(path: Path) -> str:
    try:
        return path.read_text().strip()
    except Exception:
        return ""
