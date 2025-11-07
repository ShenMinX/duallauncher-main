import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from pathlib import Path
import json
import threading
import subprocess
import time
import sys
import socket
import urllib.request
import urllib.error

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

"""
Simplified cross-platform launcher GUI.
Removes process/title matching and window moving logic (MultiMonitorTool dependent).
Each profile describes an executable to run with optional arguments. Optional fields:
  - group: string tag to group related apps
  - order: integer launch order within the group (lower first)
  - path: absolute/relative path to executable/script
  - args: optional argument string (split on whitespace)
  - autoStart: bool, whether to auto start on app launch
  - autoRestart: bool, if process exits unexpectedly, restart
  - waitTarget: optional connectivity target before launching; examples:
        http://localhost:8080
        https://example.com/health
        127.0.0.1:6379   (TCP host:port)
  - waitTimeout: total seconds to wait for connectivity (0 => no wait)
  - waitInterval: interval seconds between probes (default 2)

Launch ordering: "Start All" will sort profiles by (group, order, name).
Connectivity column reflects current reachability of waitTarget if defined.
Status column shows Running / Stopped / Starting.

Config file reused: launch.conf (JSON) with root object including key "profiles".
Existing legacy fields from the original launcher are ignored.

Run:
    python gui_launcher_simple.py

Pack (example):
    pyinstaller --onefile --name SimpleLauncher gui_launcher_simple.py
"""

# Helper: determine base dir (support PyInstaller frozen executable)
def _resolve_base_dir() -> Path:
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
    except Exception:
        pass
    return Path(__file__).resolve().parent

BASE_DIR = _resolve_base_dir()
CONF_PATH = BASE_DIR / "launch.conf"

# ---------------- Persistence -----------------

def load_profiles():
    if not CONF_PATH.exists():
        return {"profiles": []}
    try:
        with CONF_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"profiles": []}
        data.setdefault("profiles", [])
        normalized = []
        for raw in data.get("profiles", []):
            if not isinstance(raw, dict):
                continue
            p = {}
            p["name"] = raw.get("name") or raw.get("value") or raw.get("path") or "Unnamed"
            p["group"] = str(raw.get("group", "")).strip()
            try:
                p["order"] = int(raw.get("order", 0) or 0)
            except Exception:
                p["order"] = 0
            p["path"] = str(raw.get("path", "")).strip()
            p["args"] = str(raw.get("args", "")).strip()
            p["autoStart"] = bool(raw.get("autoStart", False))
            p["autoRestart"] = bool(raw.get("autoRestart", False))
            # connectivity
            p["waitTarget"] = str(raw.get("waitTarget", "")).strip()
            try:
                p["waitTimeout"] = int(raw.get("waitTimeout", 0) or 0)
            except Exception:
                p["waitTimeout"] = 0
            try:
                p["waitInterval"] = int(raw.get("waitInterval", 2) or 2)
            except Exception:
                p["waitInterval"] = 2
            try:
                p["postLaunchDelay"] = int(raw.get("postLaunchDelay", 0) or 0)
            except Exception:
                p["postLaunchDelay"] = 0
            normalized.append(p)
        data["profiles"] = normalized
        return data
    except Exception:
        return {"profiles": []}


def save_profiles(data: dict):
    data = data or {"profiles": []}
    data.setdefault("profiles", [])
    with CONF_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ---------------- Connectivity -----------------

def _is_http_target(target: str) -> bool:
    return target.startswith("http://") or target.startswith("https://")


def _is_tcp_target(target: str) -> bool:
    if ":" not in target:
        return False
    host, _, port = target.partition(":")
    if not host or not port.isdigit():
        return False
    return True


