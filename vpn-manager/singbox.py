"""sing-box configuration management, clash API, and link generation."""
import json
import re
import subprocess
import base64
import time
import urllib.request
from pathlib import Path
from config import SB_DIR, SB_CONFIG, SB_CONFIG_10, SB_CONFIG_11, SB_BIN, get_singbox_version


# ==================== Config Parser ====================

def strip_json_comments(text: str) -> str:
    """Strip // comments from sing-box JSON, respecting string literals."""
    result = []
    for line in text.splitlines():
        in_string = False
        escape = False
        cut_pos = len(line)
        for i, ch in enumerate(line):
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
            if not in_string and i + 1 < len(line) and line[i:i+2] == '//':
                cut_pos = i
                break
        result.append(line[:cut_pos].rstrip())
    return '\n'.join(result)


def load_sb_config(path: Path) -> dict | None:
    """Load a sing-box config file, stripping comments."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        clean = strip_json_comments(text)
        return json.loads(clean)
    except (json.JSONDecodeError, OSError):
        return None


def save_sb_config(path: Path, data: dict):
    """Write config as formatted JSON."""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ==================== Config Sync ====================

def sync_users(active_users: list[dict]):
    """
    Update sing-box config files with active user list.
    active_users: [{"uuid": "...", "plan_id": int, "bandwidth_mbps": int}, ...]
    """
    uuids = [u["uuid"] for u in active_users]
    if not uuids:
        uuids = ["00000000-0000-0000-0000-000000000000"]

    vless_users = [{"uuid": u, "flow": "xtls-rprx-vision"} for u in uuids]
    vmess_users = [{"uuid": u, "alterId": 0} for u in uuids]
    hy2_users = [{"password": u} for u in uuids]
    tuic_users = [{"uuid": u, "password": u} for u in uuids]
    anytls_users = [{"password": u} for u in uuids]

    user_map = {
        "vless": vless_users,
        "vmess": vmess_users,
        "hysteria2": hy2_users,
        "tuic": tuic_users,
        "anytls": anytls_users,
    }

    for config_path in [SB_CONFIG, SB_CONFIG_10, SB_CONFIG_11]:
        cfg = load_sb_config(config_path)
        if not cfg or "inbounds" not in cfg:
            continue

        for i, ib in enumerate(cfg["inbounds"]):
            itype = ib.get("type", "")
            if itype in user_map:
                cfg["inbounds"][i]["users"] = user_map[itype]

        save_sb_config(config_path, cfg)


def inject_speed_limit_rules(users_by_bw: dict[int, list[str]]):
    """
    Inject per-user speed_limit route rules into sing-box 1.11+ configs.
    users_by_bw: {bandwidth_mbps: [uuid1, uuid2, ...]}
    """
    ver = get_singbox_version()
    if ver.startswith("1.10") or ver == "0.0":
        return False

    for config_path in [SB_CONFIG, SB_CONFIG_11]:
        cfg = load_sb_config(config_path)
        if not cfg or "route" not in cfg:
            continue

        rules = cfg["route"].get("rules", [])
        # Remove existing speed limit rules (identified by speed_limit + auth_user)
        rules = [r for r in rules if not (r.get("speed_limit") and r.get("auth_user"))]

        # Add new speed limit rules at end
        for bw, uuid_list in users_by_bw.items():
            if uuid_list:
                rules.append({
                    "auth_user": uuid_list,
                    "action": "route",
                    "outbound": "direct",
                    "speed_limit": f"{bw} mbps",
                })

        cfg["route"]["rules"] = rules
        save_sb_config(config_path, cfg)

    return True


def remove_speed_limit_rules():
    """Remove all injected speed limit rules."""
    for config_path in [SB_CONFIG, SB_CONFIG_11]:
        cfg = load_sb_config(config_path)
        if not cfg or "route" not in cfg:
            continue
        rules = cfg["route"].get("rules", [])
        cfg["route"]["rules"] = [r for r in rules if not (r.get("speed_limit") and r.get("auth_user"))]
        save_sb_config(config_path, cfg)


def validate_config() -> bool:
    """Validate sing-box config."""
    r = subprocess.run([str(SB_BIN), "check", "-c", str(SB_CONFIG)],
                       capture_output=True, text=True, timeout=10)
    return r.returncode == 0


def restart_service():
    """Restart sing-box service."""
    subprocess.run(["systemctl", "restart", "sing-box"], capture_output=True, timeout=15)


def reload_service():
    """Try reload, fall back to restart."""
    r = subprocess.run(["systemctl", "reload", "sing-box"], capture_output=True, timeout=10)
    if r.returncode != 0:
        restart_service()


# ==================== Clash API ====================

CLASH_API = "http://127.0.0.1:9090"


def get_connections() -> dict | None:
    """Query clash API for active connections."""
    try:
        req = urllib.request.Request(f"{CLASH_API}/connections", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def get_per_uuid_traffic(conns_data: dict) -> dict[str, tuple[int, int]]:
    """
    Parse connections data, group traffic by authenticated user UUID.
    Returns {uuid: (total_upload, total_download)}
    This correctly sums ALL connections from ALL devices for the same user.
    """
    uuid_traffic: dict[str, list[int, int]] = {}
    for conn in conns_data.get("connections", []):
        meta = conn.get("metadata", {})
        user_uuid = meta.get("user", "")
        if not user_uuid:
            continue
        up = conn.get("upload", 0)
        down = conn.get("download", 0)
        if user_uuid in uuid_traffic:
            uuid_traffic[user_uuid][0] += up
            uuid_traffic[user_uuid][1] += down
        else:
            uuid_traffic[user_uuid] = [up, down]
    return {k: tuple(v) for k, v in uuid_traffic.items()}


def get_uuid_to_client_ips(conns_data: dict) -> dict[str, set[str]]:
    """Map each UUID to all their connected client IPs (for tc bandwidth control)."""
    mapping: dict[str, set[str]] = {}
    for conn in conns_data.get("connections", []):
        meta = conn.get("metadata", {})
        user_uuid = meta.get("user", "")
        src_ip = meta.get("sourceIP", "")
        if user_uuid and src_ip:
            mapping.setdefault(user_uuid, set()).add(src_ip)
    return mapping


def get_total_traffic(conns_data: dict) -> tuple[int, int]:
    """Get server total upload/download from connections response."""
    return (conns_data.get("uploadTotal", 0), conns_data.get("downloadTotal", 0))


def close_connection(conn_id: str):
    """Close a specific connection via clash API."""
    try:
        req = urllib.request.Request(f"{CLASH_API}/connections/{conn_id}", method="DELETE")
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


# ==================== Link Generator ====================

def gen_vless_link(uuid: str, tag: str, params: dict) -> str | None:
    port = params.get("vl_port")
    if not port:
        return None
    ip = params.get("server_ip", "")
    sni = params.get("vl_sni", "")
    sid = params.get("vl_sid", "")
    pbk = params.get("public_key", "")
    return (
        f"vless://{uuid}@{ip}:{port}?encryption=none&flow=xtls-rprx-vision"
        f"&security=reality&sni={sni}&fp=chrome&pbk={pbk}&sid={sid}"
        f"&type=tcp&headerType=none#{tag}-vl-reality"
    )


def gen_vmess_link(uuid: str, tag: str, params: dict) -> str | None:
    port = params.get("vm_port")
    if not port:
        return None
    tls = "tls" if params.get("vm_tls") else ""
    sni = params.get("vm_sni", "")
    ip = params.get("server_ipcl", params.get("server_ip", ""))
    add = sni if tls else ip
    obj = {
        "v": "2", "ps": f"{tag}-vm-ws", "add": add, "port": str(port),
        "id": uuid, "aid": "0", "scy": "auto", "net": "ws", "type": "none",
        "host": sni, "path": params.get("vm_path", ""), "tls": tls,
        "sni": sni, "fp": "chrome",
    }
    return "vmess://" + base64.b64encode(json.dumps(obj).encode()).decode()


def gen_vmess_cf_link(uuid: str, tag: str, params: dict) -> str | None:
    """Generate VMess link that goes through Cloudflare CDN."""
    cf_domain = params.get("cf_domain", "")
    vm_path = params.get("vm_path", "")
    if not cf_domain or not vm_path:
        return None
    obj = {
        "v": "2", "ps": f"{tag}-CF备用", "add": cf_domain, "port": "443",
        "id": uuid, "aid": "0", "scy": "auto", "net": "ws", "type": "none",
        "host": cf_domain, "path": vm_path, "tls": "tls",
        "sni": cf_domain, "fp": "chrome",
    }
    return "vmess://" + base64.b64encode(json.dumps(obj).encode()).decode()


def gen_vmess_argo_link(uuid: str, tag: str, params: dict, domain: str) -> str | None:
    if not params.get("vm_port") or not domain:
        return None
    obj = {
        "v": "2", "ps": f"{tag}-vm-argo", "add": domain, "port": "8443",
        "id": uuid, "aid": "0", "scy": "auto", "net": "ws", "type": "none",
        "host": domain, "path": params.get("vm_path", ""), "tls": "tls",
        "sni": domain, "fp": "chrome",
    }
    return "vmess://" + base64.b64encode(json.dumps(obj).encode()).decode()


def gen_hy2_link(uuid: str, tag: str, params: dict) -> str | None:
    port = params.get("hy2_port")
    if not port:
        return None
    addr = params.get("server_ip", "")
    sni = params.get("hy2_sni", "")
    ins = params.get("hy2_insecure", 1)
    return f"hysteria2://{uuid}@{addr}:{port}?security=tls&alpn=h3&insecure={ins}&sni={sni}#{tag}-hy2"


def gen_tuic_link(uuid: str, tag: str, params: dict) -> str | None:
    port = params.get("tuic_port")
    if not port:
        return None
    addr = params.get("server_ip", "")
    sni = params.get("tuic_sni", "")
    ins = params.get("tuic_insecure", 1)
    return (
        f"tuic://{uuid}:{uuid}@{addr}:{port}?congestion_control=bbr"
        f"&udp_relay_mode=native&alpn=h3&sni={sni}&allow_insecure={ins}#{tag}-tuic5"
    )


def gen_anytls_link(uuid: str, tag: str, params: dict) -> str | None:
    port = params.get("anytls_port")
    if not port:
        return None
    addr = params.get("server_ip", "")
    sni = params.get("anytls_sni", "")
    ins = params.get("anytls_insecure", 1)
    return f"anytls://{uuid}@{addr}:{port}?sni={sni}&allowInsecure={ins}#{tag}-anytls"
