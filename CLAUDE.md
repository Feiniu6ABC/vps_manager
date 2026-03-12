# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This repository contains a complete VPN subscription selling system built in Python. It replaces the original `sb.sh` bash script, providing secure sing-box installation, multi-user management, subscription plans, traffic control, and an admin web dashboard.

## Running

```bash
# First-time installation (as root)
python3 vpn-manager/main.py --install

# Interactive management menu
python3 vpn-manager/main.py
# Or after init: vpn-manager

# Start subscription server + admin dashboard
vpn-manager --server

# Other commands
vpn-manager --check           # Traffic/expiry check (cron runs every 3min)
vpn-manager --sync            # Sync users to sing-box config
vpn-manager --gen-subs        # Regenerate subscription files
vpn-manager --upgrade         # Upgrade sing-box binary
vpn-manager --set-admin-password
vpn-manager --status          # Show sing-box status
vpn-manager --uninstall
```

## Architecture

### File structure: `vpn-manager/`
| File | Purpose |
|------|---------|
| `main.py` | Entry point, argument dispatcher |
| `installer.py` | VPS installation: downloads sing-box, generates configs, sets up systemd service |
| `config.py` | Global constants, paths, plan defaults, server parameter loading |
| `database.py` | SQLite schema (WAL mode), migrations, config key-value store |
| `singbox.py` | Config parsing (comment-aware), user sync, clash API, link generators |
| `services.py` | Business logic: user CRUD, traffic tracking, bandwidth control, inventory, sales stats |
| `server.py` | HTTP server: subscription delivery, webhook API, admin dashboard routing |
| `dashboard.py` | Web admin dashboard: HTML SPA, session auth, REST API handlers |
| `cli.py` | Chinese interactive CLI menu (21 options) |
| `utils.py` | UUID generation, token generation, file locking, validators |

### Data storage: `/etc/vpn-manager/`
- `vpn-manager.db` - SQLite database (WAL mode) with tables: users, plans, config, sales, operation_logs, traffic_snapshots
- `subs/` - Per-user subscription files (base64-encoded proxy URIs)
- `tc_marks.json` - iptables mark mappings for tc bandwidth control
- `cpu_snap.json` - CPU usage snapshot for delta calculation

### sing-box files: `/etc/s-box/`
- `sb.json` - Symlink to active config (sb10.json or sb11.json)
- `sb10.json` / `sb11.json` - Version-specific configs
- `sing-box` - Binary (chmod 0700)
- `cert.pem`, `private.key` - TLS certificates (chmod 0600)
- `public.key` - Reality public key
- `server_ip.log`, `server_ipcl.log` - Server IP cache

### Database schema
```sql
plans: id, name, duration_hours, traffic_gb, bandwidth_mbps, price, max_connections
users: id, uuid, token, plan_id, remark, created_at, expires_at, traffic_limit_bytes,
       traffic_up_bytes, traffic_down_bytes, traffic_used_bytes, status
sales: id, user_id, plan_id, plan_name, price, source, created_at
operation_logs: id, timestamp, action, detail, operator
traffic_snapshots: uuid, upload_bytes, download_bytes, updated_at
config: key, value (key-value store for settings)
```

## Protocols

Default: VLESS-Reality only (best stealth). Configurable to also enable:
- VMess-WS, Hysteria2, TUIC, AnyTLS (1.11+)

Inbound indices: 0=VLESS-Reality, 1=VMess-WS, 2=Hysteria2, 3=TUIC, 4=AnyTLS

## Key Technical Decisions

### Bandwidth limiting (dual-mode, per-user total across all devices)
1. **sing-box 1.11+ route rules**: `speed_limit` + `auth_user` (per-connection, not per-user total)
2. **Linux tc HTB + iptables MARK**: True per-user total bandwidth limiting. Client IPs discovered via clash API, marks updated every 3 minutes.
3. Auto-fallback: if route rules fail validation, removes them and uses tc/iptables

### Traffic tracking (per-UUID, all devices summed)
- Cron polls clash API `/connections` every 3 minutes
- `metadata.user` field identifies authenticated UUID in sing-box 1.11+
- Delta calculation from snapshots handles sing-box restarts (counter resets)

### Connection limit enforcement
- Each plan has `max_connections` (default: daily=3, monthly=5, premium=10)
- Excess connections killed via clash API DELETE `/connections/{id}`
- Oldest connections killed first

### Inventory/overselling prevention
- `get_inventory_status()` tracks allocated bandwidth and traffic vs server capacity
- `check_can_sell()` validates before every user creation (CLI, batch, webhook API)
- Webhook returns 409 if capacity exceeded

### Config parsing
- Python `strip_json_comments()` properly handles string literals (better than sed)
- No sed-based JSON manipulation anywhere in codebase

### Security (improvements over sb.sh)
- SHA256 verification for downloaded binaries
- No `--insecure` or `-k` curl flags
- subprocess.run with list args (no shell injection)
- Secure temp files via tempfile module
- File permissions: 0600 for configs, 0700 for binaries
- PBKDF2 password hashing for admin dashboard
- Session-based auth with rate limiting (5 attempts per 5 minutes)
- SQLite WAL mode for concurrent access safety

## Subscription Plans (defaults)

| Plan | Duration | Traffic | Bandwidth | Price | Max Connections |
|------|----------|---------|-----------|-------|-----------------|
| Daily | 24h | 10GB | 20 Mbps | 2 yuan | 3 |
| Monthly | 30d | 100GB | 50 Mbps | 15 yuan | 5 |
| Premium | 30d | 200GB | 100 Mbps | 25 yuan | 10 |

Server capacity defaults: 2500 Mbps bandwidth, 1 TB monthly traffic.

## Web Admin Dashboard

Accessible at `http://server:8888/admin` (same port as subscription server).

Features:
- Overview: user counts, revenue, bandwidth/traffic utilization, system health
- User management: add/delete/toggle/renew with inventory checks
- Real-time monitoring: online users, connections, client IPs (auto-refresh 5s)
- Sales statistics: daily/monthly/total revenue, by plan/source, daily chart
- Operation audit logs
- Settings: server capacity, plans, API keys, sing-box management

## Card Platform Integration (发卡平台)

Two modes:
1. **Batch pre-generation**: Menu option 13 → generates N subscription URLs to a text file for bulk import
2. **Webhook API**: `POST /api/create` with `{"secret":"...","plan_id":1}` → returns `{"success":true,"sub_url":"..."}`

## Legacy

`sb.sh` is the original third-party installation script (yonggekkk/sing-box-yg). It is NO LONGER NEEDED - all installation and configuration is handled by `installer.py`. The sb.sh script had security issues including command injection via sed, insecure temp files, --insecure curl downloads, and no input validation.