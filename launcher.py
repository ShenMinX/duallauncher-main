import subprocess
import sys
import time
import socket
import ctypes
from ctypes import wintypes  # Still used for window enumeration callbacks
import webbrowser
import threading
import signal
import os
from pathlib import Path
import re
from urllib.parse import urlparse
import json
import platform

# Base directory (absolute) to make paths robust regardless of current working directory.
# When packaged as a single-file EXE (PyInstaller), use the executable's directory.
def _resolve_base_dir() -> Path:
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
    except Exception:
        pass
    return Path(__file__).resolve().parent

BASE_DIR = _resolve_base_dir()

# Configuration
PING_URL = "127.0.0.0"
# CHART_URL = "http://127.0.0.1:5000"  # Flask default
CHART_URL = "http://122.224.243.126:8011/%E7%9F%A2%E9%87%8Fweb%E6%B5%B7%E5%9B%BE%E5%BC%80%E5%8F%91ing.html"
CHART_EXE = BASE_DIR / "apps" / "chart.exe"
CTRL_EXE = BASE_DIR / "apps" / "usv_remote_ctrl.exe"
CHART_BROWSER = "edge"  # prefer Edge for fullscreen/app-mode
WAIT_PORT = 5000
PORT_TIMEOUT = 30  # seconds
START_BROWSER_DELAY = 1.0  # additional delay after port open
PING_RETRY_INTERVAL = 2  # seconds between checks
PING_TOTAL_TIMEOUT = 0   # 0 means wait forever; set >0 to limit wait time
CONFIG_PATH = BASE_DIR / "apps" / "config.json"
MULTIMON_EXE = BASE_DIR / "MultiMonitorTool.exe"
# MultiMonitorTool monitor numbers are typically 1-based. Configure desired targets here.
MULTIMON_CTRL_MONITOR = 2   # Move USV remote control to monitor #2
MULTIMON_CHART_MONITOR = 3  # Move Chart browser window to monitor #3
CHART_WINDOW_TITLE = "矢量web海图"  # Used for MultiMonitorTool Title matching
CHART_WINDOW_TITLE_ALT = "web海图"   # Alternate substring in case the full title differs
WINDOW_WAIT_TIMEOUT_USV = 20.0
WINDOW_POLL_INTERVAL = 0.25
USV_WINDOW_TITLE = "无人船远程视频控制 - 白色主题"
# Partial title to catch variations (e.g., different theme suffixes)
USV_WINDOW_TITLE_PART = "无人船远程视频控制"
SW_RESTORE = 9  # Minimal show/restore constant for ensuring window is not minimized before moving

# Globals for cleanup
_processes = []
_browser_opened = False
_browser_process = None

# Win32 helpers for minimal window control (F11 to toggle fullscreen)
VK_F11 = 0x7A
KEYEVENTF_KEYUP = 0x0002

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, ctypes.c_void_p)

def _enum_browser_windows(target_pid):
    matches = []
    def callback(hwnd, lParam):
        # Skip invisible or minimized
        if not user32.IsWindowVisible(hwnd):
            return True
        # Check process id
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != target_pid:
            return True
        # Basic heuristic: accept any visible top-level window of the process
        matches.append(hwnd)
        return True
    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return matches


def _enum_windows_for_pid(target_pid: int):
    """Enumerate visible top-level windows for the given process id."""
    return _enum_browser_windows(target_pid)


