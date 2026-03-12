"""Utility functions."""
import os
import sys
import secrets
import subprocess
import fcntl
import re
from contextlib import contextmanager
from config import SB_BIN, LOCK_FILE, MANAGER_DIR


def check_root():
    if os.geteuid() != 0:
        print("\033[33m请以root模式运行\033[0m")
        sys.exit(1)


def check_singbox():
    if not SB_BIN.exists():
        print("\033[31m未检测到 sing-box，请先运行 sb.sh 安装\033[0m")
        sys.exit(1)


def gen_uuid() -> str:
    r = subprocess.run([str(SB_BIN), "generate", "uuid"], capture_output=True, text=True, timeout=5)
    uuid = r.stdout.strip()
    if not uuid:
        raise RuntimeError("Failed to generate UUID")
    return uuid


def gen_token() -> str:
    return secrets.token_hex(16)


@contextmanager
def file_lock():
    MANAGER_DIR.mkdir(parents=True, exist_ok=True)
    f = open(LOCK_FILE, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError:
        print("\033[31m另一个实例正在运行\033[0m")
        sys.exit(1)
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def validate_uuid(s: str) -> bool:
    return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', s, re.I))


def validate_port(s: str) -> bool:
    try:
        p = int(s)
        return 1 <= p <= 65535
    except ValueError:
        return False


def bytes_to_gb(b: int) -> str:
    return f"{b / 1073741824:.2f}"


def gb_to_bytes(gb: float) -> int:
    return int(gb * 1073741824)
