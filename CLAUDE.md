# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This repository contains a complete VPN subscription selling system built in Python. It replaces the original `sb.sh` bash script, providing secure sing-box installation, multi-user management, subscription plans, traffic control, an admin web dashboard, and integrated payment/card-selling platform.

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
| `main.py` | Entry point, argument dispatcher, shortcut installer |
| `installer.py` | VPS installation: sing-box, Docker, epusdt, dujiaoka deployment |
| `config.py` | Global constants, paths, plan defaults, server parameter loading |
| `database.py` | SQLite schema (WAL mode), migrations, config key-value store |
| `singbox.py` | Config parsing (comment-aware), user sync, clash API, link generators |
| `services.py` | Business logic: user CRUD, traffic tracking, bandwidth control, inventory, sales stats |
| `server.py` | HTTPS server: subscription delivery, webhook API, admin dashboard routing |
| `dashboard.py` | Web admin dashboard: HTML SPA, session auth, REST API handlers |
| `cli.py` | Chinese interactive CLI menu (21 options) |
| `utils.py` | UUID generation, token generation, file locking, validators |

### Data storage: `/etc/vpn-manager/`
- `vpn-manager.db` - SQLite database (WAL mode) with tables: users, plans, config, sales, operation_logs, traffic_snapshots
- `subs/` - Per-user subscription files (base64-encoded proxy URIs)
- `ssl/admin.crt`, `ssl/admin.key` - Dedicated admin panel SSL certificate (CN=domain, SAN=*.domain+IP)
- `tc_marks.json` - iptables mark mappings for tc bandwidth control
- `cpu_snap.json` - CPU usage snapshot for delta calculation

### sing-box files: `/etc/s-box/`
- `sb.json` - Symlink to active config (sb10.json or sb11.json)
- `sb10.json` / `sb11.json` - Version-specific configs
- `sing-box` - Binary (chmod 0700)
- `cert.pem`, `private.key` - TLS certificates for Reality (CN=bing.com, NOT for admin)
- `public.key` - Reality public key
- `server_ip.log`, `server_ipcl.log` - Server IP cache

### Payment stack: Docker containers on `payment-net` network
- `payment-mysql` - MySQL 5.7, stores epusdt + dujiaoka databases
- `payment-redis` - Redis 7.4-alpine, dujiaoka cache/sessions
- `epusdt` - USDT-TRC20 payment gateway (headless API, port 8000)
- `dujiaoka` - Card-selling web storefront (port 80)

### Database schema
```sql
-- vpn-manager.db (SQLite)
plans: id, name, duration_hours, traffic_gb, bandwidth_mbps, price, max_connections
users: id, uuid, token, plan_id, remark, created_at, expires_at, traffic_limit_bytes,
       traffic_up_bytes, traffic_down_bytes, traffic_used_bytes, status
sales: id, user_id, plan_id, plan_name, price, source, created_at
operation_logs: id, timestamp, action, detail, operator
traffic_snapshots: uuid, upload_bytes, download_bytes, updated_at
config: key, value (key-value store for settings)

-- MySQL: epusdt database
wallet_address: id, created_at, updated_at, deleted_at, token, status
orders: id, created_at, updated_at, deleted_at, trade_id, order_id, amount, actual_amount, token, status, notify_url, redirect_url

-- MySQL: dujiaoka database (from install.sql, NOT Laravel migrations)
goods_group: id, gp_name, is_open, ord, created_at, updated_at, deleted_at
goods: id, group_id, gd_name, gd_description, gd_keywords, picture, retail_price, actual_price, in_stock, sales_volume, ord, buy_limit_num, buy_prompt, description, type, api_hook, is_open, ...
carmis: id, goods_id, status, is_loop, carmi (text), created_at, updated_at, deleted_at
pays: id, pay_name, pay_check (unique), pay_method (tinyint), pay_client (tinyint), merchant_id, merchant_key, merchant_pem, pay_handleroute, is_open, ...
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

### SSL certificates (dual-cert architecture)
- **sing-box Reality cert** (`/etc/s-box/cert.pem`): Self-signed with CN=bing.com (mimics target SNI for anti-detection). Used only by sing-box VLESS-Reality inbound.
- **Admin panel cert** (`/etc/vpn-manager/ssl/admin.crt`): Self-signed with CN=actual-domain, SAN=*.domain+IP. Used by the subscription/admin HTTPS server. Prevents Cloudflare 526 errors (CF rejects certs with wrong CN).
- `server.py` prefers admin cert if it exists, falls back to Reality cert.

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

Accessible at `https://server:2096/admin` (HTTPS, Cloudflare-compatible port).

Features:
- Overview: user counts, revenue, bandwidth/traffic utilization, system health
- User management: add/delete/toggle/renew with inventory checks
- Real-time monitoring: online users, connections, client IPs (auto-refresh 5s)
- Sales statistics: daily/monthly/total revenue, by plan/source, daily chart
- Operation audit logs
- Settings: server capacity, plans, API keys, sing-box management

## Card Platform Integration (发卡平台)

### Architecture: vpn-manager ↔ dujiaoka ↔ epusdt

```
Customer → dujiaoka shop → pays with USDT → epusdt detects payment
                         → dujiaoka delivers card key (subscription URL)
```