def wait_for_window_of_process(pid: int, timeout: float = 60.0, poll: float = 0.5) -> bool:
    """Wait until the process with pid has at least one visible top-level window."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            wins = _enum_windows_for_pid(pid)
            if wins:
                return True
        except Exception:
            pass
        time.sleep(poll)
    return False


def dump_visible_window_titles(filter_pid: int | None = None):
    """Print visible top-level window titles (optionally only for a given pid)."""
    def callback(hwnd, lParam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if filter_pid is not None:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != filter_pid:
                return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if title:
            print(f"Window: {title}")
        return True
    user32.EnumWindows(EnumWindowsProc(callback), 0)

def enforce_fullscreen_async():
    """Spawn thread to wait for browser window then toggle fullscreen and move via MultiMonitorTool."""
    def worker():
        if _browser_process is None:
            return
        pid = _browser_process.pid if hasattr(_browser_process, 'pid') else None
        # Wait up to 10 seconds for a window
        deadline = time.time() + 10
        hwnd = None
        while time.time() < deadline and hwnd is None:
            if pid:
                wins = _enum_browser_windows(pid)
                if wins:
                    hwnd = wins[0]
                    break
            time.sleep(0.5)
        if hwnd is None:
            return
        # Try to force true fullscreen by sending F11
        try:
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.2)
            user32.keybd_event(VK_F11, 0, 0, 0)
            user32.keybd_event(VK_F11, 0, KEYEVENTF_KEYUP, 0)
        except Exception:
            pass

        # Additionally, use MultiMonitorTool to move by Title to desired monitor
        try:
            # First try moving any Edge window by process name (useful if title not ready or warning page shown)
            move_window_with_multimonitor(MULTIMON_CHART_MONITOR, kind="Process", value="msedge.exe", retries=20, delay=0.5)
            # Then try by title for a while to catch when page title appears
            if not move_window_with_multimonitor(MULTIMON_CHART_MONITOR, kind="Title", value=CHART_WINDOW_TITLE, retries=40, delay=0.5):
                move_window_with_multimonitor(MULTIMON_CHART_MONITOR, kind="Title", value=CHART_WINDOW_TITLE_ALT, retries=20, delay=0.5)
        except Exception:
            pass
    threading.Thread(target=worker, daemon=True).start()


def move_window_with_multimonitor(monitor_number: int, kind: str, value: str, retries: int = 8, delay: float = 0.5):
    """Use MultiMonitorTool.exe to move a window to a monitor.

    kind: 'Process' or 'Title' (see NirSoft MultiMonitorTool docs)
    value: process name (e.g., 'usv_remote_ctrl.exe') or partial/exact title
    """
    if not MULTIMON_EXE.exists():
        print(f"MultiMonitorTool not found at {MULTIMON_EXE}")
        return False
    print(f"[MMT] Move request: monitor={monitor_number}, {kind}={value}, retries={retries}, delay={delay}s")
    ok = False
    for _ in range(max(1, retries)):
        try:
            cmd = [str(MULTIMON_EXE), "/MoveWindow", str(monitor_number), kind, value]
            res = subprocess.run(cmd, cwd=str(BASE_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                ok = True
                break
        except Exception:
            pass
        time.sleep(delay)
    if not ok:
        print(f"MultiMonitorTool move failed for {kind}={value} to monitor {monitor_number}")
    else:
        print(f"[MMT] Move success: {kind}={value} -> monitor {monitor_number}")
    return ok


def wait_for_port(port: int, host: str = "127.0.0.1", timeout: int = PORT_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            try:
                s.connect((host, port))
                return True
            except OSError:
                time.sleep(0.5)
    return False


def check_redis_connectivity(host: str, port: int, timeout: float = 2.0) -> bool:
    """Attempt a Redis PING using RESP protocol; returns True on +PONG."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            # RESP array of 1 bulk string: PING
            s.sendall(b"*1\r\n$4\r\nPING\r\n")
            s.settimeout(timeout)
            data = s.recv(256)
            if not data:
                return False
            # Expect +PONG\r\n
            return data.startswith(b"+PONG")
    except OSError:
        return False


def _can_reach(ping_target: str) -> bool:
    """Return True if ping_target is reachable.

    Preference order:
    - If target is redis://host[:port], perform Redis PING handshake.
    - If target is tcp://host:port, attempt a raw TCP connect to host:port.
    - If target is http(s)://, attempt TCP connect to its host:port.
    - If target is ping://host, perform an ICMP ping via OS ping command.
    - If target has no scheme, prefer a simple ICMP ping to the host; if of the form host:port, fall back to Redis PING (backward compatibility).
    """
    try:
        parsed = urlparse(ping_target)
        if parsed.scheme == "redis":
            host = parsed.hostname
            port = parsed.port or 6379
            if not host:
                return False
            return check_redis_connectivity(host, port)
        if parsed.scheme == "ping":
            host = parsed.hostname or ping_target.replace("ping://", "", 1)
            if not host:
                return False
            return ping_host(host)
        if parsed.scheme == "tcp":
            host = parsed.hostname
            port = parsed.port
            if not host or not port:
                return False
            try:
                with socket.create_connection((host, port), timeout=2):
                    return True
            except OSError:
                return False
        if parsed.scheme in ("http", "https"):
            host = parsed.hostname
            if not host:
                return False
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            try:
                with socket.create_connection((host, port), timeout=2):
                    return True
            except OSError:
                return False
        # No scheme
        # If pure host/ip, prefer ICMP ping
        if ":" not in ping_target:
            return ping_host(ping_target)
        # host:port retained for backward compatibility with Redis checks
        host, p = ping_target.split(":", 1)
        try:
            port = int(p)
        except Exception:
            port = 6379
        return check_redis_connectivity(host, port)
    except Exception:
        return False


