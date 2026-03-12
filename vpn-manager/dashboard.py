"""Web admin dashboard with authentication."""
import json
import time
import hashlib
import os
import secrets
from http.server import BaseHTTPRequestHandler

import database as db
import services

# ==================== Session Management ====================

_sessions: dict[str, float] = {}  # token -> expiry timestamp
SESSION_TTL = 86400  # 24 hours
_login_attempts: dict[str, list[float]] = {}  # ip -> [timestamps]
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW = 300  # 5 minutes


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000)
    return salt.hex() + ":" + key.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, key_hex = stored.split(":")
        salt = bytes.fromhex(salt_hex)
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000)
        return key.hex() == key_hex
    except Exception:
        return False


def create_session() -> str:
    token = secrets.token_hex(32)
    _sessions[token] = time.time() + SESSION_TTL
    # Cleanup expired sessions
    now = time.time()
    expired = [k for k, v in _sessions.items() if v < now]
    for k in expired:
        del _sessions[k]
    return token


def check_session(token: str) -> bool:
    if not token:
        return False
    expiry = _sessions.get(token)
    if not expiry:
        return False
    if time.time() > expiry:
        del _sessions[token]
        return False
    return True


def check_rate_limit(ip: str) -> bool:
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < ATTEMPT_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) < MAX_ATTEMPTS


def record_attempt(ip: str):
    _login_attempts.setdefault(ip, []).append(time.time())


# ==================== Request Handler ====================

def handle_admin_request(handler: BaseHTTPRequestHandler, method: str, path: str):
    """Handle all /admin/* requests. Returns True if handled."""
    # Strip /admin prefix
    sub = path[6:] if len(path) > 6 else "/"  # /admin -> /, /admin/api/x -> /api/x
    if not sub:
        sub = "/"

    # Serve dashboard HTML
    if method == "GET" and (sub == "/" or sub == ""):
        _serve_html(handler)
        return True

    # Login endpoint (no auth needed)
    if method == "POST" and sub == "/api/login":
        _handle_login(handler)
        return True

    # Password setup (only when no password set)
    if method == "POST" and sub == "/api/setup":
        _handle_setup(handler)
        return True

    # Check if password is set
    if method == "GET" and sub == "/api/check":
        pwd = db.get_config("admin_password", "")
        _send_json(handler, 200, {"has_password": bool(pwd)})
        return True

    # All other API routes require authentication
    session_token = _get_session_token(handler)
    if not check_session(session_token):
        _send_json(handler, 401, {"error": "Unauthorized"})
        return True

    # API routes
    if method == "GET":
        if sub == "/api/summary":
            data = services.get_dashboard_summary()
            _send_json(handler, 200, data)
        elif sub == "/api/users":
            users = services.list_users()
            _send_json(handler, 200, {"users": users})
        elif sub == "/api/plans":
            plans = services.list_plans()
            _send_json(handler, 200, {"plans": plans})
        elif sub == "/api/online":
            online = services.get_online_users()
            _send_json(handler, 200, {"online": online})
        elif sub == "/api/sales":
            stats = services.get_sales_stats()
            _send_json(handler, 200, stats)
        elif sub == "/api/logs":
            logs = services.get_recent_logs(100)
            _send_json(handler, 200, {"logs": logs})
        elif sub == "/api/inventory":
            inv = services.get_inventory_status()
            _send_json(handler, 200, inv)
        elif sub == "/api/system":
            health = services.get_system_health()
            _send_json(handler, 200, health)
        elif sub.startswith("/api/user/"):
            uid = sub.split("/")[-1]
            user = services.get_user(uid)
            if user:
                user["sub_url"] = services.get_sub_url(user["token"])
                _send_json(handler, 200, user)
            else:
                _send_json(handler, 404, {"error": "Not found"})
        else:
            _send_json(handler, 404, {"error": "Not found"})

    elif method == "POST":
        body = _read_body(handler)
        if sub == "/api/users/add":
            plan_id = body.get("plan_id", 1)
            remark = body.get("remark", "")
            can, reason = services.check_can_sell(plan_id)
            if not can:
                _send_json(handler, 409, {"error": reason})
            else:
                try:
                    user = services.add_user(plan_id, remark, source="dashboard")
                    user["sub_url"] = services.get_sub_url(user["token"])
                    _send_json(handler, 200, user)
                except Exception as e:
                    _send_json(handler, 500, {"error": str(e)})
        elif sub == "/api/users/batch":
            plan_id = body.get("plan_id", 1)
            count = body.get("count", 1)
            can, reason = services.check_can_sell(plan_id, count)
            if not can:
                _send_json(handler, 409, {"error": reason})
            else:
                try:
                    users = services.batch_add(plan_id, count, source="dashboard")
                    urls = [services.get_sub_url(u["token"]) for u in users]
                    _send_json(handler, 200, {"users": users, "urls": urls})
                except Exception as e:
                    _send_json(handler, 500, {"error": str(e)})
        elif sub.startswith("/api/users/delete/"):
            uid = sub.split("/")[-1]
            services.delete_user(uid)
            _send_json(handler, 200, {"ok": True})
        elif sub.startswith("/api/users/toggle/"):
            uid = sub.split("/")[-1]
            try:
                new_status = services.toggle_user(uid)
                _send_json(handler, 200, {"status": new_status})
            except Exception as e:
                _send_json(handler, 400, {"error": str(e)})
        elif sub.startswith("/api/users/renew/"):
            uid = sub.split("/")[-1]
            plan_id = body.get("plan_id", 1)
            try:
                services.renew_user(uid, plan_id)
                _send_json(handler, 200, {"ok": True})
            except Exception as e:
                _send_json(handler, 400, {"error": str(e)})
        elif sub == "/api/plans/update":
            pid = body.get("id")
            if pid:
                kwargs = {}
                for k in ("name", "duration_hours", "traffic_gb", "bandwidth_mbps", "price", "max_connections"):
                    if k in body:
                        kwargs[k] = body[k]
                services.update_plan(pid, **kwargs)
                _send_json(handler, 200, {"ok": True})
            else:
                _send_json(handler, 400, {"error": "Missing plan id"})
        elif sub == "/api/settings":
            for k in ("sub_port", "server_bandwidth_mbps", "server_monthly_traffic_tb",
                       "auto_purge_days", "api_secret"):
                if k in body:
                    db.set_config(k, str(body[k]))
            services.log_operation("修改设置", json.dumps(body, ensure_ascii=False), "admin")
            _send_json(handler, 200, {"ok": True})
        elif sub == "/api/sync":
            services.sync_to_singbox()
            services.log_operation("手动同步", "", "admin")
            _send_json(handler, 200, {"ok": True})
        elif sub == "/api/refresh-subs":
            services.generate_all_subs()
            _send_json(handler, 200, {"ok": True})
        elif sub == "/api/check-traffic":
            services.check_traffic()
            _send_json(handler, 200, {"ok": True})
        elif sub == "/api/change-password":
            new_pwd = body.get("password", "")
            if len(new_pwd) < 6:
                _send_json(handler, 400, {"error": "密码至少6位"})
            else:
                db.set_config("admin_password", hash_password(new_pwd))
                _sessions.clear()
                services.log_operation("修改密码", "", "admin")
                _send_json(handler, 200, {"ok": True})
        elif sub == "/api/logout":
            if session_token in _sessions:
                del _sessions[session_token]
            _send_json(handler, 200, {"ok": True})
        else:
            _send_json(handler, 404, {"error": "Not found"})

    return True