### Delivery mechanism: Card keys (卡密)
- dujiaoka `goods.type=1` = automatic delivery from `carmis` table
- dujiaoka only supports type=1 (auto/card key) and type=2 (manual) — NO API delivery type
- `api_hook` field is fire-and-forget notification, NOT used for delivery
- Admin generates subscription URLs via vpn-manager menu 13 → auto-imported to dujiaoka `carmis` table
- For type=1 goods, dujiaoka overrides `in_stock` with count of unsold card keys

### Webhook API (for external platforms)
- `POST /api/create` with `{"secret":"...","plan_id":1}` → returns `{"success":true,"sub_url":"..."}`
- This is for external card platforms, NOT used by dujiaoka integration

### Auto-deployment (installer.py)
During installation, `deploy_dujiaoka()` automatically:
1. Creates MySQL + Redis containers on `payment-net` Docker network
2. Imports dujiaoka schema from `install.sql` (NOT Laravel migrations)
3. Creates `install.lock` to skip web installer
4. Configures epusdt payment method (UPDATE pays SET ... WHERE pay_check='epusdt')
5. Disables all demo payment methods
6. Creates VPN product group + 3 products matching vpn-manager plans
7. Sets admin password via `php artisan tinker`

## Docker Image Pinning

All Docker images are pinned by digest to prevent breaking changes:

```python
DOCKER_IMAGE_MYSQL = "mysql:5.7"
DOCKER_IMAGE_REDIS = "redis:7.4-alpine"
DOCKER_IMAGE_EPUSDT = "stilleshan/epusdt@sha256:ae2c767a9ab3..."
DOCKER_IMAGE_DUJIAOKA = "stilleshan/dujiaoka@sha256:320818591390..."
```

## Known Pitfalls & Lessons Learned

### 1. epusdt GORM AutoMigrate broken (stilleshan/epusdt Docker image)
- **Problem**: The `stilleshan/epusdt` image (v0.0.2) uses GORM AutoMigrate which silently fails with MySQL 5.7. Tables `wallet_address` and `orders` are never created. epusdt starts and serves HTTP but logs `Error 1146: Table 'epusdt.wallet_address' doesn't exist` every 5 seconds.
- **Fix**: Manually CREATE TABLE via `docker exec payment-mysql mysql` after container starts, then INSERT wallet address, then `docker restart epusdt`.

### 2. dujiaoka schema: install.sql, NOT Laravel migrations
- **Problem**: The `stilleshan/dujiaoka` image has no migration files. Running `php artisan migrate` does nothing.
- **Fix**: Copy `/dujiaoka/database/sql/install.sql` from container and import via mysql. Also need `install.lock` at `/dujiaoka/install.lock` to prevent web installer.

### 3. dujiaoka `pays` table schema gotchas
- `pay_client` is `tinyint(1)` (NOT varchar) — use numeric values like 3
- `pay_method` is `tinyint(1)` — use numeric values like 1
- There is NO `pay_uri` column — gateway URL goes in `merchant_pem`
- API token goes in `merchant_id`
- Route is `pay/epusdt` (in `pay_handleroute`)
- `pay_check` has a UNIQUE constraint — use UPDATE, not re-INSERT

### 4. MySQL readiness detection
- **Problem**: `mysqladmin ping` succeeds before MySQL actually accepts authenticated connections.
- **Fix**: Use `docker exec payment-mysql mysql -uroot -p{password} -e "SELECT 1"` for readiness checks.

### 5. Reality cert causes Cloudflare 526
- **Problem**: sing-box Reality cert has CN=bing.com (for anti-detection). If the admin panel uses this cert, Cloudflare rejects it with 526 (invalid SSL certificate) because CN doesn't match the actual domain.
- **Fix**: Generate a separate admin SSL cert with CN=actual-domain and SAN=*.domain+IP. Store at `/etc/vpn-manager/ssl/admin.{crt,key}`.

### 6. main.py shortcut symlink self-destruction
- **Problem**: If `/usr/bin/vpn-manager` is a symlink to `/etc/vpn-manager/main.py`, the `install_shortcut()` function writes a bash wrapper through the symlink, destroying main.py.
- **Fix**: Check `os.path.islink(SHORTCUT_PATH)` and `os.unlink()` before writing. Always create `/usr/bin/vpn-manager` as a regular file, never a symlink.

### 7. dujiaoka APP_NAME must be quoted
- **Problem**: Laravel .env parser fails on unquoted values containing spaces (e.g., `APP_NAME=独角数卡`).
- **Fix**: Quote the value: `APP_NAME="独角数卡"`

### 8. Docker container networking
- All payment containers must be on the same Docker network (`payment-net`) to communicate by hostname
- Container hostnames: `payment-mysql`, `payment-redis`, `epusdt`
- epusdt connects to MySQL as `payment-mysql:3306`, dujiaoka same
- dujiaoka calls epusdt API at `http://epusdt:8000/api/v1/order/create-transaction`

### 9. `database.py` context manager
- `db.get_db()` returns a context manager, must use `with db.get_db() as conn:` — NOT `db.get_db().execute()`

## Legacy

`sb.sh` is the original third-party installation script (yonggekkk/sing-box-yg). It is NO LONGER NEEDED - all installation and configuration is handled by `installer.py`. The sb.sh script had security issues including command injection via sed, insecure temp files, --insecure curl downloads, and no input validation.
