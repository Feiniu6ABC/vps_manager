"""HTTP subscription server + webhook API + admin dashboard."""
import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import database as db
import services
from config import SUBS_DIR


class RequestHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        # Admin dashboard routes
        if self.path.startswith("/admin"):
            from dashboard import handle_admin_request
            handle_admin_request(self, "GET", self.path)
            return

        parts = self.path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "sub":
            self.handle_subscription(parts[1])
        elif len(parts) == 1 and parts[0] == "status":
            self.handle_status()
        else:
            self.send_error(404)

    def do_POST(self):
        # Admin dashboard routes
        if self.path.startswith("/admin"):
            from dashboard import handle_admin_request
            handle_admin_request(self, "POST", self.path)
            return

        parts = self.path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "api" and parts[1] == "create":
            self.handle_api_create()
        else:
            self.send_error(404)

    def handle_subscription(self, token: str):
        user = services.get_user_by_token(token)
        if not user:
            self.send_error(404, "Not found")
            return

        if user["status"] != "active":
            self.send_error(403, "Subscription inactive")
            return

        now = time.time()
        if user["expires_at"] < now:
            self.send_error(403, "Expired")
            return

        limit = user["traffic_limit_bytes"]
        if limit > 0 and user["traffic_used_bytes"] >= limit:
            self.send_error(403, "Traffic limit exceeded")
            return

        sub_file = SUBS_DIR / f"{token}.txt"
        if not sub_file.exists():
            services.generate_user_sub(user["id"])
            if not sub_file.exists():
                self.send_error(404, "Subscription file not found")
                return

        content = sub_file.read_text()

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Profile-Update-Interval", "6")
        self.send_header("Content-Disposition", "attachment; filename=subscription")

        upload = user.get("traffic_up_bytes", 0)
        download = user.get("traffic_down_bytes", 0)
        total = limit if limit > 0 else 1099511627776
        expire = int(user["expires_at"])
        self.send_header(
            "subscription-userinfo",
            f"upload={upload}; download={download}; total={total}; expire={expire}"
        )

        self.end_headers()
        self.wfile.write(content.encode())

    def handle_api_create(self):
        """Webhook for card selling platforms."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length > 0 else {}
        except Exception:
            self.send_json(400, {"success": False, "error": "Invalid JSON"})
            return

        api_secret = db.get_config("api_secret", "")
        if not api_secret or body.get("secret") != api_secret:
            self.send_json(403, {"success": False, "error": "Invalid secret"})
            return

        plan_id = body.get("plan_id", 1)
        remark = body.get("remark", "")

        can_sell, reason = services.check_can_sell(plan_id)
        if not can_sell:
            self.send_json(409, {"success": False, "error": reason})
            return

        try:
            user = services.add_user(plan_id, remark or f"API-{plan_id}", source="api")
        except Exception as e:
            self.send_json(500, {"success": False, "error": str(e)})
            return

        sub_url = services.get_sub_url(user["token"])
        expire_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(user["expires_at"]))

        self.send_json(200, {
            "success": True,
            "sub_url": sub_url,
            "user_id": user["id"],
            "plan": services.get_plan(plan_id)["name"],
            "traffic_gb": user["traffic_gb"],
            "bandwidth_mbps": user["bandwidth_mbps"],
            "expires": expire_str,
            "expires_ts": user["expires_at"],
        })

    def handle_status(self):
        inv = services.get_inventory_status()
        self.send_json(200, {
            "active_users": inv["active_users"],
            "bw_utilization_pct": inv["bw_utilization_pct"],
            "plans_available": {
                str(k): v["available"] for k, v in inv["plan_capacity"].items()
            },
        })

    def send_json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class ThreadedHTTPServer(HTTPServer):
    allow_reuse_address = True

    def process_request(self, request, client_address):
        t = threading.Thread(target=self._handle, args=(request, client_address))
        t.daemon = True
        t.start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


def run_server(port: int = 8888):
    server = ThreadedHTTPServer(("0.0.0.0", port), RequestHandler)
    print(f"Subscription server running on port {port} (admin: http://0.0.0.0:{port}/admin)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