def _can_reach_http(url: str, timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except urllib.error.HTTPError as e:
        # HTTP error codes still indicate server reachable
        return 200 <= getattr(e, "code", 0) < 500
    except Exception:
        return False


def _can_reach_tcp(target: str, timeout: float = 3.0) -> bool:
    host, _, port = target.partition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def can_reach(target: str) -> bool:
    if not target:
        return False
    if _is_http_target(target):
        return _can_reach_http(target)
    if _is_tcp_target(target):
        return _can_reach_tcp(target)
    # Unknown scheme: try TCP assuming host:port; else fail
    return False


def wait_for_connectivity(target: str, total_timeout: int, interval: int) -> bool:
    if not target or total_timeout <= 0:
        return True  # Nothing to wait for
    deadline = time.time() + total_timeout
    while time.time() < deadline:
        if can_reach(target):
            return True
        time.sleep(max(0.2, interval))
    return can_reach(target)

# ---------------- Profile Editor -----------------

class ProfileEditor(tk.Toplevel):
    def __init__(self, master, profile=None):
        super().__init__(master)
        self.title("Edit Profile (Simple)")
        self.resizable(False, False)
        self.profile = profile or {}
        self.result = None

        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        self.var_name = tk.StringVar(value=self.profile.get("name", ""))
        self.var_group = tk.StringVar(value=self.profile.get("group", ""))
        self.var_order = tk.StringVar(value=str(self.profile.get("order", 0)))
        self.var_path = tk.StringVar(value=self.profile.get("path", ""))
        self.var_args = tk.StringVar(value=self.profile.get("args", ""))
        self.var_wait = tk.StringVar(value=self.profile.get("waitTarget", ""))
        self.var_wait_timeout = tk.StringVar(value=str(self.profile.get("waitTimeout", 0)))
        self.var_wait_interval = tk.StringVar(value=str(self.profile.get("waitInterval", 2)))
        self.var_post_delay = tk.StringVar(value=str(self.profile.get("postLaunchDelay", 0)))
        self.var_auto = tk.IntVar(value=1 if bool(self.profile.get("autoStart", False)) else 0)
        self.var_restart = tk.IntVar(value=1 if bool(self.profile.get("autoRestart", False)) else 0)

        row = 0
        ttk.Label(frm, text="Name:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(frm, textvariable=self.var_name, width=30).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(frm, text="Group (optional):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(frm, textvariable=self.var_group, width=20).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(frm, text="Order (integer):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(frm, textvariable=self.var_order, width=10).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(frm, text="Executable Path:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        path_wrap = ttk.Frame(frm)
        path_wrap.grid(row=row, column=1, sticky="w")
        ttk.Entry(path_wrap, textvariable=self.var_path, width=30).pack(side="left")
        ttk.Button(path_wrap, text="Browse...", command=self._browse_path).pack(side="left", padx=(6, 0))
        row += 1
        ttk.Label(frm, text="Arguments (optional):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(frm, textvariable=self.var_args, width=30).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(frm, text="Wait Target (optional):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(frm, textvariable=self.var_wait, width=30).grid(row=row, column=1, sticky="w")
        row += 1
        sub = ttk.Frame(frm)
        sub.grid(row=row, column=1, sticky="w")
        ttk.Label(sub, text="Timeout (s):").pack(side="left")
        ttk.Entry(sub, textvariable=self.var_wait_timeout, width=8).pack(side="left", padx=(0, 8))
        ttk.Label(sub, text="Interval (s):").pack(side="left")
        ttk.Entry(sub, textvariable=self.var_wait_interval, width=8).pack(side="left")
        row += 1
        ttk.Label(frm, text="Post-Launch Delay (s, group only):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(frm, textvariable=self.var_post_delay, width=10).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Checkbutton(frm, text="Auto Start", variable=self.var_auto).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Checkbutton(frm, text="Auto Restart on Crash", variable=self.var_restart).grid(row=row, column=1, sticky="w")
        row += 1
        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="OK", command=self.on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=4)

        self.grab_set()
        self.transient(master)

    def _browse_path(self):
        initdir = str(BASE_DIR)
        current = (self.var_path.get() or "").strip()
        try:
            if current:
                p = Path(current)
                if p.exists():
                    initdir = str(p.parent)
                elif p.parent and p.parent.exists():
                    initdir = str(p.parent)
        except Exception:
            pass
        filename = filedialog.askopenfilename(title="Select Executable", initialdir=initdir)
        if filename:
            self.var_path.set(filename)

    def on_ok(self):
        name = self.var_name.get().strip() or self.var_path.get().strip()
        if not name:
            messagebox.showerror("Error", "Please enter a name or executable path.")
            return
        path = self.var_path.get().strip()
        if not path:
            messagebox.showerror("Error", "Please enter the executable path.")
            return
        try:
            order = int((self.var_order.get() or "0").strip())
        except Exception:
            order = 0
        wait_target = self.var_wait.get().strip()
        try:
            wt_timeout = int((self.var_wait_timeout.get() or "0").strip())
        except Exception:
            wt_timeout = 0
        try:
            wt_interval = int((self.var_wait_interval.get() or "2").strip())
        except Exception:
            wt_interval = 2
        try:
            post_delay = int((self.var_post_delay.get() or "0").strip())
        except Exception:
            post_delay = 0
        self.result = {
            "name": name,
            "group": self.var_group.get().strip(),
            "order": order,
            "path": path,
            "args": self.var_args.get().strip(),
            "waitTarget": wait_target,
            "waitTimeout": wt_timeout,
            "waitInterval": wt_interval,
            "postLaunchDelay": post_delay,
            "autoStart": bool(int(self.var_auto.get() or 0)),
            "autoRestart": bool(int(self.var_restart.get() or 0)),
        }
        self.destroy()

# ---------------- Main Application -----------------

class SimpleLauncherApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Simple Launcher (Cross-Platform)")
        self.geometry("820x440")
        self.resizable(True, True)

        self.data = load_profiles()
        # defaults for new group auto-start override settings
        self.data.setdefault("autoStartGroups", [])
        self.data.setdefault("autoStartGroupsOverride", False)
        # Redis settings
        self.data.setdefault("redisHost", "localhost")
        self.data.setdefault("redisPort", 6379)
        self.data.setdefault("redisDb", 0)
        self.data.setdefault("redisPassword", "")
        # Group modes: {group_name: {"mode": "on"|"off"|"redis", "redisKey": "key:field"}}
        self.data.setdefault("groupModes", {})
        self.processes = {}           # name -> subprocess.Popen
        self.status_map = {}          # name -> Running / Stopped / Starting
        self.conn_status = {}         # name -> Online / Offline / - / Waiting...
        self._stop_event = threading.Event()
        self._user_stop_flags = {}    # name -> True if user manually stopped
        self._launching = set()       # names currently launching
        self._last_restart = {}       # name -> last restart time
        self._redis_client = None
        self._redis_monitor_running = False

        self._build_menu()
        self._build_main()
        self._load_table()

        # Auto start after short delay
        self.after(200, self._auto_start_profiles)
        # Background connectivity monitor
        threading.Thread(target=self._conn_monitor, daemon=True).start()
        # Background process monitor (autoRestart)
        threading.Thread(target=self._proc_monitor, daemon=True).start()
        # Start Redis monitor if configured
        self.after(500, self._start_redis_monitor)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- UI Construction ----
    def _build_menu(self):
        m = tk.Menu(self)
        self.config(menu=m)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="Add Profile", command=self.on_add)
        fm.add_command(label="Edit Selected", command=self.on_edit)
        fm.add_command(label="Delete Selected", command=self.on_delete)
        fm.add_separator()
        fm.add_command(label="Save to launch.conf", command=self.on_save)
        fm.add_separator()
        fm.add_command(label="Group Launcher...", command=self.open_group_launcher)
        m.add_cascade(label="Config", menu=fm)

    def _build_main(self):
        top = ttk.Frame(self)
        top.pack(fill="both", expand=True, padx=10, pady=10)

        cols = ("name", "group", "order", "path", "auto", "conn", "status")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", height=14)
        self.tree.heading("name", text="Name")
        self.tree.heading("group", text="Group")
        self.tree.heading("order", text="Order")
        self.tree.heading("path", text="Path")
        self.tree.heading("auto", text="Auto")
        self.tree.heading("conn", text="Conn")
        self.tree.heading("status", text="Status")

        self.tree.column("name", width=130)
        self.tree.column("group", width=90)
        self.tree.column("order", width=60, anchor="center")
        self.tree.column("path", width=250)
        self.tree.column("auto", width=60, anchor="center")
        self.tree.column("conn", width=70, anchor="center")
        self.tree.column("status", width=80, anchor="center")

        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(top, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")

        # Top buttons
        cfgops = ttk.Frame(self)
        cfgops.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(cfgops, text="Add Profile", command=self.on_add).pack(side="left", padx=4)
        ttk.Button(cfgops, text="Edit Selected", command=self.on_edit).pack(side="left", padx=4)
        ttk.Button(cfgops, text="Delete Selected", command=self.on_delete).pack(side="left", padx=4)
        ttk.Button(cfgops, text="Save to launch.conf", command=self.on_save).pack(side="left", padx=12)

        # Popup menu
        self._popup = tk.Menu(self, tearoff=0)
        self._popup.add_command(label="Add Profile", command=self.on_add)
        self._popup.add_command(label="Edit Selected", command=self.on_edit)
        self._popup.add_command(label="Delete Selected", command=self.on_delete)
        self._popup.add_separator()
        self._popup.add_command(label="Save to launch.conf", command=self.on_save)

        def on_right_click(event):
            try:
                row = self.tree.identify_row(event.y)
                if row:
                    self.tree.selection_set(row)
                self._popup.tk_popup(event.x_root, event.y_root)
            finally:
                self._popup.grab_release()

        self.tree.bind("<Button-3>", on_right_click)

        # Action buttons
        ops = ttk.Frame(self)
        ops.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(ops, text="Start Selected", command=self.on_start_selected).pack(side="left", padx=4)
        ttk.Button(ops, text="Stop Selected", command=self.on_stop_selected).pack(side="left", padx=4)
        ttk.Button(ops, text="Start All in Order", command=self.on_start_all).pack(side="left", padx=12)
        # Group controls
        ttk.Label(ops, text="Group:").pack(side="left", padx=(12, 4))
        self.group_var = tk.StringVar(value="")
        self.group_combo = ttk.Combobox(ops, textvariable=self.group_var, width=18, state="readonly")
        self.group_combo.pack(side="left")
        ttk.Button(ops, text="Group Settings...", command=self.open_group_launcher).pack(side="left", padx=4)
        ttk.Button(ops, text="Stop All", command=self.on_stop_all).pack(side="left", padx=12)

        self.status = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status, anchor="w").pack(fill="x", padx=10, pady=(0, 8))

        # Shortcuts
        self.bind_all("<Control-n>", lambda e: self.on_add())
        self.bind_all("<Control-s>", lambda e: self.on_save())

    # ---- Table loading ----
    def _load_table(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for prof in self.data.get("profiles", []):
            name = prof.get("name")
            if prof.get("waitTarget"):
                self.conn_status[name] = self.conn_status.get(name, "-")
            else:
                self.conn_status[name] = "-"
            self.status_map[name] = self.status_map.get(name, "Stopped")
            self.tree.insert("", "end", iid=name, values=(
                prof.get("name", ""),
                prof.get("group", ""),
                prof.get("order", 0),
                prof.get("path", ""),
                "Yes" if prof.get("autoStart") else "No",
                self.conn_status.get(name, "-"),
                self.status_map.get(name, "Stopped"),
            ))
        self.after(100, self._apply_status_to_tree)
        # refresh available groups in UI
        try:
            self._refresh_groups()
        except Exception:
            pass

    def _get_groups(self):
        try:
            groups = sorted({(p.get("group", "") or "").strip() for p in self.data.get("profiles", [])})
            return [g for g in groups if g]
        except Exception:
            return []

    def _refresh_groups(self):
        groups = self._get_groups()
        try:
            current = self.group_var.get() if hasattr(self, "group_var") else ""
            self.group_combo["values"] = groups
            if current not in groups:
                self.group_var.set( groups[0] if groups else "" )
        except Exception:
            pass

    # ---- CRUD ----
    def on_add(self):
        dlg = ProfileEditor(self)
        self.wait_window(dlg)
        if dlg.result:
            names = {p.get("name") for p in self.data.get("profiles", [])}
            if dlg.result["name"] in names:
                messagebox.showerror("Error", "Name already exists.")
                return
            self.data.setdefault("profiles", []).append(dlg.result)
            self._load_table()

    def on_edit(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Info", "Please select a profile first.")
            return
        name = sel[0]
        prof = next((p for p in self.data.get("profiles", []) if p.get("name") == name), None)
        if not prof:
            messagebox.showerror("Error", "Profile not found.")
            return
        dlg = ProfileEditor(self, profile=prof)
        self.wait_window(dlg)
        if dlg.result:
            for i, p in enumerate(self.data.get("profiles", [])):
                if p.get("name") == name:
                    self.data["profiles"][i] = dlg.result
                    break
            self._load_table()

    def on_delete(self):
        sel = self.tree.selection()
        if not sel:
            return
        name = sel[0]
        self.data["profiles"] = [p for p in self.data.get("profiles", []) if p.get("name") != name]
        self.processes.pop(name, None)
        self.status_map.pop(name, None)
        self.conn_status.pop(name, None)
        self._load_table()

    def on_save(self):
        try:
            save_profiles(self.data)
            messagebox.showinfo("Saved", f"Profiles saved to {CONF_PATH.name}")
        except Exception as e:
            messagebox.showerror("Save Failed", str(e))

    # ---- Launch/Stop logic ----
    def _start_profile(self, prof: dict):
        name = prof.get("name")
        path = (prof.get("path") or "").strip()
        args = (prof.get("args") or "").strip()
        wait_target = (prof.get("waitTarget") or "").strip()
        wait_timeout = int(prof.get("waitTimeout", 0) or 0)
        wait_interval = int(prof.get("waitInterval", 2) or 2)

        if not path:
            self.status.set(f"No executable path: {name}")
            return
        existing = self.processes.get(name)
        if existing and getattr(existing, 'poll', lambda: None)() is None:
            # already running
            return
        if name in self._launching:
            return

        # mark launching before thread so sequential wait logic sees it immediately
        self._launching.add(name)

        def runner():
            self.status_map[name] = "Starting"
            self._user_stop_flags[name] = False
            self._apply_status_to_tree()
            # optional connectivity wait
            if wait_target:
                self.conn_status[name] = "Waiting..."
                self._apply_status_to_tree()
                ok_conn = wait_for_connectivity(wait_target, total_timeout=wait_timeout, interval=wait_interval)
                self.conn_status[name] = "Online" if ok_conn else "Offline"
                self._apply_status_to_tree()
                if wait_target and not ok_conn:
                    self.status.set(f"Connectivity failed/timeout: {wait_target}")
                    self.status_map[name] = "Stopped"
                    self._launching.discard(name)
                    self._apply_status_to_tree()
                    return
            # launch process
            try:
                cmd = [path] + ([a for a in args.split() if a] if args else [])
                proc = subprocess.Popen(cmd, cwd=str(Path(path).parent))
                self.processes[name] = proc
                self.status_map[name] = "Running"
                self.status.set(f"Started: {name}")
            except Exception as e:
                self.status_map[name] = "Stopped"
                self.status.set(f"Launch failed {name}: {e}")
            finally:
                self._launching.discard(name)
                self._apply_status_to_tree()

        threading.Thread(target=runner, daemon=True).start()

    def _stop_profile(self, prof: dict):
        name = prof.get("name")
        self._user_stop_flags[name] = True
        p = self.processes.get(name)
        if p and p.poll() is None:
            try:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except Exception:
                    p.kill()
            except Exception:
                pass
        self.processes.pop(name, None)
        self.status_map[name] = "Stopped"
        self._apply_status_to_tree()

    def on_start_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        name = sel[0]
        prof = next((p for p in self.data.get("profiles", []) if p.get("name") == name), None)
        if prof:
            self._start_profile(prof)

    def on_stop_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        name = sel[0]
        prof = next((p for p in self.data.get("profiles", []) if p.get("name") == name), None)
        if prof:
            self._stop_profile(prof)

    def on_start_all(self):
        # Start all profiles sequentially in the defined order
        def worker():
            profs = list(self.data.get("profiles", []))
            profs.sort(key=lambda p: (p.get("group", ""), int(p.get("order", 0) or 0), p.get("name", "")))
            for idx, prof in enumerate(profs):
                try:
                    name = prof.get("name")
                    self.status.set(f"Starting {idx+1}/{len(profs)}: {name}")
                    self._start_profile(prof)
                    # wait for launch thread to complete (name removed from _launching)
                    timeout = max(10, int(prof.get("waitTimeout", 0) or 0) + 10)
                    deadline = time.time() + timeout
                    time.sleep(0.3)  # give thread time to start
                    while name in self._launching and not self._stop_event.is_set() and time.time() < deadline:
                        time.sleep(0.2)
                    # optional post-launch delay if in a group
                    try:
                        if (prof.get("group", "").strip() and int(prof.get("postLaunchDelay", 0) or 0) > 0):
                            delay = int(prof.get("postLaunchDelay", 0) or 0)
                            self.status.set(f"Waiting {delay}s before next launch...")
                            end = time.time() + delay
                            while time.time() < end and not self._stop_event.is_set():
                                time.sleep(0.2)
                    except Exception:
                        pass
                except Exception:
                    pass
            self.status.set("Start all complete")

        threading.Thread(target=worker, daemon=True).start()

    def _start_groups(self, groups):
        groups = [g.strip() for g in (groups or []) if g and g.strip()]
        if not groups:
            return
        gset = set(groups)

        def worker():
            profs = [p for p in self.data.get("profiles", []) if (p.get("group", "").strip() in gset)]
            profs.sort(key=lambda p: (p.get("group", ""), int(p.get("order", 0) or 0), p.get("name", "")))
            for idx, prof in enumerate(profs):
                try:
                    name = prof.get("name")
                    self.status.set(f"Starting {idx+1}/{len(profs)}: {name}")
                    self._start_profile(prof)
                    timeout = max(10, int(prof.get("waitTimeout", 0) or 0) + 10)
                    deadline = time.time() + timeout
                    time.sleep(0.3)  # give thread time to start
                    while name in self._launching and not self._stop_event.is_set() and time.time() < deadline:
                        time.sleep(0.2)
                    try:
                        if (prof.get("group", "").strip() and int(prof.get("postLaunchDelay", 0) or 0) > 0):
                            delay = int(prof.get("postLaunchDelay", 0) or 0)
                            self.status.set(f"Waiting {delay}s before next launch...")
                            end = time.time() + delay
                            while time.time() < end and not self._stop_event.is_set():
                                time.sleep(0.2)
                    except Exception:
                        pass
                except Exception:
                    pass
            self.status.set(f"Start groups {sorted(gset)} complete")

        threading.Thread(target=worker, daemon=True).start()

    def on_start_group(self):
        group = (self.group_var.get() or "").strip()
        if not group:
            self.status.set("Select a group to start")
            return

        def worker():
            profs = [p for p in self.data.get("profiles", []) if (p.get("group", "").strip() == group)]
            profs.sort(key=lambda p: (int(p.get("order", 0) or 0), p.get("name", "")))
            for idx, prof in enumerate(profs):
                try:
                    name = prof.get("name")
                    self.status.set(f"Starting {idx+1}/{len(profs)}: {name}")
                    self._start_profile(prof)
                    timeout = max(10, int(prof.get("waitTimeout", 0) or 0) + 10)
                    deadline = time.time() + timeout
                    time.sleep(0.3)  # give thread time to start
                    while name in self._launching and not self._stop_event.is_set() and time.time() < deadline:
                        time.sleep(0.2)
                    try:
                        if (prof.get("group", "").strip() and int(prof.get("postLaunchDelay", 0) or 0) > 0):
                            delay = int(prof.get("postLaunchDelay", 0) or 0)
                            self.status.set(f"Waiting {delay}s before next launch...")
                            end = time.time() + delay
                            while time.time() < end and not self._stop_event.is_set():
                                time.sleep(0.2)
                    except Exception:
                        pass
                except Exception:
                    pass
            self.status.set(f"Start group '{group}' complete")

        threading.Thread(target=worker, daemon=True).start()

    def on_stop_group(self):
        group = (self.group_var.get() or "").strip()
        if not group:
            self.status.set("Select a group to stop")
            return
        profs = [p for p in self.data.get("profiles", []) if (p.get("group", "").strip() == group)]
        for prof in profs:
            try:
                self._stop_profile(prof)
            except Exception:
                pass
        self.status.set(f"Stop group '{group}' complete")

    def on_stop_all(self):
        for prof in list(self.data.get("profiles", [])):
            try:
                self._stop_profile(prof)
            except Exception:
                pass

    def _auto_start_profiles(self):
        def worker():
            try:
                # Auto-start behavior with optional group override
                override = bool(self.data.get("autoStartGroupsOverride"))
                group_modes = self.data.get("groupModes", {})
                
                if override:
                    # Use group modes: start groups with mode='on'
                    on_groups = {g for g, gm in group_modes.items() if gm.get("mode") == "on"}
                    if on_groups:
                        profs = [p for p in self.data.get("profiles", []) if (p.get("group", "").strip() in on_groups)]
                    else:
                        profs = []
                else:
                    # fallback to per-app autoStart
                    profs = [p for p in self.data.get("profiles", []) if p.get("autoStart")]
                
                profs.sort(key=lambda p: (p.get("group", ""), int(p.get("order", 0) or 0), p.get("name", "")))
                for idx, prof in enumerate(profs):
                    try:
                        name = prof.get("name")
                        self.status.set(f"Auto-starting {idx+1}/{len(profs)}: {name}")
                        self._start_profile(prof)
                        timeout = max(10, int(prof.get("waitTimeout", 0) or 0) + 10)
                        deadline = time.time() + timeout
                        time.sleep(0.3)  # give thread time to start
                        while name in self._launching and not self._stop_event.is_set() and time.time() < deadline:
                            time.sleep(0.2)
                        # optional post-launch delay if in a group
                        try:
                            if (prof.get("group", "").strip() and int(prof.get("postLaunchDelay", 0) or 0) > 0):
                                delay = int(prof.get("postLaunchDelay", 0) or 0)
                                self.status.set(f"Waiting {delay}s before next launch...")
                                end = time.time() + delay
                                while time.time() < end and not self._stop_event.is_set():
                                    time.sleep(0.2)
                        except Exception:
                            pass
                    except Exception:
                        pass
                self.status.set("Auto start complete")
            except Exception:
                self.status.set("Auto start complete (with errors)")

        threading.Thread(target=worker, daemon=True).start()

    def open_group_launcher(self):
        try:
            GroupLauncherWindow(self)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open Group Launcher: {e}")

    # ---- Background monitors ----
    def _apply_status_to_tree(self):
        for name in list(self.status_map.keys()):
            if name in self.tree.get_children():
                try:
                    self.tree.set(name, column="status", value=self.status_map.get(name, "Stopped"))
                except Exception:
                    pass
        for name in list(self.conn_status.keys()):
            if name in self.tree.get_children():
                try:
                    self.tree.set(name, column="conn", value=self.conn_status.get(name, "-"))
                except Exception:
                    pass

    def _conn_monitor(self):
        while not self._stop_event.is_set():
            try:
                for prof in list(self.data.get("profiles", [])):
                    name = prof.get("name")
                    wt = (prof.get("waitTarget") or "").strip()
                    if not wt:
                        continue
                    ok = False
                    try:
                        ok = can_reach(wt)
                    except Exception:
                        ok = False
                    self.conn_status[name] = "Online" if ok else "Offline"
                self.after(0, self._apply_status_to_tree)
            except Exception:
                pass
            for _ in range(6):
                if self._stop_event.is_set():
                    break
                time.sleep(0.5)

    def _proc_monitor(self):
        while not self._stop_event.is_set():
            try:
                now = time.time()
                for prof in list(self.data.get("profiles", [])):
                    if not prof.get("autoRestart"):
                        continue
                    name = prof.get("name")
                    if name in self._launching:
                        continue
                    if self._user_stop_flags.get(name):
                        continue
                    p = self.processes.get(name)
                    if p is None:
                        continue
                    running = getattr(p, 'poll', lambda: None)() is None
                    if running:
                        continue
                    last = self._last_restart.get(name, 0)
                    if now - last < 3:
                        continue
                    self._last_restart[name] = now
                    self.status.set(f"Detected exit, restarting: {name}")
                    self._start_profile(prof)
            except Exception:
                pass
            for _ in range(6):
                if self._stop_event.is_set():
                    break
                time.sleep(0.5)

    def _start_redis_monitor(self):
        """Start or restart Redis monitoring thread"""
        if not REDIS_AVAILABLE:
            return
        
        # Stop existing monitor
        self._redis_monitor_running = False
        time.sleep(0.3)  # let old thread exit
        
        # Check if any groups use redis mode
        group_modes = self.data.get("groupModes", {})
        has_redis = any(gm.get("mode") == "redis" for gm in group_modes.values())
        if not has_redis:
            return
        
        # Create new Redis client
        try:
            host = self.data.get("redisHost", "localhost")
            port = int(self.data.get("redisPort", 6379))
            db = int(self.data.get("redisDb", 0))
            password = self.data.get("redisPassword", "").strip() or None
            
            self._redis_client = redis.Redis(
                host=host,
                port=port,
                db=db,
                password=password,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2
            )
            # Test connection
            self._redis_client.ping()
            self._redis_monitor_running = True
            threading.Thread(target=self._redis_monitor, daemon=True).start()
        except Exception as e:
            self.status.set(f"Redis connection failed: {e}")
            self._redis_client = None

    def _redis_monitor(self):
        """Monitor Redis keys and control group start/stop"""
        while self._redis_monitor_running and not self._stop_event.is_set():
            try:
                if not self._redis_client:
                    break
                
                group_modes = self.data.get("groupModes", {})
                for group, gm in group_modes.items():
                    if gm.get("mode") != "redis":
                        continue
                    
                    redis_key = (gm.get("redisKey", "") or "").strip()
                    if not redis_key:
                        continue
                    
                    # Parse key:field format
                    if ":" in redis_key:
                        key, field = redis_key.split(":", 1)
                        try:
                            value = self._redis_client.hget(key, field)
                        except Exception:
                            value = None
                    else:
                        try:
                            value = self._redis_client.get(redis_key)
                        except Exception:
                            value = None
                    
                    # Determine desired state: 1=running, 0/missing=stopped
                    should_run = (value == "1")
                    
                    # Get current state of group
                    profs = [p for p in self.data.get("profiles", []) if p.get("group", "").strip() == group]
                    running_count = sum(1 for p in profs if p.get("name") in self.processes 
                                       and self.processes[p.get("name")].poll() is None)
                    is_running = running_count > 0
                    
                    # Apply state change
                    if should_run and not is_running:
                        # Start group
                        self.status.set(f"Redis trigger: starting group '{group}'")
                        self._start_groups([group])
                    elif not should_run and is_running:
                        # Stop group
                        self.status.set(f"Redis trigger: stopping group '{group}'")
                        for prof in profs:
                            try:
                                self._stop_profile(prof)
                            except Exception:
                                pass
                
            except Exception as e:
                # Connection lost or other error
                pass
            
            # Poll interval
            for _ in range(10):  # 2 seconds total
                if self._stop_event.is_set() or not self._redis_monitor_running:
                    break
                time.sleep(0.2)

    def _on_close(self):
        try:
            self._stop_event.set()
        except Exception:
            pass
        try:
            for name, p in list(self.processes.items()):
                self._user_stop_flags[name] = True
                if p and getattr(p, 'poll', lambda: None)() is None:
                    try:
                        p.terminate()
                        try:
                            p.wait(timeout=2)
                        except Exception:
                            p.kill()
                    except Exception:
                        pass
            self.processes.clear()
        except Exception:
            pass
        self.destroy()


class GroupLauncherWindow(tk.Toplevel):
    def __init__(self, app: SimpleLauncherApp):
        super().__init__(app)
        self.app = app
        self.title("Group Launcher Settings")
        self.resizable(False, False)

        self.mode_vars = {}  # group -> StringVar for dropdown
        self.redis_key_vars = {}  # group -> StringVar for redis key:field
        self.redis_entry_widgets = {}  # group -> Entry widget (to show/hide)
        self.var_override = tk.IntVar(value=1 if app.data.get("autoStartGroupsOverride") else 0)

        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        # Redis settings section
        redis_frame = ttk.LabelFrame(frm, text="Redis Server Settings", padding=10)
        redis_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        
        self.var_redis_host = tk.StringVar(value=app.data.get("redisHost", "localhost"))
        self.var_redis_port = tk.StringVar(value=str(app.data.get("redisPort", 6379)))
        self.var_redis_db = tk.StringVar(value=str(app.data.get("redisDb", 0)))
        self.var_redis_password = tk.StringVar(value=app.data.get("redisPassword", ""))

        ttk.Label(redis_frame, text="Host:").grid(row=0, column=0, sticky="e", padx=4)
        ttk.Entry(redis_frame, textvariable=self.var_redis_host, width=15).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(redis_frame, text="Port:").grid(row=0, column=2, sticky="e", padx=4)
        ttk.Entry(redis_frame, textvariable=self.var_redis_port, width=8).grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(redis_frame, text="DB:").grid(row=1, column=0, sticky="e", padx=4)
        ttk.Entry(redis_frame, textvariable=self.var_redis_db, width=8).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(redis_frame, text="Password:").grid(row=1, column=2, sticky="e", padx=4)
        ttk.Entry(redis_frame, textvariable=self.var_redis_password, width=15, show="*").grid(row=1, column=3, sticky="w", padx=4)

        if not REDIS_AVAILABLE:
            ttk.Label(redis_frame, text="(redis-py not installed)", foreground="red").grid(row=2, column=0, columnspan=4, pady=4)

        # Groups section
        ttk.Label(frm, text="Group Launch Control:", font=("", 10, "bold")).grid(row=1, column=0, sticky="w", pady=(10, 5))

        list_frame = ttk.Frame(frm)
        list_frame.grid(row=2, column=0, sticky="w")

        groups = self.app._get_groups()
        group_modes = self.app.data.get("groupModes", {})
        
        r = 0
        for g in groups:
            # Group name label
            ttk.Label(list_frame, text=g, width=20).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=4)
            
            # Mode dropdown
            mode_var = tk.StringVar(value=group_modes.get(g, {}).get("mode", "off"))
            mode_combo = ttk.Combobox(list_frame, textvariable=mode_var, values=["on", "off", "redis"], width=10, state="readonly")
            mode_combo.grid(row=r, column=1, sticky="w", padx=4)
            mode_combo.bind("<<ComboboxSelected>>", lambda e, gg=g: self.on_mode_change(gg))
            self.mode_vars[g] = mode_var
            
            # Redis key:field entry (shown only when mode=redis)
            redis_key = group_modes.get(g, {}).get("redisKey", "")
            redis_var = tk.StringVar(value=redis_key)
            redis_entry = ttk.Entry(list_frame, textvariable=redis_var, width=25)
            redis_entry.grid(row=r, column=2, sticky="w", padx=4)
            self.redis_key_vars[g] = redis_var
            self.redis_entry_widgets[g] = redis_entry
            
            # Show/hide redis entry based on mode
            if mode_var.get() != "redis":
                redis_entry.grid_remove()
            
            r += 1

        # Override checkbox
        ttk.Checkbutton(frm, text="On app launch, use these group settings (override per-app Auto Start)",
                        variable=self.var_override).grid(row=3, column=0, sticky="w", pady=(12, 4))

        # Buttons
        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Button(btns, text="Apply & Start Redis Monitor", command=self.on_apply).pack(side="left", padx=4)
        ttk.Button(btns, text="Save", command=self.on_save).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="left", padx=4)

        self.transient(app)
        self.grab_set()

    def on_mode_change(self, group: str):
        """Show/hide Redis key entry based on mode selection"""
        mode = self.mode_vars[group].get()
        entry = self.redis_entry_widgets.get(group)
        if entry:
            if mode == "redis":
                entry.grid()
            else:
                entry.grid_remove()

    def on_apply(self):
        """Apply settings and start/update Redis monitor"""
        try:
            self.save_settings()
            self.app._start_redis_monitor()
            messagebox.showinfo("Applied", "Settings applied and Redis monitor started/updated")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to apply: {e}")

    def save_settings(self):
        """Save Redis and group mode settings"""
        # Save Redis connection settings
        try:
            self.app.data["redisHost"] = self.var_redis_host.get().strip() or "localhost"
            self.app.data["redisPort"] = int(self.var_redis_port.get() or 6379)
            self.app.data["redisDb"] = int(self.var_redis_db.get() or 0)
            self.app.data["redisPassword"] = self.var_redis_password.get().strip()
        except Exception:
            pass
        
        # Save group modes
        group_modes = {}
        for g, mode_var in self.mode_vars.items():
            mode = mode_var.get()
            redis_key = self.redis_key_vars[g].get().strip()
            group_modes[g] = {
                "mode": mode,
                "redisKey": redis_key if mode == "redis" else ""
            }
        self.app.data["groupModes"] = group_modes
        self.app.data["autoStartGroupsOverride"] = bool(int(self.var_override.get() or 0))

    def on_save(self):
        """Save to launch.conf"""
        try:
            self.save_settings()
            save_profiles(self.app.data)
            self.app.status.set("Saved group launch settings")
            messagebox.showinfo("Saved", "Settings saved to launch.conf")
        except Exception as e:
            messagebox.showerror("Save Failed", str(e))

    # (no background monitor methods here)

# ---------------- Entry Point -----------------

if __name__ == "__main__":
    app = SimpleLauncherApp()
    app.mainloop()