def ping_host(host: str, count: int = 1, timeout: int = 2) -> bool:
    """Ping a host using the OS ping command.

    On Windows, use `ping -n <count> -w <timeout_ms> <host>`.
    Returns True if exit code is 0.
    """
    try:
        system = platform.system().lower()
        if system.startswith("windows"):
            # timeout in ms; -w is per-echo timeout on Windows
            cmd = ["ping", "-n", str(count), "-w", str(timeout * 1000), host]
        else:
            # For completeness on other platforms
            cmd = ["ping", "-c", str(count), "-W", str(timeout), host]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except Exception:
        return False


def wait_for_connectivity(ping_target: str, total_timeout: int = PING_TOTAL_TIMEOUT, interval: int = PING_RETRY_INTERVAL, cancel_event: object | None = None) -> bool:
    """Block until ping_target is reachable or timeout elapses. Returns True if reachable.

    If cancel_event (threading.Event) is provided and set(), the wait is aborted and returns False.
    """
    print(f"Checking connectivity to {ping_target}...")
    start = time.time()
    attempt = 0
    while True:
        # cancellation support
        try:
            if cancel_event is not None and getattr(cancel_event, 'is_set', lambda: False)():
                print("Connectivity check canceled.")
                return False
        except Exception:
            pass
        attempt += 1
        if _can_reach(ping_target):
            print(f"Connectivity OK to {ping_target}.")
            return True
        if total_timeout and (time.time() - start) >= total_timeout:
            print(f"Connectivity check timed out after {total_timeout}s for {ping_target}.")
            return False
        time.sleep(interval)


def open_browser_fullscreen(url: str):
    global _browser_opened, _browser_process
    # Build Edge commands (avoid kiosk/InPrivate; use app mode + dedicated profile)
    browser_cmds = []
    if CHART_BROWSER.lower() in ("edge", "msedge", ""):
        edge_paths = [
            "msedge",
            "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
            "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
        ]
        profile_dir = BASE_DIR / "edge_profile"
        common = [
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--disable-first-run-ui",
            # Make sure HTTPS-only / private network blocks don't interfere for HTTP URLs
            "--disable-features=BlockInsecurePrivateNetworkRequests,EdgeHttpsOnlyMode,HttpsUpgrades",
        ]
        for edge in edge_paths:
            # App mode fullscreen (preferred)
            browser_cmds.append([
                edge,
                f"--app={url}",
                "--start-fullscreen",
                *common,
            ])
            # Plain url with fullscreen
            browser_cmds.append([
                edge,
                url,
                "--start-fullscreen",
                *common,
            ])

    for cmd in browser_cmds:
        try:
            print(f"Attempting to launch browser with command: {' '.join(cmd)}")
            _browser_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _browser_opened = True
            try:
                # Track the browser like other child processes so terminate_children() will close it
                _processes.append(_browser_process)
            except Exception:
                pass
            print(f"Launched browser with command: {' '.join(cmd)}")
            enforce_fullscreen_async()
            return
        except FileNotFoundError:
            continue
    # Fallback
    print("Falling back to default system browser (manual fullscreen may be required).")
    webbrowser.open(url)
    _browser_opened = True


def launch_process(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"Executable not found: {path}")
    env = os.environ.copy()
    p = subprocess.Popen([str(path)], cwd=str(path.parent), env=env)
    _processes.append(p)
    # If this is the USV control app, wait for its window, then move via MultiMonitorTool
    try:
        if path.name.lower() == "usv_remote_ctrl.exe":
            def _move_ctrl():
                # Wait explicitly for a window to show up
                waited_ok = wait_for_window_of_process(p.pid, timeout=WINDOW_WAIT_TIMEOUT_USV, poll=WINDOW_POLL_INTERVAL)
                if not waited_ok:
                    print(f"USV window did not appear within {WINDOW_WAIT_TIMEOUT_USV}s; will still attempt move via MultiMonitorTool.")
                    try:
                        print("Visible windows (for diagnostics):")
                        dump_visible_window_titles(p.pid)
                    except Exception:
                        pass
                # Try to restore the window (if minimized) before moving
                try:
                    wins = _enum_windows_for_pid(p.pid)
                    if wins:
                        user32.ShowWindow(wins[0], SW_RESTORE)
                        try:
                            user32.SetForegroundWindow(wins[0])
                        except Exception:
                            pass
                except Exception:
                    pass
                # First try moving by process name
                ok = move_window_with_multimonitor(MULTIMON_CTRL_MONITOR, "Process", "usv_remote_ctrl.exe", retries=40, delay=0.5)
                # Try without .exe as some tools accept bare process name
                if not ok:
                    ok = move_window_with_multimonitor(MULTIMON_CTRL_MONITOR, "Process", "usv_remote_ctrl", retries=20, delay=0.5)
                # Then try by known window title as a fallback
                if not ok:
                    ok = move_window_with_multimonitor(MULTIMON_CTRL_MONITOR, "Title", USV_WINDOW_TITLE, retries=40, delay=0.5)
                # Finally, try by partial title in case theme suffix differs
                if not ok:
                    move_window_with_multimonitor(MULTIMON_CTRL_MONITOR, "Title", USV_WINDOW_TITLE_PART, retries=40, delay=0.5)
            threading.Thread(target=_move_ctrl, daemon=True).start()
    except Exception:
        pass
    return p