# ==================== Helpers ====================

def _get_session_token(handler: BaseHTTPRequestHandler) -> str:
    # Check Authorization header first
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    # Check cookie
    cookies = handler.headers.get("Cookie", "")
    for part in cookies.split(";"):
        part = part.strip()
        if part.startswith("session="):
            return part[8:]
    return ""


def _read_body(handler: BaseHTTPRequestHandler) -> dict:
    try:
        length = int(handler.headers.get("Content-Length", 0))
        return json.loads(handler.rfile.read(length)) if length > 0 else {}
    except Exception:
        return {}


def _send_json(handler: BaseHTTPRequestHandler, code: int, data):
    body = json.dumps(data, ensure_ascii=False, default=str).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _handle_login(handler: BaseHTTPRequestHandler):
    ip = handler.client_address[0]
    if not check_rate_limit(ip):
        _send_json(handler, 429, {"error": "登录尝试过多，请稍后再试"})
        return

    body = _read_body(handler)
    password = body.get("password", "")
    stored = db.get_config("admin_password", "")

    if not stored:
        _send_json(handler, 400, {"error": "请先设置管理员密码"})
        return

    if verify_password(password, stored):
        token = create_session()
        services.log_operation("管理员登录", f"IP: {ip}", "admin")
        _send_json(handler, 200, {"token": token})
    else:
        record_attempt(ip)
        _send_json(handler, 401, {"error": "密码错误"})


def _handle_setup(handler: BaseHTTPRequestHandler):
    stored = db.get_config("admin_password", "")
    if stored:
        _send_json(handler, 400, {"error": "密码已设置，请通过登录后修改"})
        return

    body = _read_body(handler)
    password = body.get("password", "")
    if len(password) < 6:
        _send_json(handler, 400, {"error": "密码至少6位"})
        return

    db.set_config("admin_password", hash_password(password))
    token = create_session()
    services.log_operation("初始化密码", f"IP: {handler.client_address[0]}", "admin")
    _send_json(handler, 200, {"token": token})


def _serve_html(handler: BaseHTTPRequestHandler):
    html = ADMIN_HTML.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(html)))
    handler.end_headers()
    handler.wfile.write(html)


