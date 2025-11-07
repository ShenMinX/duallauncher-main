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
        self.processes = {}           # name -> subprocess.Popen
        self.status_map = {}          # name -> Running / Stopped / Starting
        self.conn_status = {}         # name -> Online / Offline / - / Waiting...
        self._stop_event = threading.Event()
        self._user_stop_flags = {}    # name -> True if user manually stopped
        self._launching = set()       # names currently launching
        self._last_restart = {}       # name -> last restart time

        self._build_menu()
        self._build_main()
        self._load_table()

        # Auto start after short delay
        self.after(200, self._auto_start_profiles)
        # Background connectivity monitor
        threading.Thread(target=self._conn_monitor, daemon=True).start()
        # Background process monitor (autoRestart)
        threading.Thread(target=self._proc_monitor, daemon=True).start()

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
                groups = set([g.strip() for g in self.data.get("autoStartGroups", []) if g and g.strip()])
                if override and groups:
                    profs = [p for p in self.data.get("profiles", []) if (p.get("group", "").strip() in groups)]
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
        self.title("Group Launcher")
        self.resizable(False, False)

        self.vars = {}  # group -> IntVar
        self.var_override = tk.IntVar(value=1 if app.data.get("autoStartGroupsOverride") else 0)

        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        ttk.Label(frm, text="Select groups to launch:").grid(row=0, column=0, sticky="w")

        list_frame = ttk.Frame(frm)
        list_frame.grid(row=1, column=0, sticky="w")

        groups = self.app._get_groups()
        sel_groups = set([g.strip() for g in self.app.data.get("autoStartGroups", []) if g and g.strip()])
        r = 0
        for g in groups:
            var = tk.IntVar(value=1 if g in sel_groups else 0)
            chk = ttk.Checkbutton(list_frame, text=g, variable=var, command=lambda gg=g, vv=var: self.on_toggle(gg, vv))
            chk.grid(row=r, column=0, sticky="w", pady=2)
            self.vars[g] = var
            r += 1

        # override checkbox
        ttk.Checkbutton(frm, text="On app launch, start only these groups (override per-app Auto Start)",
                        variable=self.var_override).grid(row=2, column=0, sticky="w", pady=(8, 4))

        # buttons
        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Button(btns, text="Launch Selected Now", command=self.launch_selected).pack(side="left", padx=4)
        ttk.Button(btns, text="Save", command=self.on_save).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="left", padx=4)

        self.transient(app)
        self.grab_set()

    def on_toggle(self, group: str, var: tk.IntVar):
        try:
            if var.get():
                # launch this group immediately when checked
                self.app._start_groups([group])
        except Exception:
            pass

    def get_selected_groups(self):
        return [g for g, v in self.vars.items() if int(v.get() or 0) == 1]

    def launch_selected(self):
        groups = self.get_selected_groups()
        self.app._start_groups(groups)

    def on_save(self):
        groups = self.get_selected_groups()
        self.app.data["autoStartGroups"] = groups
        self.app.data["autoStartGroupsOverride"] = bool(int(self.var_override.get() or 0))
        try:
            save_profiles(self.app.data)
            self.app.status.set("Saved group launch settings")
        except Exception as e:
            messagebox.showerror("Save Failed", str(e))

    # (no background monitor methods here)

# ---------------- Entry Point -----------------

if __name__ == "__main__":
    app = SimpleLauncherApp()
    app.mainloop()
