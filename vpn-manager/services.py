"""Business logic: user management, traffic, bandwidth, subscriptions, inventory."""
import time
import json
import base64
import subprocess
import secrets
import os
from pathlib import Path

import database as db
import singbox
from config import (
    SB_DIR, SB_BIN, SUBS_DIR, MANAGER_DIR,
    load_server_params, get_singbox_version, get_default_interface,
)
from utils import gen_uuid, gen_token, gb_to_bytes, bytes_to_gb, file_lock


# ==================== Operation Logging ====================

def log_operation(action: str, detail: str = "", operator: str = "system"):
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO operation_logs (timestamp, action, detail, operator) VALUES (?,?,?,?)",
            (int(time.time()), action, detail, operator),
        )


def get_recent_logs(limit: int = 50) -> list[dict]:
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM operation_logs ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ==================== Plan Service ====================

def list_plans() -> list[dict]:
    with db.get_db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM plans ORDER BY id").fetchall()]


def get_plan(plan_id: int) -> dict | None:
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        return dict(row) if row else None


def update_plan(plan_id: int, **kwargs):
    with db.get_db() as conn:
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values()) + [plan_id]
        conn.execute(f"UPDATE plans SET {sets} WHERE id=?", vals)
    log_operation("修改套餐", f"套餐{plan_id}: {kwargs}", "admin")


# ==================== User Service ====================

def add_user(plan_id: int, remark: str = "", source: str = "manual") -> dict:
    plan = get_plan(plan_id)
    if not plan:
        raise ValueError(f"Invalid plan_id: {plan_id}")

    uuid = gen_uuid()
    token = gen_token()
    now = int(time.time())
    user_id = f"u_{secrets.token_hex(4)}"
    if not remark:
        remark = f"用户{time.strftime('%m%d%H%M')}"
    expires = now + plan["duration_hours"] * 3600
    limit = gb_to_bytes(plan["traffic_gb"])

    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO users VALUES (?,?,?,?,?,?,?,?,0,0,0,'active')",
            (user_id, uuid, token, plan_id, remark, now, expires, limit),
        )
        # Record sale
        conn.execute(
            "INSERT INTO sales (user_id, plan_id, plan_name, price, source, created_at) VALUES (?,?,?,?,?,?)",
            (user_id, plan_id, plan["name"], plan.get("price", 0), source, now),
        )

    sync_to_singbox()
    generate_user_sub(user_id)
    log_operation("添加用户", f"{user_id} 套餐:{plan['name']} 来源:{source}", "admin")
    return {
        "id": user_id, "uuid": uuid, "token": token, "plan_id": plan_id,
        "remark": remark, "expires_at": expires, "traffic_gb": plan["traffic_gb"],
        "bandwidth_mbps": plan["bandwidth_mbps"], "price": plan.get("price", 0),
    }


def delete_user(user_id: str):
    with db.get_db() as conn:
        row = conn.execute("SELECT token, remark FROM users WHERE id=?", (user_id,)).fetchone()
        if row:
            (SUBS_DIR / f"{row['token']}.txt").unlink(missing_ok=True)
            log_operation("删除用户", f"{user_id} ({row['remark']})", "admin")
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.execute("DELETE FROM traffic_snapshots WHERE uuid IN (SELECT uuid FROM users WHERE id=?)", (user_id,))
    sync_to_singbox()