def graceful_shutdown(*_):
    print("Shutting down...")
    terminate_children()
    sys.exit(0)


def terminate_children():
    """Terminate any child processes we launched."""
    for p in list(_processes):
        terminate_process_tree(p)
    # Clear out dead processes
    for p in list(_processes):
        if p.poll() is not None:
            try:
                _processes.remove(p)
            except Exception:
                pass
    # Also make sure browser process is gone, even if it wasn't tracked earlier
    global _browser_process, _browser_opened
    try:
        if _browser_process and _browser_process.poll() is None:
            terminate_process_tree(_browser_process)
    except Exception:
        pass
    _browser_process = None
    _browser_opened = False

def terminate_process_tree(proc: subprocess.Popen):
    """Terminate a process and its children on Windows using taskkill."""
    if not proc:
        return
    try:
        if proc.poll() is not None:
            return
        pid = proc.pid
        # Use taskkill to terminate the process tree forcefully
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        # Fallback to terminate/kill
        try:
            proc.terminate()
        except Exception:
            pass
        time.sleep(1)
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


def start_sequence(start_ctrl: bool = True, start_chart_backend: bool = False, open_chart_browser: bool = True):
    """Programmatic start for GUI: launch apps and/or browser according to flags."""
    ctrl_proc = None
    chart_proc = None
    if start_ctrl:
        print("Launching USV remote control application...")
        ctrl_proc = launch_process(CTRL_EXE)
    if start_chart_backend:
        print("Launching chart (Flask) backend...")
        chart_proc = launch_process(CHART_EXE)
        print(f"Waiting for port {WAIT_PORT}...")
        if wait_for_port(WAIT_PORT):
            print("Port is open.")
        else:
            print(f"Timeout waiting for port {WAIT_PORT}.")
    if open_chart_browser:
        time.sleep(START_BROWSER_DELAY)
        open_browser_fullscreen(CHART_URL)
    return ctrl_proc, chart_proc


def main():
    signal.signal(signal.SIGINT, graceful_shutdown)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, graceful_shutdown)

    # Load config.json (if present) and determine ping target (redis host:port or ping_url)
    ping_target = PING_URL
    try:
        if CONFIG_PATH.exists():
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                cfg = json.load(f)
            # prefer explicit ping_url if present
            if isinstance(cfg, dict) and cfg.get("ping_url"):
                ping_target = cfg.get("ping_url")
            elif isinstance(cfg, dict) and cfg.get("redis"):
                r = cfg.get("redis")
                host = r.get("host", "127.0.0.1")
                port = r.get("port", 6379)
                ping_target = f"{host}:{port}"
    except Exception:
        pass

    # Ensure network/host is reachable before launching
    if not wait_for_connectivity(ping_target, PING_TOTAL_TIMEOUT, PING_RETRY_INTERVAL):
        print(f"Connectivity not available to {ping_target}; aborting launch.")
        return

    print("Launching USV remote control application...")
    ctrl_proc = launch_process(CTRL_EXE)

    # print("Launching chart (Flask) backend...")
    # chart_proc = launch_process(CHART_EXE)

    # print(f"Waiting for port {WAIT_PORT}...")
    # if wait_for_port(WAIT_PORT):
    #     print("Port is open. Launching browser...")
    #     time.sleep(START_BROWSER_DELAY)
    #     open_browser_fullscreen(CHART_URL, TARGET_MONITOR_FOR_CHART)
    # else:
    #     print(f"Timeout waiting for port {WAIT_PORT}. Browser will still be attempted.")
    #     open_browser_fullscreen(CHART_URL, TARGET_MONITOR_FOR_CHART)
    open_browser_fullscreen(CHART_URL)

    # Wait for either process to exit
    try:
        while True:
            if ctrl_proc.poll() is not None:
                print("USV remote control exited.")
                break
            time.sleep(1)
    finally:
        graceful_shutdown()


if __name__ == "__main__":
    main()
