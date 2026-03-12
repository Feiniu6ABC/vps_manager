#!/usr/bin/env python3
"""VPN User Subscription Manager - Entry point."""
import sys
import os

# Add script directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import MANAGER_DIR, SB_CONFIG, SB_BIN
import database as db

SCRIPT_PATH = os.path.abspath(__file__)
SHORTCUT_PATH = "/usr/bin/vpn-manager"


def check_root():
    if os.geteuid() != 0:
        print("\033[33m请以root模式运行\033[0m")
        sys.exit(1)


def install_shortcut():
    """Always ensure vpn-manager shortcut exists."""
    try:
        wrapper = f"#!/bin/bash\n/usr/bin/python3 {SCRIPT_PATH} \"$@\"\n"
        # Write or update shortcut
        need_write = True
        if os.path.exists(SHORTCUT_PATH):
            with open(SHORTCUT_PATH) as f:
                need_write = f.read() != wrapper
        if need_write:
            with open(SHORTCUT_PATH, "w") as f:
                f.write(wrapper)
            os.chmod(SHORTCUT_PATH, 0o755)
    except Exception:
        pass


def setup_cron():
    """Ensure traffic checking cron job exists."""
    cron_line = f"*/3 * * * * root /usr/bin/python3 {SCRIPT_PATH} --check >/dev/null 2>&1"
    crontab_path = "/etc/crontab"
    try:
        content = open(crontab_path).read()
        if "--check" not in content:
            with open(crontab_path, "a") as f:
                f.write(f"\n{cron_line}\n")
            print("\033[32m流量检查定时任务已设置 (每3分钟)\033[0m")
    except Exception:
        pass


def import_existing_uuid():
    """Import existing sing-box UUID on first run."""
    if not SB_CONFIG.exists():
        return
    import singbox
    cfg = singbox.load_sb_config(SB_CONFIG)
    if not cfg:
        return
    inbounds = cfg.get("inbounds", [])
    if not inbounds or not inbounds[0].get("users"):
        return
    existing_uuid = inbounds[0]["users"][0].get("uuid", "")
    if not existing_uuid:
        return
    with db.get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count > 0:
        return
    print(f"\033[33m检测到现有 UUID: {existing_uuid}\033[0m")
    ans = input("\033[33m是否导入为管理员用户？(Y/n): \033[0m")
    if not ans or ans.lower() == "y":
        from services import import_existing_uuid as do_import
        do_import(existing_uuid)
        print("\033[32m管理员用户已导入\033[0m")


def first_run_init():
    """Full initialization after sing-box is installed."""
    db.init_db()

    # Migrate from old JSON files if they exist
    if (MANAGER_DIR / "users.json").exists():
        db.migrate_from_json()
        print("\033[32m已从旧 JSON 文件迁移数据\033[0m")

    import_existing_uuid()
    setup_cron()


def main():
    check_root()
    MANAGER_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()

    # Always ensure shortcut is installed
    install_shortcut()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        # Commands that DON'T require sing-box
        if cmd == "--install":
            from cli import action_install
            action_install()
            # After install, do full init then enter menu
            if SB_BIN.exists():
                first_run_init()
                print()
                print("\033[32m安装完成！进入管理菜单...\033[0m")
                print()
                from cli import main_menu
                main_menu()
            return

        elif cmd == "--uninstall":
            from installer import uninstall
            confirm = input("\033[31m确认卸载 sing-box 和 vpn-manager？所有数据将被备份。(y/N): \033[0m")
            if confirm.lower() == "y":
                uninstall()
            return

        # Commands that require sing-box
        if not SB_BIN.exists():
            print("\033[31m未检测到 sing-box，请先执行: python3 main.py --install\033[0m")
            sys.exit(1)

        if cmd == "--sync":
            from services import sync_to_singbox
            sync_to_singbox()
        elif cmd == "--check":
            from services import check_traffic
            check_traffic()
        elif cmd == "--gen-subs":
            from services import generate_all_subs
            generate_all_subs()
        elif cmd == "--init":
            first_run_init()
        elif cmd == "--server":
            from server import run_server
            port = int(db.get_config("sub_port", "8888"))
            run_server(port)
        elif cmd == "--upgrade":
            from installer import upgrade_singbox
            ver = sys.argv[2] if len(sys.argv) > 2 else ""
            upgrade_singbox(ver)
        elif cmd == "--set-admin-password":
            from dashboard import hash_password
            import getpass
            pwd = getpass.getpass("设置管理员密码: ")
            if len(pwd) < 6:
                print("\033[31m密码至少6位\033[0m")
                sys.exit(1)
            pwd2 = getpass.getpass("确认密码: ")
            if pwd != pwd2:
                print("\033[31m两次密码不一致\033[0m")
                sys.exit(1)
            db.set_config("admin_password", hash_password(pwd))
            print("\033[32m管理员密码已设置\033[0m")
        elif cmd == "--status":
            from installer import service_status, get_installed_version
            ver = get_installed_version()
            status = service_status()
            color = "\033[32m" if status == "active" else "\033[31m"
            print(f"sing-box v{ver}: {color}{status}\033[0m")
        else:
            print(f"未知命令: {cmd}")
            print("用法: vpn-manager [--install|--uninstall|--upgrade|--sync|--check|")
            print("                   --gen-subs|--server|--set-admin-password|--status]")
            sys.exit(1)
    else:
        # Interactive menu
        if not SB_BIN.exists():
            print("\033[33m未检测到 sing-box，开始安装...\033[0m")
            print()
            from cli import action_install
            action_install()
            if not SB_BIN.exists():
                sys.exit(1)

        first_run_init()
        from cli import main_menu
        main_menu()


if __name__ == "__main__":
    main()