def list_users() -> list[dict]:
    with db.get_db() as conn:
        rows = conn.execute("""
            SELECT u.*, p.name as plan_name, p.bandwidth_mbps, p.max_connections
            FROM users u LEFT JOIN plans p ON u.plan_id = p.id
            ORDER BY u.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_user(user_id: str) -> dict | None:
    with db.get_db() as conn:
        row = conn.execute("""
            SELECT u.*, p.name as plan_name, p.bandwidth_mbps, p.traffic_gb, p.max_connections
            FROM users u LEFT JOIN plans p ON u.plan_id = p.id
            WHERE u.id=?
        """, (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_token(token: str) -> dict | None:
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE token=?", (token,)).fetchone()
        return dict(row) if row else None


def renew_user(user_id: str, plan_id: int):
    plan = get_plan(plan_id)
    if not plan:
        raise ValueError("Invalid plan")
    now = int(time.time())
    expires = now + plan["duration_hours"] * 3600
    limit = gb_to_bytes(plan["traffic_gb"])
    with db.get_db() as conn:
        conn.execute("""
            UPDATE users SET plan_id=?, expires_at=?, traffic_limit_bytes=?,
                traffic_up_bytes=0, traffic_down_bytes=0, traffic_used_bytes=0, status='active'
            WHERE id=?
        """, (plan_id, expires, limit, user_id))
        conn.execute(
            "INSERT INTO sales (user_id, plan_id, plan_name, price, source, created_at) VALUES (?,?,?,?,?,?)",
            (user_id, plan_id, plan["name"], plan.get("price", 0), "renew", now),
        )
    sync_to_singbox()
    generate_user_sub(user_id)
    log_operation("续费用户", f"{user_id} -> {plan['name']}", "admin")


def toggle_user(user_id: str) -> str:
    with db.get_db() as conn:
        row = conn.execute("SELECT status FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise ValueError("User not found")
        new_status = "disabled" if row["status"] == "active" else "active"
        conn.execute("UPDATE users SET status=? WHERE id=?", (new_status, user_id))
    sync_to_singbox()
    if new_status == "active":
        generate_user_sub(user_id)
    log_operation("切换用户状态", f"{user_id} -> {new_status}", "admin")
    return new_status


def set_traffic(user_id: str, used_gb: float):
    used_bytes = gb_to_bytes(used_gb)
    with db.get_db() as conn:
        conn.execute(
            "UPDATE users SET traffic_used_bytes=?, traffic_up_bytes=0, traffic_down_bytes=? WHERE id=?",
            (used_bytes, used_bytes, user_id),
        )


def batch_add(plan_id: int, count: int, source: str = "batch") -> list[dict]:
    results = []
    plan = get_plan(plan_id)
    if not plan:
        raise ValueError("Invalid plan")
    now = int(time.time())
    expires = now + plan["duration_hours"] * 3600
    limit = gb_to_bytes(plan["traffic_gb"])

    with db.get_db() as conn:
        for i in range(count):
            uuid = gen_uuid()
            token = gen_token()
            user_id = f"u_{secrets.token_hex(4)}"
            remark = f"批量{i+1}"
            conn.execute(
                "INSERT INTO users VALUES (?,?,?,?,?,?,?,?,0,0,0,'active')",
                (user_id, uuid, token, plan_id, remark, now, expires, limit),
            )
            conn.execute(
                "INSERT INTO sales (user_id, plan_id, plan_name, price, source, created_at) VALUES (?,?,?,?,?,?)",
                (user_id, plan_id, plan["name"], plan.get("price", 0), source, now),
            )
            results.append({"id": user_id, "uuid": uuid, "token": token})

    sync_to_singbox()
    generate_all_subs()
    log_operation("批量添加", f"套餐{plan_id} x {count}", "admin")
    return results


def import_existing_uuid(uuid: str):
    """Import the original sing-box UUID as admin user."""
    token = gen_token()
    now = int(time.time())
    expires = now + 365 * 24 * 3600
    with db.get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE uuid=?", (uuid,)).fetchone()
        if existing:
            return
        conn.execute(
            "INSERT INTO users VALUES (?,?,?,?,?,?,?,?,0,0,0,'active')",
            ("admin", uuid, token, 0, "管理员", now, expires, 0),
        )


# ==================== Subscription Service ====================

def generate_user_sub(user_id: str):
    """Generate subscription file for a user."""
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return
    user = dict(row)
    if user["status"] != "active":
        return

    params = load_server_params()
    protocols = db.get_config_json("protocols", ["vless-reality"])
    tag = user["remark"] or "Node"
    uuid = user["uuid"]
    links = []

    for proto in protocols:
        link = None
        if proto == "vless-reality":
            link = singbox.gen_vless_link(uuid, tag, params)
        elif proto == "vmess-ws":
            link = singbox.gen_vmess_link(uuid, tag, params)
            if link:
                links.append(link)
            argo = params.get("argo_domain")
            if argo:
                link = singbox.gen_vmess_argo_link(uuid, tag, params, argo)
        elif proto == "hysteria2":
            link = singbox.gen_hy2_link(uuid, tag, params)
        elif proto == "tuic":
            link = singbox.gen_tuic_link(uuid, tag, params)
        elif proto == "anytls":
            link = singbox.gen_anytls_link(uuid, tag, params)
        if link:
            links.append(link)

    SUBS_DIR.mkdir(parents=True, exist_ok=True)
    content = base64.b64encode("\n".join(links).encode()).decode()
    (SUBS_DIR / f"{user['token']}.txt").write_text(content)


def generate_all_subs():
    with db.get_db() as conn:
        rows = conn.execute("SELECT id FROM users WHERE status='active'").fetchall()
    for row in rows:
        generate_user_sub(row["id"])


def generate_cards(plan_id: int, count: int) -> list[str]:
    """Generate subscription URLs for card platform."""
    users = batch_add(plan_id, count, source="card")
    sub_port = db.get_config("sub_port", "8888")
    server_ip = (SB_DIR / "server_ipcl.log").read_text().strip() if (SB_DIR / "server_ipcl.log").exists() else ""
    return [f"http://{server_ip}:{sub_port}/sub/{u['token']}" for u in users]


def get_sub_url(token: str) -> str:
    sub_port = db.get_config("sub_port", "8888")
    server_ip = ""
    try:
        server_ip = (SB_DIR / "server_ipcl.log").read_text().strip()
    except Exception:
        pass
    return f"http://{server_ip}:{sub_port}/sub/{token}"


# ==================== Config Sync ====================

def sync_to_singbox():
    """Sync active users to sing-box config and apply bandwidth limits."""
    with file_lock():
        with db.get_db() as conn:
            rows = conn.execute("""
                SELECT u.uuid, u.plan_id, COALESCE(p.bandwidth_mbps, 0) as bandwidth_mbps
                FROM users u LEFT JOIN plans p ON u.plan_id = p.id
                WHERE u.status = 'active'
            """).fetchall()

        active_users = [dict(r) for r in rows]

        # 1. Update inbound users
        singbox.sync_users(active_users)

        # 2. Apply bandwidth limits
        users_by_bw: dict[int, list[str]] = {}
        for u in active_users:
            bw = u["bandwidth_mbps"]
            if bw > 0:
                users_by_bw.setdefault(bw, []).append(u["uuid"])

        if users_by_bw:
            ok = singbox.inject_speed_limit_rules(users_by_bw)
            if ok and not singbox.validate_config():
                singbox.remove_speed_limit_rules()
                apply_tc_bandwidth(active_users)
            elif not ok:
                apply_tc_bandwidth(active_users)

        # 3. Validate and restart
        if singbox.validate_config():
            singbox.restart_service()
        else:
            singbox.remove_speed_limit_rules()
            if singbox.validate_config():
                singbox.restart_service()


# ==================== Bandwidth Control (tc + iptables) ====================

def apply_tc_bandwidth(active_users: list[dict]):
    iface = get_default_interface()
    vl_port = None
    cfg = singbox.load_sb_config(SB_DIR / "sb.json")
    if cfg and cfg.get("inbounds"):
        for ib in cfg["inbounds"]:
            if ib.get("type") == "vless":
                vl_port = ib.get("listen_port")
                break
    if not vl_port:
        return

    subprocess.run(["tc", "qdisc", "del", "dev", iface, "root"], capture_output=True)
    subprocess.run([
        "tc", "qdisc", "add", "dev", iface, "root", "handle", "1:", "htb", "default", "9999"
    ], capture_output=True)
    subprocess.run([
        "tc", "class", "add", "dev", iface, "parent", "1:", "classid", "1:9999",
        "htb", "rate", "1000mbit", "ceil", "1000mbit"
    ], capture_output=True)

    mark_map = {}
    for i, u in enumerate(active_users):
        bw = u.get("bandwidth_mbps", 0)
        if bw <= 0:
            continue
        mark = 10 + i
        mark_map[u["uuid"]] = mark
        subprocess.run([
            "tc", "class", "add", "dev", iface, "parent", "1:", "classid", f"1:{mark}",
            "htb", "rate", f"{bw}mbit", "ceil", f"{bw}mbit"
        ], capture_output=True)
        subprocess.run([
            "tc", "filter", "add", "dev", iface, "parent", "1:", "protocol", "ip",
            "handle", str(mark), "fw", "flowid", f"1:{mark}"
        ], capture_output=True)

    mark_file = MANAGER_DIR / "tc_marks.json"
    mark_file.write_text(json.dumps(mark_map))

    subprocess.run(["iptables", "-N", "VPN_BW"], capture_output=True)
    subprocess.run(["iptables", "-F", "VPN_BW"], capture_output=True)
    subprocess.run(["iptables", "-D", "OUTPUT", "-p", "tcp", "--sport", str(vl_port), "-j", "VPN_BW"],
                   capture_output=True)
    subprocess.run(["iptables", "-I", "OUTPUT", "-p", "tcp", "--sport", str(vl_port), "-j", "VPN_BW"],
                   capture_output=True)


def update_tc_marks():
    mark_file = MANAGER_DIR / "tc_marks.json"
    if not mark_file.exists():
        return
    try:
        mark_map = json.loads(mark_file.read_text())
    except Exception:
        return

    conns = singbox.get_connections()
    if not conns:
        return

    uuid_ips = singbox.get_uuid_to_client_ips(conns)

    subprocess.run(["iptables", "-F", "VPN_BW"], capture_output=True)
    for uuid, ips in uuid_ips.items():
        mark = mark_map.get(uuid)
        if not mark:
            continue
        for ip in ips:
            subprocess.run([
                "iptables", "-A", "VPN_BW", "-d", ip,
                "-j", "MARK", "--set-mark", str(mark)
            ], capture_output=True)


# ==================== Traffic Service ====================

def check_traffic():
    conns_data = singbox.get_connections()
    if not conns_data:
        return

    uuid_traffic = singbox.get_per_uuid_traffic(conns_data)
    update_tc_marks()
    enforce_connection_limits(conns_data)

    needs_sync = False
    now = int(time.time())

    with db.get_db() as conn:
        for uuid, (current_up, current_down) in uuid_traffic.items():
            snap = conn.execute(
                "SELECT upload_bytes, download_bytes FROM traffic_snapshots WHERE uuid=?",
                (uuid,)
            ).fetchone()

            if snap:
                prev_up, prev_down = snap["upload_bytes"], snap["download_bytes"]
                delta_up = current_up if current_up < prev_up else current_up - prev_up
                delta_down = current_down if current_down < prev_down else current_down - prev_down
            else:
                delta_up = current_up
                delta_down = current_down

            if delta_up + delta_down > 0:
                conn.execute("""
                    UPDATE users SET
                        traffic_up_bytes = traffic_up_bytes + ?,
                        traffic_down_bytes = traffic_down_bytes + ?,
                        traffic_used_bytes = traffic_used_bytes + ?
                    WHERE uuid = ? AND status = 'active'
                """, (delta_up, delta_down, delta_up + delta_down, uuid))

            conn.execute("""
                INSERT OR REPLACE INTO traffic_snapshots (uuid, upload_bytes, download_bytes, updated_at)
                VALUES (?, ?, ?, ?)
            """, (uuid, current_up, current_down, now))

        expired = conn.execute("""
            UPDATE users SET status='expired'
            WHERE status='active' AND plan_id > 0 AND expires_at < ?
        """, (now,)).rowcount

        overlimit = conn.execute("""
            UPDATE users SET status='overlimit'
            WHERE status='active' AND plan_id > 0 AND traffic_limit_bytes > 0
            AND traffic_used_bytes >= traffic_limit_bytes
        """).rowcount

        if expired > 0 or overlimit > 0:
            needs_sync = True
            if expired > 0:
                log_operation("自动过期", f"{expired} 个用户已过期")
            if overlimit > 0:
                log_operation("流量超限", f"{overlimit} 个用户已超额")

    if needs_sync:
        with db.get_db() as conn:
            rows = conn.execute("SELECT token FROM users WHERE status IN ('expired','overlimit')").fetchall()
            for row in rows:
                (SUBS_DIR / f"{row['token']}.txt").unlink(missing_ok=True)
        sync_to_singbox()

    # Auto-purge old expired users
    auto_purge()


# ==================== Connection Limit Enforcement ====================

def enforce_connection_limits(conns_data: dict):
    """Kill excess connections when a user exceeds their plan's max_connections."""
    uuid_conns: dict[str, list] = {}
    for conn_item in conns_data.get("connections", []):
        meta = conn_item.get("metadata", {})
        user_uuid = meta.get("user", "")
        if user_uuid:
            uuid_conns.setdefault(user_uuid, []).append(conn_item)

    # Get max_connections per user
    with db.get_db() as conn:
        rows = conn.execute("""
            SELECT u.uuid, COALESCE(p.max_connections, 5) as max_connections
            FROM users u LEFT JOIN plans p ON u.plan_id = p.id
            WHERE u.status = 'active' AND u.plan_id > 0
        """).fetchall()
        limits = {r["uuid"]: r["max_connections"] for r in rows}

    for uuid, connections in uuid_conns.items():
        max_conn = limits.get(uuid, 5)
        if max_conn <= 0:
            continue
        if len(connections) > max_conn:
            # Sort by start time, kill oldest excess connections
            connections.sort(key=lambda c: c.get("start", ""))
            excess = connections[:len(connections) - max_conn]
            for c in excess:
                conn_id = c.get("id", "")
                if conn_id:
                    singbox.close_connection(conn_id)


# ==================== Auto-Purge ====================

def auto_purge():
    """Delete users that have been expired for more than N days."""
    try:
        purge_days = int(db.get_config("auto_purge_days", "7"))
    except ValueError:
        purge_days = 7
    if purge_days <= 0:
        return

    cutoff = int(time.time()) - purge_days * 86400
    with db.get_db() as conn:
        rows = conn.execute("""
            SELECT id, token, remark FROM users
            WHERE status IN ('expired', 'overlimit') AND expires_at < ?
        """, (cutoff,)).fetchall()

        if rows:
            for row in rows:
                (SUBS_DIR / f"{row['token']}.txt").unlink(missing_ok=True)
            conn.execute("""
                DELETE FROM users
                WHERE status IN ('expired', 'overlimit') AND expires_at < ?
            """, (cutoff,))
            log_operation("自动清理", f"清理 {len(rows)} 个过期用户")


# ==================== Inventory / Capacity Check ====================

def get_inventory_status() -> dict:
    server_bw = int(db.get_config("server_bandwidth_mbps", "2500"))
    server_traffic_tb = float(db.get_config("server_monthly_traffic_tb", "1.0"))
    server_traffic_bytes = int(server_traffic_tb * 1099511627776)

    with db.get_db() as conn:
        rows = conn.execute("""
            SELECT u.id, u.traffic_limit_bytes, u.traffic_used_bytes,
                   COALESCE(p.bandwidth_mbps, 0) as bandwidth_mbps,
                   p.traffic_gb
            FROM users u LEFT JOIN plans p ON u.plan_id = p.id
            WHERE u.status = 'active' AND u.plan_id > 0
        """).fetchall()

    active_count = len(rows)
    total_bw_allocated = sum(r["bandwidth_mbps"] for r in rows)
    total_traffic_allocated = sum(gb_to_bytes(r["traffic_gb"]) for r in rows if r["traffic_gb"])
    total_traffic_used = sum(r["traffic_used_bytes"] for r in rows)

    plans = list_plans()
    plan_capacity = {}
    for p in plans:
        bw = p["bandwidth_mbps"]
        traffic = gb_to_bytes(p["traffic_gb"])
        bw_remaining = max(0, server_bw - total_bw_allocated)
        bw_slots = bw_remaining // bw if bw > 0 else 999
        traffic_remaining = max(0, server_traffic_bytes - total_traffic_allocated)
        traffic_slots = traffic_remaining // traffic if traffic > 0 else 999
        plan_capacity[p["id"]] = {
            "plan_name": p["name"],
            "available": min(bw_slots, traffic_slots),
            "bw_slots": bw_slots,
            "traffic_slots": traffic_slots,
        }

    return {
        "server_bandwidth_mbps": server_bw,
        "server_monthly_traffic_tb": server_traffic_tb,
        "active_users": active_count,
        "total_bw_allocated_mbps": total_bw_allocated,
        "total_traffic_allocated_gb": round(total_traffic_allocated / 1073741824, 1),
        "total_traffic_used_gb": round(total_traffic_used / 1073741824, 1),
        "bw_utilization_pct": round(total_bw_allocated / server_bw * 100, 1) if server_bw else 0,
        "traffic_utilization_pct": round(total_traffic_allocated / server_traffic_bytes * 100, 1) if server_traffic_bytes else 0,
        "plan_capacity": plan_capacity,
    }


def check_can_sell(plan_id: int, count: int = 1) -> tuple[bool, str]:
    inv = get_inventory_status()
    cap = inv["plan_capacity"].get(plan_id)
    if not cap:
        return False, "套餐不存在"
    if cap["available"] < count:
        reasons = []
        if cap["bw_slots"] < count:
            reasons.append(f"带宽不足 (剩余可售 {cap['bw_slots']} 个)")
        if cap["traffic_slots"] < count:
            reasons.append(f"流量不足 (剩余可售 {cap['traffic_slots']} 个)")
        return False, "库存不足: " + ", ".join(reasons)
    return True, "ok"


# ==================== Online Monitoring ====================

def get_online_users() -> list[dict]:
    """Get currently connected users from clash API."""
    conns_data = singbox.get_connections()
    if not conns_data:
        return []

    uuid_info: dict[str, dict] = {}
    for conn_item in conns_data.get("connections", []):
        meta = conn_item.get("metadata", {})
        user_uuid = meta.get("user", "")
        if not user_uuid:
            continue
        if user_uuid not in uuid_info:
            uuid_info[user_uuid] = {
                "uuid": user_uuid,
                "connections": 0,
                "upload": 0,
                "download": 0,
                "client_ips": set(),
            }
        uuid_info[user_uuid]["connections"] += 1
        uuid_info[user_uuid]["upload"] += conn_item.get("upload", 0)
        uuid_info[user_uuid]["download"] += conn_item.get("download", 0)
        src_ip = meta.get("sourceIP", "")
        if src_ip:
            uuid_info[user_uuid]["client_ips"].add(src_ip)

    # Enrich with user info from DB
    with db.get_db() as conn:
        for uuid, info in uuid_info.items():
            row = conn.execute(
                "SELECT id, remark, plan_id FROM users WHERE uuid=?", (uuid,)
            ).fetchone()
            if row:
                info["user_id"] = row["id"]
                info["remark"] = row["remark"]
                info["plan_id"] = row["plan_id"]
            info["client_ips"] = list(info["client_ips"])

    return list(uuid_info.values())


# ==================== Sales Statistics ====================

def get_sales_stats() -> dict:
    now = int(time.time())
    today_start = now - (now % 86400)
    month_start = now - 30 * 86400

    with db.get_db() as conn:
        # Today's sales
        today = conn.execute(
            "SELECT COUNT(*) as count, COALESCE(SUM(price), 0) as revenue FROM sales WHERE created_at >= ?",
            (today_start,)
        ).fetchone()

        # This month
        month = conn.execute(
            "SELECT COUNT(*) as count, COALESCE(SUM(price), 0) as revenue FROM sales WHERE created_at >= ?",
            (month_start,)
        ).fetchone()

        # Total
        total = conn.execute(
            "SELECT COUNT(*) as count, COALESCE(SUM(price), 0) as revenue FROM sales"
        ).fetchone()

        # By plan
        by_plan = conn.execute("""
            SELECT plan_name, COUNT(*) as count, COALESCE(SUM(price), 0) as revenue
            FROM sales GROUP BY plan_id ORDER BY plan_id
        """).fetchall()

        # By source
        by_source = conn.execute("""
            SELECT source, COUNT(*) as count, COALESCE(SUM(price), 0) as revenue
            FROM sales GROUP BY source
        """).fetchall()

        # Daily sales for last 30 days
        daily = conn.execute("""
            SELECT (created_at / 86400) as day, COUNT(*) as count, COALESCE(SUM(price), 0) as revenue
            FROM sales WHERE created_at >= ?
            GROUP BY day ORDER BY day
        """, (month_start,)).fetchall()

        # Recent sales
        recent = conn.execute("""
            SELECT s.*, u.remark FROM sales s
            LEFT JOIN users u ON s.user_id = u.id
            ORDER BY s.created_at DESC LIMIT 20
        """).fetchall()

    return {
        "today": {"count": today["count"], "revenue": today["revenue"]},
        "month": {"count": month["count"], "revenue": month["revenue"]},
        "total": {"count": total["count"], "revenue": total["revenue"]},
        "by_plan": [dict(r) for r in by_plan],
        "by_source": [dict(r) for r in by_source],
        "daily": [dict(r) for r in daily],
        "recent": [dict(r) for r in recent],
    }


# ==================== System Health ====================

def get_system_health() -> dict:
    """Get server system resource usage."""
    result = {}

    # CPU usage
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        parts = line.split()
        idle = int(parts[4])
        total = sum(int(x) for x in parts[1:])
        # Store for delta calculation
        snap_file = MANAGER_DIR / "cpu_snap.json"
        prev = {}
        if snap_file.exists():
            try:
                prev = json.loads(snap_file.read_text())
            except Exception:
                pass
        if prev:
            d_total = total - prev.get("total", 0)
            d_idle = idle - prev.get("idle", 0)
            result["cpu_pct"] = round((1 - d_idle / d_total) * 100, 1) if d_total > 0 else 0
        else:
            result["cpu_pct"] = 0
        snap_file.write_text(json.dumps({"total": total, "idle": idle}))
    except Exception:
        result["cpu_pct"] = 0

    # Memory
    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                meminfo[parts[0].rstrip(":")] = int(parts[1])
        total_mb = meminfo.get("MemTotal", 0) / 1024
        avail_mb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0)) / 1024
        used_mb = total_mb - avail_mb
        result["mem_total_mb"] = round(total_mb)
        result["mem_used_mb"] = round(used_mb)
        result["mem_pct"] = round(used_mb / total_mb * 100, 1) if total_mb > 0 else 0
    except Exception:
        result["mem_total_mb"] = 0
        result["mem_used_mb"] = 0
        result["mem_pct"] = 0

    # Network traffic (bytes since boot)
    try:
        iface = get_default_interface()
        with open("/proc/net/dev") as f:
            for line in f:
                if iface in line:
                    parts = line.split()
                    result["net_rx_bytes"] = int(parts[1])
                    result["net_tx_bytes"] = int(parts[9])
                    break
    except Exception:
        result["net_rx_bytes"] = 0
        result["net_tx_bytes"] = 0

    # Disk
    try:
        st = os.statvfs("/")
        total_gb = st.f_blocks * st.f_frsize / 1073741824
        free_gb = st.f_bavail * st.f_frsize / 1073741824
        result["disk_total_gb"] = round(total_gb, 1)
        result["disk_used_gb"] = round(total_gb - free_gb, 1)
        result["disk_pct"] = round((total_gb - free_gb) / total_gb * 100, 1) if total_gb > 0 else 0
    except Exception:
        result["disk_total_gb"] = 0
        result["disk_used_gb"] = 0
        result["disk_pct"] = 0

    # Uptime
    try:
        with open("/proc/uptime") as f:
            result["uptime_seconds"] = int(float(f.read().split()[0]))
    except Exception:
        result["uptime_seconds"] = 0

    # sing-box service status
    try:
        r = subprocess.run(["systemctl", "is-active", "sing-box"], capture_output=True, text=True, timeout=5)
        result["singbox_status"] = r.stdout.strip()
    except Exception:
        result["singbox_status"] = "unknown"

    return result


# ==================== Dashboard Summary ====================

def get_dashboard_summary() -> dict:
    """Combined summary for admin dashboard."""
    inv = get_inventory_status()
    sales = get_sales_stats()
    health = get_system_health()
    online = get_online_users()

    with db.get_db() as conn:
        user_counts = conn.execute("""
            SELECT status, COUNT(*) as count FROM users GROUP BY status
        """).fetchall()
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    status_counts = {r["status"]: r["count"] for r in user_counts}

    return {
        "users": {
            "total": total_users,
            "active": status_counts.get("active", 0),
            "expired": status_counts.get("expired", 0),
            "disabled": status_counts.get("disabled", 0),
            "overlimit": status_counts.get("overlimit", 0),
        },
        "online": {
            "count": len(online),
            "total_connections": sum(u["connections"] for u in online),
        },
        "inventory": inv,
        "sales": {
            "today_count": sales["today"]["count"],
            "today_revenue": sales["today"]["revenue"],
            "month_count": sales["month"]["count"],
            "month_revenue": sales["month"]["revenue"],
            "total_count": sales["total"]["count"],
            "total_revenue": sales["total"]["revenue"],
        },
        "system": health,
    }
