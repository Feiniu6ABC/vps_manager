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
7. Pre-generates 5 card keys per plan (subscription URLs) so shop has initial stock
8. Sets admin password via `php artisan tinker`

### Admin authentication
- Login requires both **username** and **password** (not password-only)
- Default username stored in config key `admin_username` (default: "admin")
- Password hashed with PBKDF2, stored in config key `admin_password`
- Error messages don't reveal whether username or password is wrong ("用户名或密码错误")
- Rate limiting: 5 attempts per 5 minutes per IP

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
- **Problem**: The `stilleshan/epusdt` image (v0.0.2) uses GORM AutoMigrate which creates incomplete tables with MySQL 5.7. The `orders` table is missing `block_transaction_id`, `callback_num`, and `callback_confirm` columns. This causes the epusdt API to return `Error 1054: Unknown column 'block_transaction_id'` when creating payment transactions, which breaks the entire dujiaoka → epusdt payment flow.
- **Symptom**: User clicks "buy" on dujiaoka shop → dujiaoka's EpusdtController POSTs to epusdt API → epusdt returns 200 with `status_code: 400` error → dujiaoka shows error page or empty response (due to `catch (RuleValidationException) {}` empty catch block) → order expires.
- **Fix**: Manually CREATE TABLE with the full schema from epusdt's `sql/v0.0.1.sql` (including `block_transaction_id varchar(128)`, `callback_num int DEFAULT 0`, `callback_confirm int DEFAULT 2`, UNIQUE KEY on `order_id`, INDEX on `block_transaction_id`), then INSERT wallet address, then `docker restart epusdt`.

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

### 10. MySQL 5.7 client charset defaults to latin1
- **Problem**: Even with `--character-set-server=utf8mb4`, the `mysql` CLI client inside the container defaults to `character_set_client=latin1`. Chinese text inserted via `docker exec payment-mysql mysql -e "INSERT..."` gets double-encoded (UTF-8 bytes interpreted as latin1, then stored as UTF-8).
- **Fix**: Mount a custom `/etc/mysql/conf.d/charset.cnf` into the container that sets `[client] default-character-set=utf8mb4` and `[mysqld] character-set-server=utf8mb4`. This ensures ALL connections (CLI, PHP, Go) use utf8mb4.

### 11. dujiaoka type=1 stock = carmis count (not in_stock field)
- **Problem**: For `type=1` goods (auto delivery), dujiaoka's Goods model accessor overrides `in_stock` with the count of unsold card keys in `carmis` table. Setting `in_stock=999` in the INSERT has no effect — shop shows "库存不足" if there are no card keys.
- **Fix**: Pre-generate subscription URLs via `services.generate_cards()` and insert into dujiaoka's `carmis` table during deployment. The installer creates 50 initial card keys per plan.

### 13. epusdt path config: Go code prepends "." to path values
- **Problem**: epusdt Go code prepends `.` directly to config path values. With `static_path=/app/static` → `./app/static` → `/app/app/static` (wrong). With `static_path=static` → `.static` (wrong). Payment checkout page returns "open .../index.html: no such file or directory".
- **Fix**: Use `/static`, `/runtime`, `/logs` (leading slash). Go code turns them into `./static`, `./runtime`, `./logs` which resolve correctly from working dir `/app`.

### 14. dujiaoka QUEUE_CONNECTION must be redis, NOT sync
- **Problem**: With `QUEUE_CONNECTION=sync`, Laravel's `dispatch()->delay()` is ignored — delayed jobs (like `OrderExpired`) execute immediately. This causes orders to expire the instant they are created, before the user can click "pay".
- **Symptom**: User creates order → sees bill page → order already expired (status=-1) within seconds.
- **Fix**: Set `QUEUE_CONNECTION=redis` in dujiaoka's `.env`. Redis is already deployed as `payment-redis`. The supervisord `dujiaoka-worker` process picks up delayed jobs from Redis.

### 12. `/usr/bin/vpn-manager` must be a regular file, NOT a symlink
- **Problem**: If the shortcut is a symlink to `/etc/vpn-manager/main.py`, the `install_shortcut()` function writes a bash wrapper through the symlink, destroying the Python source. The vpn-sub service then fails with SyntaxError.
- **Fix**: `install_shortcut()` checks `os.path.islink()` and removes the symlink first. Always creates a regular file.

## Legacy

`sb.sh` is the original third-party installation script (yonggekkk/sing-box-yg). It is NO LONGER NEEDED - all installation and configuration is handled by `installer.py`. The sb.sh script had security issues including command injection via sed, insecure temp files, --insecure curl downloads, and no input validation.