# ==================== Admin HTML Template ====================

ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VPN Admin Panel</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#1c2128;--border:#30363d;--text:#c9d1d9;--text2:#8b949e;
--blue:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--purple:#bc8cff}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
background:var(--bg);color:var(--text);font-size:14px;line-height:1.5}
a{color:var(--blue);text-decoration:none}
/* Login Page */
.login-wrap{display:flex;align-items:center;justify-content:center;min-height:100vh;background:var(--bg)}
.login-box{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:40px;width:380px;text-align:center}
.login-box h1{font-size:20px;margin-bottom:8px;color:var(--blue)}
.login-box p{color:var(--text2);margin-bottom:24px;font-size:13px}
.login-box input{width:100%;padding:10px 14px;background:var(--bg);border:1px solid var(--border);
border-radius:8px;color:var(--text);font-size:14px;margin-bottom:16px;outline:none}
.login-box input:focus{border-color:var(--blue)}
.login-box button{width:100%;padding:10px;background:var(--green);color:#fff;border:none;
border-radius:8px;font-size:14px;cursor:pointer;font-weight:600}
.login-box button:hover{opacity:0.9}
.login-box .error{color:var(--red);font-size:13px;margin-bottom:12px}
/* Layout */
.app{display:none}
.sidebar{position:fixed;left:0;top:0;width:220px;height:100vh;background:#010409;border-right:1px solid var(--border);
display:flex;flex-direction:column;z-index:100}
.sidebar .logo{padding:20px;font-size:15px;font-weight:700;color:var(--blue);border-bottom:1px solid var(--border)}
.sidebar nav{flex:1;padding:8px 0}
.sidebar nav a{display:flex;align-items:center;gap:10px;padding:10px 20px;color:var(--text2);font-size:13px;
transition:all .15s}
.sidebar nav a:hover{color:var(--text);background:var(--bg2)}
.sidebar nav a.active{color:var(--text);background:var(--bg2);border-right:2px solid var(--blue)}
.sidebar nav a .icon{width:18px;text-align:center;font-size:15px}
.sidebar .bottom{padding:12px 20px;border-top:1px solid var(--border)}
.sidebar .bottom a{color:var(--text2);font-size:12px}
.main{margin-left:220px;padding:24px 30px;min-height:100vh}
.page-title{font-size:20px;font-weight:600;margin-bottom:20px;display:flex;align-items:center;gap:12px}
/* Cards */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;margin-bottom:24px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:18px}
.card .label{font-size:12px;color:var(--text2);text-transform:uppercase;letter-spacing:.5px}
.card .val{font-size:26px;font-weight:700;margin-top:6px}
.card .sub{font-size:12px;color:var(--text2);margin-top:4px}
.card.green .val{color:var(--green)} .card.blue .val{color:var(--blue)}
.card.red .val{color:var(--red)} .card.yellow .val{color:var(--yellow)}
.card.purple .val{color:var(--purple)}
/* Table */
.tbl-wrap{background:var(--bg2);border:1px solid var(--border);border-radius:10px;overflow:hidden;margin-bottom:20px}
.tbl-wrap .tbl-header{display:flex;justify-content:space-between;align-items:center;padding:14px 18px;
border-bottom:1px solid var(--border)}
.tbl-wrap .tbl-header h3{font-size:14px;font-weight:600}
table{width:100%;border-collapse:collapse}
th,td{padding:10px 18px;text-align:left;border-bottom:1px solid #21262d;white-space:nowrap}
th{background:var(--bg3);color:var(--text2);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.5px}
tr:hover td{background:rgba(56,139,253,0.04)}
/* Buttons */
.btn{padding:5px 12px;border:1px solid var(--border);border-radius:6px;cursor:pointer;font-size:12px;
background:var(--bg2);color:var(--text);transition:all .15s;display:inline-flex;align-items:center;gap:4px}
.btn:hover{background:var(--bg3);border-color:var(--text2)}
.btn-sm{padding:3px 8px;font-size:11px}
.btn-green{background:var(--green);color:#fff;border-color:var(--green)}
.btn-green:hover{opacity:.9}
.btn-red{background:transparent;color:var(--red);border-color:var(--red)}
.btn-red:hover{background:var(--red);color:#fff}
.btn-blue{background:var(--blue);color:#fff;border-color:var(--blue)}
.btn-blue:hover{opacity:.9}
/* Tags */
.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500}
.tag-green{background:rgba(63,185,80,.15);color:var(--green)}
.tag-red{background:rgba(248,81,73,.15);color:var(--red)}
.tag-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}
.tag-gray{background:rgba(139,148,158,.15);color:var(--text2)}
/* Progress */
.prog{height:6px;background:var(--border);border-radius:3px;overflow:hidden;margin-top:6px}
.prog-fill{height:100%;border-radius:3px;transition:width .5s}
/* Modal */
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:200;align-items:center;justify-content:center}
.modal-bg.show{display:flex}
.modal{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:24px;width:420px;max-width:90vw}
.modal h3{font-size:16px;margin-bottom:16px}
.modal label{display:block;font-size:12px;color:var(--text2);margin-bottom:4px;margin-top:12px}
.modal input,.modal select{width:100%;padding:8px 12px;background:var(--bg);border:1px solid var(--border);
border-radius:6px;color:var(--text);font-size:13px;outline:none}
.modal input:focus,.modal select:focus{border-color:var(--blue)}
.modal .actions{display:flex;gap:8px;margin-top:20px;justify-content:flex-end}
/* Flex utils */
.flex{display:flex;align-items:center;gap:8px}
.gap-sm{gap:4px}
.ml-auto{margin-left:auto}
.mt-1{margin-top:8px}.mt-2{margin-top:16px}.mb-1{margin-bottom:8px}.mb-2{margin-bottom:16px}
/* Toast */
.toast{position:fixed;top:20px;right:20px;padding:12px 20px;background:var(--bg2);border:1px solid var(--border);
border-radius:8px;z-index:300;font-size:13px;opacity:0;transition:opacity .3s;pointer-events:none}
.toast.show{opacity:1}
.toast.ok{border-color:var(--green);color:var(--green)}
.toast.err{border-color:var(--red);color:var(--red)}
/* Responsive */
@media(max-width:768px){
.sidebar{width:60px}.sidebar .logo span,.sidebar nav a span,.sidebar .bottom{display:none}
.sidebar nav a{padding:12px;justify-content:center}.main{margin-left:60px;padding:16px}
.cards{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<!-- Login Page -->
<div class="login-wrap" id="loginPage">
<div class="login-box">
<h1>VPN Admin Panel</h1>
<p id="loginSubtitle">管理员登录</p>
<div class="error" id="loginError" style="display:none"></div>
<input type="password" id="loginPwd" placeholder="管理员密码" autofocus>
<input type="password" id="loginPwd2" placeholder="确认密码" style="display:none">
<button onclick="doLogin()" id="loginBtn">登录</button>
</div>
</div>

<!-- App -->
<div class="app" id="app">
<div class="sidebar">
<div class="logo"><span>VPN Admin</span></div>
<nav>
<a href="#overview" class="active" data-page="overview"><span class="icon">&#9632;</span><span>概览</span></a>
<a href="#users" data-page="users"><span class="icon">&#9679;</span><span>用户管理</span></a>
<a href="#monitor" data-page="monitor"><span class="icon">&#9670;</span><span>实时监控</span></a>
<a href="#sales" data-page="sales"><span class="icon">&#9733;</span><span>营收统计</span></a>
<a href="#logs" data-page="logs"><span class="icon">&#9776;</span><span>操作日志</span></a>
<a href="#settings" data-page="settings"><span class="icon">&#9881;</span><span>系统设置</span></a>
</nav>
<div class="bottom"><a href="javascript:void(0)" onclick="doLogout()">退出登录</a></div>
</div>
<div class="main" id="mainContent"></div>
</div>

<!-- Modal -->
<div class="modal-bg" id="modalBg" onclick="if(event.target===this)closeModal()">
<div class="modal" id="modalBox"></div>
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<script>
let token='';
const API='/admin/api';

// ===== Auth =====
async function initAuth(){
  const r=await fetch(API+'/check');
  const d=await r.json();
  if(!d.has_password){
    document.getElementById('loginSubtitle').textContent='首次使用，请设置管理员密码';
    document.getElementById('loginPwd').placeholder='设置密码 (至少6位)';
    document.getElementById('loginPwd2').style.display='';
    document.getElementById('loginBtn').textContent='确认设置';
    document.getElementById('loginBtn').onclick=doSetup;
  }
  const saved=localStorage.getItem('vpn_admin_token');
  if(saved){
    token=saved;
    const t=await apiFetch('/summary');
    if(t){showApp();return}
    localStorage.removeItem('vpn_admin_token');
  }
  document.getElementById('loginPage').style.display='flex';
}
async function doLogin(){
  const pwd=document.getElementById('loginPwd').value;
  if(!pwd){showLoginErr('请输入密码');return}
  const r=await fetch(API+'/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pwd})});
  const d=await r.json();
  if(r.ok){token=d.token;localStorage.setItem('vpn_admin_token',token);showApp()}
  else showLoginErr(d.error||'登录失败')
}
async function doSetup(){
  const p1=document.getElementById('loginPwd').value;
  const p2=document.getElementById('loginPwd2').value;
  if(p1.length<6){showLoginErr('密码至少6位');return}
  if(p1!==p2){showLoginErr('两次密码不一致');return}
  const r=await fetch(API+'/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:p1})});
  const d=await r.json();
  if(r.ok){token=d.token;localStorage.setItem('vpn_admin_token',token);showApp()}
  else showLoginErr(d.error||'设置失败')
}
function showLoginErr(msg){const e=document.getElementById('loginError');e.textContent=msg;e.style.display='block'}
function doLogout(){token='';localStorage.removeItem('vpn_admin_token');location.reload()}
function showApp(){document.getElementById('loginPage').style.display='none';document.getElementById('app').style.display='block';route()}

// ===== API =====
async function apiFetch(path,opts={}){
  try{
    const r=await fetch(API+path,{...opts,headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json',...(opts.headers||{})}});
    if(r.status===401){doLogout();return null}
    return await r.json()
  }catch(e){return null}
}
async function apiPost(path,body={}){
  return apiFetch(path,{method:'POST',body:JSON.stringify(body)})
}

// ===== Router =====
let currentPage='overview';
let refreshTimer=null;
function route(){
  const hash=location.hash.slice(1)||'overview';
  currentPage=hash;
  document.querySelectorAll('.sidebar nav a').forEach(a=>{
    a.classList.toggle('active',a.dataset.page===hash)
  });
  if(refreshTimer)clearInterval(refreshTimer);
  const pages={overview:pageOverview,users:pageUsers,monitor:pageMonitor,sales:pageSales,logs:pageLogs,settings:pageSettings};
  (pages[hash]||pageOverview)();
}
window.addEventListener('hashchange',route);

// ===== Toast =====
function toast(msg,ok=true){
  const t=document.getElementById('toast');
  t.textContent=msg;t.className='toast show '+(ok?'ok':'err');
  setTimeout(()=>t.className='toast',3000)
}

// ===== Modal =====
function openModal(html){document.getElementById('modalBox').innerHTML=html;document.getElementById('modalBg').classList.add('show')}
function closeModal(){document.getElementById('modalBg').classList.remove('show')}

// ===== Utils =====
function fmtBytes(b){if(!b)return'0 B';const u=['B','KB','MB','GB','TB'];let i=0;let v=b;while(v>=1024&&i<4){v/=1024;i++}return v.toFixed(i>1?2:0)+' '+u[i]}
function fmtTime(ts){if(!ts)return'-';const d=new Date(ts*1000);return d.getFullYear()+'-'+s2(d.getMonth()+1)+'-'+s2(d.getDate())+' '+s2(d.getHours())+':'+s2(d.getMinutes())}
function s2(n){return n<10?'0'+n:''+n}
function fmtDuration(s){const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);return(d?d+'天':'')+(h?h+'时':'')+(m?m+'分':'')||'0分'}
function statusTag(s){const m={active:['在线','green'],expired:['过期','red'],overlimit:['超额','yellow'],disabled:['禁用','gray']};const[t,c]=m[s]||[s,'gray'];return`<span class="tag tag-${c}">${t}</span>`}
function progBar(pct,color){color=color||(pct<70?'var(--green)':pct<90?'var(--yellow)':'var(--red)');return`<div class="prog"><div class="prog-fill" style="width:${Math.min(pct,100)}%;background:${color}"></div></div>`}
const $=id=>document.getElementById(id);
const mc=id=>document.getElementById('mainContent');

// ===== Pages =====

async function pageOverview(){
  mc().innerHTML='<div class="page-title">概览</div><div id="ov">加载中...</div>';
  const d=await apiFetch('/summary');
  if(!d)return;
  const u=d.users,s=d.sales,inv=d.inventory,sys=d.system,on=d.online;
  mc().innerHTML=`
  <div class="page-title">概览</div>
  <div class="cards">
    <div class="card green"><div class="label">活跃用户</div><div class="val">${u.active}</div><div class="sub">总计 ${u.total} / 在线 ${on.count}</div></div>
    <div class="card blue"><div class="label">今日营收</div><div class="val">&yen;${s.today_revenue.toFixed(2)}</div><div class="sub">今日销量 ${s.today_count} 单</div></div>
    <div class="card yellow"><div class="label">本月营收</div><div class="val">&yen;${s.month_revenue.toFixed(2)}</div><div class="sub">本月销量 ${s.month_count} 单</div></div>
    <div class="card purple"><div class="label">累计营收</div><div class="val">&yen;${s.total_revenue.toFixed(2)}</div><div class="sub">累计 ${s.total_count} 单</div></div>
  </div>
  <div class="cards">
    <div class="card"><div class="label">带宽利用率</div><div class="val" style="font-size:20px">${inv.total_bw_allocated_mbps} / ${inv.server_bandwidth_mbps} Mbps</div>${progBar(inv.bw_utilization_pct)}<div class="sub">${inv.bw_utilization_pct}%</div></div>
    <div class="card"><div class="label">流量利用率</div><div class="val" style="font-size:20px">${inv.total_traffic_allocated_gb} / ${(inv.server_monthly_traffic_tb*1024).toFixed(0)} GB</div>${progBar(inv.traffic_utilization_pct)}<div class="sub">${inv.traffic_utilization_pct}%</div></div>
    <div class="card"><div class="label">CPU / 内存</div><div class="val" style="font-size:20px">${sys.cpu_pct}% / ${sys.mem_pct}%</div>${progBar(sys.cpu_pct)}<div class="sub mt-1">${sys.mem_used_mb}MB / ${sys.mem_total_mb}MB</div></div>
    <div class="card"><div class="label">系统状态</div><div class="val" style="font-size:16px">sing-box: ${sys.singbox_status==='active'?'<span style="color:var(--green)">运行中</span>':'<span style="color:var(--red)">'+sys.singbox_status+'</span>'}</div><div class="sub mt-1">运行时间: ${fmtDuration(sys.uptime_seconds)}</div><div class="sub">磁盘: ${sys.disk_used_gb}/${sys.disk_total_gb}GB (${sys.disk_pct}%)</div></div>
  </div>
  <div class="cards" style="grid-template-columns:1fr 1fr">
    <div class="tbl-wrap"><div class="tbl-header"><h3>库存状态</h3></div><table><tr><th>套餐</th><th>可售</th><th>带宽限</th><th>流量限</th></tr>
    ${Object.values(inv.plan_capacity).map(p=>`<tr><td>${p.plan_name}</td><td><span class="tag ${p.available>5?'tag-green':p.available>0?'tag-yellow':'tag-red'}">${p.available}</span></td><td>${p.bw_slots}</td><td>${p.traffic_slots}</td></tr>`).join('')}
    </table></div>
    <div class="tbl-wrap"><div class="tbl-header"><h3>用户状态</h3></div><table><tr><th>状态</th><th>数量</th></tr>
    <tr><td>${statusTag('active')}</td><td>${u.active}</td></tr>
    <tr><td>${statusTag('expired')}</td><td>${u.expired}</td></tr>
    <tr><td>${statusTag('overlimit')}</td><td>${u.overlimit}</td></tr>
    <tr><td>${statusTag('disabled')}</td><td>${u.disabled}</td></tr>
    </table></div>
  </div>`;
  refreshTimer=setInterval(pageOverview,30000);
}

async function pageUsers(){
  mc().innerHTML='<div class="page-title">用户管理 <button class="btn btn-green ml-auto" onclick="showAddUser()">+ 添加用户</button><button class="btn btn-blue" style="margin-left:8px" onclick="showBatchAdd()">批量添加</button></div><div id="userList">加载中...</div>';
  const d=await apiFetch('/users');
  if(!d)return;
  const plans=await apiFetch('/plans');
  window._plans=plans?plans.plans:[];
  let html='<div class="tbl-wrap"><table><tr><th>ID</th><th>备注</th><th>套餐</th><th>状态</th><th>到期</th><th>已用流量</th><th>限额</th><th>操作</th></tr>';
  for(const u of d.users){
    const used=fmtBytes(u.traffic_used_bytes);
    const limit=u.traffic_limit_bytes?fmtBytes(u.traffic_limit_bytes):'无限';
    html+=`<tr><td><code style="font-size:11px">${u.id}</code></td><td>${u.remark||'-'}</td>
    <td>${u.plan_name||'管理员'}</td><td>${statusTag(u.status)}</td><td>${fmtTime(u.expires_at)}</td>
    <td>${used}</td><td>${limit}</td>
    <td class="flex gap-sm">
      <button class="btn btn-sm" onclick="showUserDetail('${u.id}')">详情</button>
      <button class="btn btn-sm" onclick="toggleUser('${u.id}')">${u.status==='active'?'禁用':'启用'}</button>
      <button class="btn btn-sm btn-red" onclick="deleteUser('${u.id}','${u.remark}')">删除</button>
    </td></tr>`;
  }
  html+='</table></div>';
  $('userList').innerHTML=html;
}

function showAddUser(){
  const opts=window._plans.map(p=>`<option value="${p.id}">${p.name} (${p.price}元)</option>`).join('');
  openModal(`<h3>添加用户</h3>
  <label>套餐</label><select id="mPlan">${opts}</select>
  <label>备注</label><input id="mRemark" placeholder="可选">
  <div class="actions"><button class="btn" onclick="closeModal()">取消</button><button class="btn btn-green" onclick="doAddUser()">确认添加</button></div>`);
}

async function doAddUser(){
  const r=await apiPost('/users/add',{plan_id:parseInt($('mPlan').value),remark:$('mRemark').value});
  closeModal();
  if(r&&!r.error){toast('用户已创建');pageUsers()}
  else toast(r?r.error:'创建失败',false)
}

function showBatchAdd(){
  const opts=window._plans.map(p=>`<option value="${p.id}">${p.name} (${p.price}元)</option>`).join('');
  openModal(`<h3>批量添加</h3>
  <label>套餐</label><select id="mBPlan">${opts}</select>
  <label>数量</label><input id="mBCount" type="number" value="10" min="1" max="100">
  <div class="actions"><button class="btn" onclick="closeModal()">取消</button><button class="btn btn-green" onclick="doBatchAdd()">批量创建</button></div>`);
}

async function doBatchAdd(){
  const r=await apiPost('/users/batch',{plan_id:parseInt($('mBPlan').value),count:parseInt($('mBCount').value)});
  closeModal();
  if(r&&r.urls){
    const txt=r.urls.join('\n');
    openModal(`<h3>创建成功 (${r.urls.length} 个)</h3><textarea style="width:100%;height:200px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px;font-size:12px" readonly>${txt}</textarea>
    <div class="actions"><button class="btn" onclick="navigator.clipboard.writeText('${txt.replace(/'/g,"\\'")}');toast('已复制')">复制全部</button><button class="btn btn-green" onclick="closeModal();pageUsers()">关闭</button></div>`);
  }else toast(r?r.error:'创建失败',false)
}

async function showUserDetail(uid){
  const u=await apiFetch('/user/'+uid);
  if(!u)return;
  const sub=u.sub_url||'';
  openModal(`<h3>用户详情</h3>
  <div style="font-size:13px;line-height:2">
  <b>ID:</b> ${u.id}<br><b>UUID:</b> <code style="font-size:11px">${u.uuid}</code><br>
  <b>套餐:</b> ${u.plan_name||'管理员'}<br><b>状态:</b> ${statusTag(u.status)}<br>
  <b>带宽:</b> ${u.bandwidth_mbps||'无限'} Mbps<br><b>最大连接:</b> ${u.max_connections||'无限'}<br>
  <b>创建:</b> ${fmtTime(u.created_at)}<br><b>到期:</b> ${fmtTime(u.expires_at)}<br>
  <b>流量:</b> ${fmtBytes(u.traffic_used_bytes)} / ${u.traffic_limit_bytes?fmtBytes(u.traffic_limit_bytes):'无限'}<br>
  <b>上行:</b> ${fmtBytes(u.traffic_up_bytes)} <b>下行:</b> ${fmtBytes(u.traffic_down_bytes)}<br>
  <b>订阅:</b> <input value="${sub}" readonly style="width:100%;font-size:11px;margin-top:4px" onclick="this.select()">
  </div>
  <div class="actions"><button class="btn" onclick="navigator.clipboard.writeText('${sub}');toast('已复制')">复制链接</button><button class="btn btn-green" onclick="closeModal()">关闭</button></div>`);
}

async function toggleUser(uid){
  const r=await apiPost('/users/toggle/'+uid);
  if(r&&!r.error){toast('已切换');pageUsers()}
  else toast(r?r.error:'操作失败',false)
}

async function deleteUser(uid,name){
  if(!confirm(`确认删除用户 ${name} (${uid})?`))return;
  const r=await apiPost('/users/delete/'+uid);
  if(r&&r.ok){toast('已删除');pageUsers()}
  else toast('删除失败',false)
}

async function pageMonitor(){
  mc().innerHTML='<div class="page-title">实时监控</div><div id="monData">加载中...</div>';
  await loadMonitor();
  refreshTimer=setInterval(loadMonitor,5000);
}

async function loadMonitor(){
  const [on,sys]=await Promise.all([apiFetch('/online'),apiFetch('/system')]);
  if(!on||!sys)return;
  let html=`<div class="cards">
    <div class="card green"><div class="label">在线用户</div><div class="val">${on.online.length}</div></div>
    <div class="card blue"><div class="label">活跃连接</div><div class="val">${on.online.reduce((s,u)=>s+u.connections,0)}</div></div>
    <div class="card"><div class="label">CPU</div><div class="val">${sys.cpu_pct}%</div>${progBar(sys.cpu_pct)}</div>
    <div class="card"><div class="label">内存</div><div class="val">${sys.mem_pct}%</div>${progBar(sys.mem_pct)}</div>
  </div>`;
  if(on.online.length>0){
    html+='<div class="tbl-wrap"><div class="tbl-header"><h3>在线用户</h3></div><table><tr><th>用户</th><th>连接数</th><th>上行</th><th>下行</th><th>客户端IP</th></tr>';
    for(const u of on.online){
      html+=`<tr><td>${u.remark||u.uuid.slice(0,8)}</td><td>${u.connections}</td>
      <td>${fmtBytes(u.upload)}</td><td>${fmtBytes(u.download)}</td>
      <td style="font-size:11px">${(u.client_ips||[]).join(', ')}</td></tr>`;
    }
    html+='</table></div>';
  }else{
    html+='<div class="card" style="text-align:center;padding:40px;color:var(--text2)">当前无在线用户</div>';
  }
  $('monData').innerHTML=html;
}

async function pageSales(){
  mc().innerHTML='<div class="page-title">营收统计</div><div id="salesData">加载中...</div>';
  const d=await apiFetch('/sales');
  if(!d)return;
  let html=`<div class="cards">
    <div class="card green"><div class="label">今日</div><div class="val">&yen;${d.today.revenue.toFixed(2)}</div><div class="sub">${d.today.count} 单</div></div>
    <div class="card blue"><div class="label">本月</div><div class="val">&yen;${d.month.revenue.toFixed(2)}</div><div class="sub">${d.month.count} 单</div></div>
    <div class="card purple"><div class="label">累计</div><div class="val">&yen;${d.total.revenue.toFixed(2)}</div><div class="sub">${d.total.count} 单</div></div>
  </div>`;
  // By plan
  html+='<div class="cards" style="grid-template-columns:1fr 1fr"><div class="tbl-wrap"><div class="tbl-header"><h3>按套餐</h3></div><table><tr><th>套餐</th><th>销量</th><th>营收</th></tr>';
  for(const p of d.by_plan)html+=`<tr><td>${p.plan_name}</td><td>${p.count}</td><td>&yen;${p.revenue.toFixed(2)}</td></tr>`;
  html+='</table></div>';
  // By source
  html+='<div class="tbl-wrap"><div class="tbl-header"><h3>按来源</h3></div><table><tr><th>来源</th><th>销量</th><th>营收</th></tr>';
  const srcName={manual:'手动',api:'API',batch:'批量',card:'发卡',dashboard:'面板',renew:'续费'};
  for(const s of d.by_source)html+=`<tr><td>${srcName[s.source]||s.source}</td><td>${s.count}</td><td>&yen;${s.revenue.toFixed(2)}</td></tr>`;
  html+='</table></div></div>';
  // Daily chart
  if(d.daily.length>0){
    const maxR=Math.max(...d.daily.map(x=>x.revenue),1);
    html+='<div class="tbl-wrap"><div class="tbl-header"><h3>每日营收 (近30天)</h3></div><div style="padding:18px;display:flex;align-items:flex-end;gap:4px;height:200px;overflow-x:auto">';
    for(const day of d.daily){
      const h=Math.max(day.revenue/maxR*150,2);
      const dt=new Date(day.day*86400*1000);
      const label=s2(dt.getMonth()+1)+'/'+s2(dt.getDate());
      html+=`<div style="display:flex;flex-direction:column;align-items:center;min-width:24px" title="${label}: ¥${day.revenue.toFixed(2)} (${day.count}单)">
        <div style="font-size:10px;color:var(--text2);margin-bottom:4px">${day.count}</div>
        <div style="width:18px;height:${h}px;background:var(--blue);border-radius:3px 3px 0 0"></div>
        <div style="font-size:9px;color:var(--text2);margin-top:4px;writing-mode:vertical-lr">${label}</div>
      </div>`;
    }
    html+='</div></div>';
  }
  // Recent
  html+='<div class="tbl-wrap"><div class="tbl-header"><h3>近期订单</h3></div><table><tr><th>时间</th><th>用户</th><th>套餐</th><th>金额</th><th>来源</th></tr>';
  for(const s of d.recent)html+=`<tr><td>${fmtTime(s.created_at)}</td><td>${s.remark||s.user_id}</td><td>${s.plan_name}</td><td>&yen;${s.price.toFixed(2)}</td><td>${srcName[s.source]||s.source}</td></tr>`;
  html+='</table></div>';
  $('salesData').innerHTML=html;
}

async function pageLogs(){
  mc().innerHTML='<div class="page-title">操作日志</div><div id="logData">加载中...</div>';
  const d=await apiFetch('/logs');
  if(!d)return;
  let html='<div class="tbl-wrap"><table><tr><th>时间</th><th>操作</th><th>详情</th><th>操作者</th></tr>';
  for(const l of d.logs)html+=`<tr><td style="white-space:nowrap">${fmtTime(l.timestamp)}</td><td>${l.action}</td><td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;font-size:12px">${l.detail}</td><td>${l.operator}</td></tr>`;
  html+='</table></div>';
  $('logData').innerHTML=html;
}

async function pageSettings(){
  mc().innerHTML='<div class="page-title">系统设置</div><div id="setData">加载中...</div>';
  const[plans,inv]=await Promise.all([apiFetch('/plans'),apiFetch('/inventory')]);
  if(!plans)return;
  let html='';
  // Server settings
  html+=`<div class="tbl-wrap"><div class="tbl-header"><h3>服务器配置</h3><button class="btn btn-sm btn-green" onclick="saveSettings()">保存</button></div>
  <div style="padding:18px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px">
    <div><label style="font-size:12px;color:var(--text2)">服务器带宽 (Mbps)</label><input id="sBw" value="${inv?inv.server_bandwidth_mbps:2500}"></div>
    <div><label style="font-size:12px;color:var(--text2)">月流量 (TB)</label><input id="sTb" value="${inv?inv.server_monthly_traffic_tb:1}"></div>
    <div><label style="font-size:12px;color:var(--text2)">过期自动清理 (天)</label><input id="sPurge" value="7"></div>
    <div><label style="font-size:12px;color:var(--text2)">订阅端口</label><input id="sPort" value="8888"></div>
    <div><label style="font-size:12px;color:var(--text2)">API密钥</label><input id="sSecret" placeholder="用于发卡平台"></div>
  </div></div>`;
  // Plans
  html+='<div class="tbl-wrap"><div class="tbl-header"><h3>套餐配置</h3></div><table><tr><th>ID</th><th>名称</th><th>时长</th><th>流量</th><th>带宽</th><th>价格</th><th>最大连接</th><th>操作</th></tr>';
  for(const p of plans.plans){
    html+=`<tr><td>${p.id}</td><td>${p.name}</td><td>${p.duration_hours}h</td><td>${p.traffic_gb}GB</td>
    <td>${p.bandwidth_mbps}Mbps</td><td>&yen;${(p.price||0).toFixed(2)}</td><td>${p.max_connections||5}</td>
    <td><button class="btn btn-sm" onclick="editPlan(${p.id})">编辑</button></td></tr>`;
  }
  html+='</table></div>';
  // Actions
  html+=`<div class="cards" style="grid-template-columns:repeat(4,1fr)">
    <div class="card" style="text-align:center;cursor:pointer" onclick="doAction('/sync','同步sing-box')"><div class="val" style="font-size:16px;color:var(--blue)">同步 sing-box</div></div>
    <div class="card" style="text-align:center;cursor:pointer" onclick="doAction('/refresh-subs','刷新订阅')"><div class="val" style="font-size:16px;color:var(--green)">刷新订阅</div></div>
    <div class="card" style="text-align:center;cursor:pointer" onclick="doAction('/check-traffic','检查流量')"><div class="val" style="font-size:16px;color:var(--yellow)">检查流量</div></div>
    <div class="card" style="text-align:center;cursor:pointer" onclick="showChangePwd()"><div class="val" style="font-size:16px;color:var(--purple)">修改密码</div></div>
  </div>`;
  // API info
  html+=`<div class="tbl-wrap"><div class="tbl-header"><h3>发卡平台 API</h3></div>
  <div style="padding:18px;font-size:13px;line-height:2">
  <b>Webhook地址:</b> <code>http://服务器IP:订阅端口/api/create</code><br>
  <b>请求方式:</b> POST JSON<br>
  <b>请求体:</b> <code>{"secret":"API密钥","plan_id":1}</code><br>
  <b>plan_id:</b> ${plans.plans.map(p=>p.id+'='+p.name).join(', ')}<br>
  <b>返回:</b> <code>{"success":true,"sub_url":"订阅链接",...}</code>
  </div></div>`;
  $('setData').innerHTML=html;
}

async function saveSettings(){
  const body={
    server_bandwidth_mbps:$('sBw').value,
    server_monthly_traffic_tb:$('sTb').value,
    auto_purge_days:$('sPurge').value,
    sub_port:$('sPort').value,
    api_secret:$('sSecret').value,
  };
  const r=await apiPost('/settings',body);
  if(r&&r.ok)toast('设置已保存');else toast('保存失败',false)
}

function editPlan(pid){
  const p=window._plans&&window._plans.find(x=>x.id===pid);
  if(!p){apiFetch('/plans').then(d=>{window._plans=d.plans;editPlan(pid)});return}
  openModal(`<h3>编辑套餐 #${pid}</h3>
  <label>名称</label><input id="epName" value="${p.name}">
  <label>时长 (小时)</label><input id="epHours" type="number" value="${p.duration_hours}">
  <label>流量 (GB)</label><input id="epGb" type="number" value="${p.traffic_gb}">
  <label>带宽 (Mbps)</label><input id="epBw" type="number" value="${p.bandwidth_mbps}">
  <label>价格 (元)</label><input id="epPrice" type="number" step="0.01" value="${p.price||0}">
  <label>最大连接数</label><input id="epMaxConn" type="number" value="${p.max_connections||5}">
  <div class="actions"><button class="btn" onclick="closeModal()">取消</button><button class="btn btn-green" onclick="savePlan(${pid})">保存</button></div>`);
}

async function savePlan(pid){
  const r=await apiPost('/plans/update',{
    id:pid,name:$('epName').value,duration_hours:parseInt($('epHours').value),
    traffic_gb:parseFloat($('epGb').value),bandwidth_mbps:parseInt($('epBw').value),
    price:parseFloat($('epPrice').value),max_connections:parseInt($('epMaxConn').value)
  });
  closeModal();
  if(r&&r.ok){toast('套餐已更新');pageSettings()}else toast('更新失败',false)
}

async function doAction(path,name){
  if(!confirm(`确认执行: ${name}?`))return;
  const r=await apiPost(path);
  if(r&&r.ok)toast(name+'完成');else toast('操作失败',false)
}

function showChangePwd(){
  openModal(`<h3>修改管理员密码</h3>
  <label>新密码</label><input id="cpPwd" type="password" placeholder="至少6位">
  <label>确认密码</label><input id="cpPwd2" type="password">
  <div class="actions"><button class="btn" onclick="closeModal()">取消</button><button class="btn btn-green" onclick="doChangePwd()">确认</button></div>`);
}

async function doChangePwd(){
  const p1=$('cpPwd').value,p2=$('cpPwd2').value;
  if(p1.length<6){toast('密码至少6位',false);return}
  if(p1!==p2){toast('两次密码不一致',false);return}
  const r=await apiPost('/change-password',{password:p1});
  closeModal();
  if(r&&r.ok){toast('密码已修改，请重新登录');setTimeout(doLogout,2000)}
  else toast(r?r.error:'修改失败',false)
}

// ===== Init =====
document.addEventListener('DOMContentLoaded',initAuth);
document.getElementById('loginPwd').addEventListener('keydown',e=>{if(e.key==='Enter')document.getElementById('loginBtn').click()});
</script>
</body>
</html>"""
