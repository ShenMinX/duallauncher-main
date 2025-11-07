import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from pathlib import Path
import json
import threading
import subprocess
import launcher
import time
import sys

# pyinstaller --onefile --name DuallauncherProfiles gui_launcher_profiles.py

# 在开发环境下使用脚本目录；打包为单文件（PyInstaller）时，使用可执行文件所在目录
def _resolve_base_dir() -> Path:
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
    except Exception:
        pass
    return Path(__file__).resolve().parent

BASE_DIR = _resolve_base_dir()
CONF_PATH = BASE_DIR / "launch.conf"


def load_profiles():
    if not CONF_PATH.exists():
        return {"profiles": []}
    try:
        with CONF_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"profiles": []}
        data.setdefault("profiles", [])
        # normalize booleans and defaults
        for p in data.get("profiles", []):
            try:
                p["autoStart"] = bool(p.get("autoStart", False))
            except Exception:
                p["autoStart"] = False
            # monitor/restart on unexpected exit
            try:
                p["autoRestart"] = bool(p.get("autoRestart", False))
            except Exception:
                p["autoRestart"] = False
            # optional connectivity wait fields
            if "waitTimeout" in p:
                try:
                    p["waitTimeout"] = int(p.get("waitTimeout") or 0)
                except Exception:
                    p["waitTimeout"] = 0
            if "waitInterval" in p:
                try:
                    p["waitInterval"] = int(p.get("waitInterval") or 2)
                except Exception:
                    p["waitInterval"] = 2
        return data
    except Exception:
        return {"profiles": []}


def save_profiles(data: dict):
    data = data or {"profiles": []}
    data.setdefault("profiles", [])
    with CONF_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class ProfileEditor(tk.Toplevel):
    def __init__(self, master, profile=None):
        super().__init__(master)
        self.title("Edit Profile")
        self.resizable(False, False)
        self.profile = profile or {}
        self.result = None

        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        # 字段：名称、匹配类型(kind)、值(value)、显示器(monitor)、可执行路径(path 可选)、参数(args 可选)、
        #      启动前等待连接(waitTarget/Timeout/Interval 可选)、自动启动(autoStart)
        self.var_name = tk.StringVar(value=self.profile.get("name", ""))
        self.var_kind = tk.StringVar(value=self.profile.get("kind", "Process"))  # Process/Title
        self.var_value = tk.StringVar(value=self.profile.get("value", ""))
        self.var_monitor = tk.StringVar(value=str(self.profile.get("monitor", 1)))
        self.var_path = tk.StringVar(value=self.profile.get("path", ""))
        self.var_args = tk.StringVar(value=self.profile.get("args", ""))
        self.var_wait_target = tk.StringVar(value=self.profile.get("waitTarget", ""))
        self.var_wait_timeout = tk.StringVar(value=str(self.profile.get("waitTimeout", 0)))
        self.var_wait_interval = tk.StringVar(value=str(self.profile.get("waitInterval", 2)))
        self.var_auto = tk.IntVar(value=1 if bool(self.profile.get("autoStart", False)) else 0)
        self.var_restart = tk.IntVar(value=1 if bool(self.profile.get("autoRestart", False)) else 0)

        row = 0
        ttk.Label(frm, text="Name:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(frm, textvariable=self.var_name, width=28).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(frm, text="Match Type:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        ttk.Combobox(frm, textvariable=self.var_kind, values=["Process", "Title"], state="readonly", width=12).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(frm, text="Match Value (Process or Title):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(frm, textvariable=self.var_value, width=28).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(frm, text="Monitor #:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(frm, textvariable=self.var_monitor, width=10).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(frm, text="Executable Path (optional):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        path_wrap = ttk.Frame(frm)
        path_wrap.grid(row=row, column=1, sticky="w")
        ttk.Entry(path_wrap, textvariable=self.var_path, width=28).pack(side="left")
        ttk.Button(path_wrap, text="Browse...", command=self._browse_path).pack(side="left", padx=(6, 0))
        row += 1
        ttk.Label(frm, text="Arguments (optional):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(frm, textvariable=self.var_args, width=28).grid(row=row, column=1, sticky="w")
        row += 1
        # Optional connectivity wait settings
        ttk.Label(frm, text="Wait Target (optional):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(frm, textvariable=self.var_wait_target, width=28).grid(row=row, column=1, sticky="w")
        row += 1
        subfrm = ttk.Frame(frm)
        subfrm.grid(row=row, column=1, sticky="w")
        ttk.Label(subfrm, text="Timeout (s):").pack(side="left")
        ttk.Entry(subfrm, textvariable=self.var_wait_timeout, width=8).pack(side="left", padx=(0, 8))
        ttk.Label(subfrm, text="Interval (s):").pack(side="left")
        ttk.Entry(subfrm, textvariable=self.var_wait_interval, width=8).pack(side="left")
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
        # 打开文件选择器以选择可执行文件
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
        filetypes = [
            ("Executable", "*.exe *.bat *.cmd"),
            ("All Files", "*.*"),
        ]
        filename = filedialog.askopenfilename(title="Select Executable", initialdir=initdir, filetypes=filetypes)
        if filename:
            self.var_path.set(filename)

    def on_ok(self):
        try:
            monitor = int(self.var_monitor.get().strip())
        except Exception:
            messagebox.showerror("Error", "Monitor number must be an integer.")
            return
        name = self.var_name.get().strip() or self.var_value.get().strip()
        if not name:
            messagebox.showerror("Error", "Please enter a name or match value.")
            return
        value = self.var_value.get().strip()
        if not value:
            messagebox.showerror("Error", "Please enter a match value (process or title).")
            return
        kind = self.var_kind.get().strip() or "Process"
        # parse optional wait fields
        wt_target = self.var_wait_target.get().strip()
        try:
            wt_timeout = int((self.var_wait_timeout.get() or "0").strip())
        except Exception:
            wt_timeout = 0
        try:
            wt_interval = int((self.var_wait_interval.get() or "2").strip())
        except Exception:
            wt_interval = 2
        self.result = {
            "name": name,
            "kind": kind,
            "value": value,
            "monitor": monitor,
            "path": self.var_path.get().strip(),
            "args": self.var_args.get().strip(),
            "autoStart": bool(int(self.var_auto.get() or 0)),
            "autoRestart": bool(int(self.var_restart.get() or 0)),
        }
        if wt_target:
            self.result["waitTarget"] = wt_target
            if wt_timeout:
                self.result["waitTimeout"] = wt_timeout
            if wt_interval:
                self.result["waitInterval"] = wt_interval
        self.destroy()


class ProfilesApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Profiles Launcher (Multi-App / Multi-Monitor)")
        self.geometry("750x420")
        self.resizable(True, True)

        self.data = load_profiles()
        self.processes = {}  # name -> subprocess.Popen
        self.conn_status = {}  # name -> "Online"/"Offline"/"Waiting..."/"-"
        self._stop_event = threading.Event()
        # Auto-restart/monitoring helpers
        self._user_stop_flags = {}   # name -> True if user requested stop via UI
        self._launching = set()      # names currently in launching flow to avoid duplicates
        self._last_restart = {}      # name -> last restart timestamp to avoid thrashing

        self._build_menu()
        self._build_main()
        self._load_table()

        # Auto start
        self.after(200, self._auto_start_profiles)
        # Background connectivity monitor
        threading.Thread(target=self._conn_monitor, daemon=True).start()
        # Background process monitor (auto-restart on crash)
        threading.Thread(target=self._proc_monitor, daemon=True).start()
        # Window close hook
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_menu(self):
        m = tk.Menu(self)
        self.config(menu=m)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="Add Profile", command=self.on_add)
        fm.add_command(label="Edit Selected", command=self.on_edit)
        fm.add_command(label="Delete Selected", command=self.on_delete)
        fm.add_separator()
        fm.add_command(label="Save to launch.conf", command=self.on_save)
        m.add_cascade(label="Config", menu=fm)

    def _build_main(self):
        top = ttk.Frame(self)
        top.pack(fill="both", expand=True, padx=10, pady=10)

        cols = ("name", "kind", "value", "monitor", "path", "auto", "conn")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", height=12)
        self.tree.heading("name", text="Name")
        self.tree.heading("kind", text="Type")
        self.tree.heading("value", text="Match")
        self.tree.heading("monitor", text="Monitor")
        self.tree.heading("path", text="Path")
        self.tree.heading("auto", text="Auto")
        self.tree.heading("conn", text="Conn")
        self.tree.column("name", width=120)
        self.tree.column("value", width=150)
        self.tree.column("path", width=200)
        self.tree.column("kind", width=70)
        self.tree.column("monitor", width=60, anchor="center")
        self.tree.column("auto", width=60, anchor="center")
        self.tree.column("conn", width=70, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(top, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")

        # Config operation buttons
        cfgops = ttk.Frame(self)
        cfgops.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(cfgops, text="Add Profile", command=self.on_add).pack(side="left", padx=4)
        ttk.Button(cfgops, text="Edit Selected", command=self.on_edit).pack(side="left", padx=4)
        ttk.Button(cfgops, text="Delete Selected", command=self.on_delete).pack(side="left", padx=4)
        ttk.Button(cfgops, text="Save to launch.conf", command=self.on_save).pack(side="left", padx=12)

        # Context menu
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

        # Action area
        ops = ttk.Frame(self)
        ops.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(ops, text="Start Selected", command=self.on_start_selected).pack(side="left", padx=4)
        ttk.Button(ops, text="Stop Selected", command=self.on_stop_selected).pack(side="left", padx=4)
        ttk.Button(ops, text="Start All", command=self.on_start_all).pack(side="left", padx=12)
        ttk.Button(ops, text="Stop All", command=self.on_stop_all).pack(side="left", padx=4)

        # Status label
        self.status = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status, anchor="w").pack(fill="x", padx=10, pady=(0, 8))
        # Shortcuts
        self.bind_all("<Control-n>", lambda e: self.on_add())
        self.bind_all("<Control-s>", lambda e: self.on_save())

    def _load_table(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for prof in self.data.get("profiles", []):
            name = prof.get("name", prof.get("value"))
            # Initialize connection status
            if prof.get("waitTarget"):
                self.conn_status[name] = self.conn_status.get(name, "-")
            else:
                self.conn_status[name] = "-"
            self.tree.insert("", "end", iid=prof.get("name", prof.get("value")), values=(
                prof.get("name", ""),
                prof.get("kind", "Process"),
                prof.get("value", ""),
                prof.get("monitor", 1),
                prof.get("path", ""),
                "Yes" if prof.get("autoStart") else "No",
                self.conn_status.get(name, "-"),
            ))
        # Initial refresh of connection column
        self.after(100, self._apply_conn_status_to_tree)

    def on_add(self):
        dlg = ProfileEditor(self)
        self.wait_window(dlg)
        if dlg.result:
            # unique by name
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
            # replace
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
        self._load_table()

    def on_save(self):
        try:
            save_profiles(self.data)
            messagebox.showinfo("Saved", f"Profiles saved to {CONF_PATH.name}")
        except Exception as e:
            messagebox.showerror("Save Failed", str(e))

    # Start/stop logic
    def _start_profile(self, prof: dict):
        name = prof.get("name", prof.get("value"))
        path = (prof.get("path") or "").strip()
        args = (prof.get("args") or "").strip()
        monitor = int(prof.get("monitor", 1))
        kind = prof.get("kind", "Process")
        value = prof.get("value", "")
        wait_target = (prof.get("waitTarget") or "").strip()
        wait_timeout = int(prof.get("waitTimeout", 0) or 0)
        wait_interval = int(prof.get("waitInterval", 2) or 2)

        # Prevent duplicate launches: if process is already running, just attempt move
        existing = self.processes.get(name)
        if existing and getattr(existing, 'poll', lambda: None)() is None:
            # If already running, optionally re-move the window and return
            threading.Thread(
                target=lambda: launcher.move_window_with_multimonitor(
                    monitor, kind, value, retries=20, delay=0.5
                ),
                daemon=True,
            ).start()
            return
        if name in self._launching:
            return

        def runner():
            self._launching.add(name)
            # Clear any prior user-stop flag since we're explicitly starting now
            self._user_stop_flags[name] = False
            # Optional: wait for connectivity before launch
            if wait_target:
                self.status.set(f"Waiting for connectivity: {wait_target} ...")
                self._set_conn_status(name, "Waiting...")
                ok_conn = launcher.wait_for_connectivity(wait_target, total_timeout=wait_timeout, interval=wait_interval)
                if not ok_conn:
                    self.status.set(f"Connectivity failed/timeout: {wait_target}")
                    self._set_conn_status(name, "Offline")
                    self._launching.discard(name)
                    return
                else:
                    self._set_conn_status(name, "Online")
            # Launch process (optional)
            proc = None
            if path:
                try:
                    cmd = [path] + ([a for a in args.split() if a] if args else [])
                    proc = subprocess.Popen(cmd, cwd=str(Path(path).parent))
                    self.processes[name] = proc
                except Exception as e:
                    self.status.set(f"Launch failed: {name}: {e}")
                    self._launching.discard(name)
                    return
            # Move window
            if proc is not None:
                launcher.wait_for_window_of_process(proc.pid, timeout=15.0, poll=0.5)
            ok_move = launcher.move_window_with_multimonitor(monitor, kind, value, retries=40, delay=0.5)
            if not ok_move and kind == "Process" and value.endswith(".exe"):
                launcher.move_window_with_multimonitor(monitor, kind, value[:-4], retries=20, delay=0.5)
            self.status.set(f"Window move complete: {name}")
            self._launching.discard(name)

        threading.Thread(target=runner, daemon=True).start()

    def _set_conn_status(self, name: str, value: str):
        self.conn_status[name] = value
        # Apply to UI (main thread)
        try:
            self.after(0, self._apply_conn_status_to_tree)
        except Exception:
            pass

    def _apply_conn_status_to_tree(self):
        for name, status in list(self.conn_status.items()):
            if name in self.tree.get_children():
                try:
                    self.tree.set(name, column="conn", value=status)
                except Exception:
                    pass

    def _conn_monitor(self):
    # Periodically check connectivity for profiles with waitTarget
        while not self._stop_event.is_set():
            try:
                for prof in list(self.data.get("profiles", [])):
                    name = prof.get("name", prof.get("value"))
                    wt = (prof.get("waitTarget") or "").strip()
                    if not wt:
                        continue
                    ok = False
                    try:
                        # Single connectivity probe (non-blocking loop)
                        ok = launcher._can_reach(wt)
                    except Exception:
                        ok = False
                    self.conn_status[name] = "Online" if ok else "Offline"
                # 推送到UI
                self.after(0, self._apply_conn_status_to_tree)
            except Exception:
                pass
            # Sleep between refresh cycles
            for _ in range(6):
                if self._stop_event.is_set():
                    break
                time.sleep(0.5)

    def _on_close(self):
    # Signal background threads to stop
        try:
            self._stop_event.set()
        except Exception:
            pass
    # Mark all running apps as user-stopped and terminate them
        try:
            for name, p in list(self.processes.items()):
                try:
                    self._user_stop_flags[name] = True
                except Exception:
                    pass
                try:
                    if p and getattr(p, 'poll', lambda: None)() is None:
                        launcher.terminate_process_tree(p)
                except Exception:
                    pass
            self.processes.clear()
        except Exception:
            pass
        # Close window
        self.destroy()

    def _stop_profile(self, prof: dict):
        name = prof.get("name", prof.get("value"))
    # Mark as user-stopped to avoid auto-restart
        self._user_stop_flags[name] = True
        p = self.processes.get(name)
        if p and p.poll() is None:
            try:
                launcher.terminate_process_tree(p)
            except Exception:
                pass
        self.processes.pop(name, None)

    def _proc_monitor(self):
    # Monitor autoRestart profiles and restart if unexpectedly exited
        while not self._stop_event.is_set():
            try:
                now = time.time()
                for prof in list(self.data.get("profiles", [])):
                    try:
                        if not prof.get("autoRestart"):
                            continue
                        path = (prof.get("path") or "").strip()
                        if not path:
                            # Only monitor processes started by this launcher
                            continue
                        name = prof.get("name", prof.get("value"))
                        # Skip if currently launching to avoid dups
                        if name in self._launching:
                            continue
                        # If user requested stop, don't restart
                        if self._user_stop_flags.get(name):
                            continue
                        p = self.processes.get(name)
                        if p is None:
                            # Skip auto start if not previously launched by this app
                            continue
                        running = getattr(p, 'poll', lambda: None)() is None
                        if running:
                            continue
                        # Cooldown to avoid rapid restart loops
                        last = self._last_restart.get(name, 0)
                        if now - last < 3:
                            continue
                        # Relaunch
                        self._last_restart[name] = now
                        self.status.set(f"Detected exit, restarting: {name}")
                        self._start_profile(prof)
                    except Exception:
                        pass
            except Exception:
                pass
            # Sleep a bit
            for _ in range(6):
                if self._stop_event.is_set():
                    break
                time.sleep(0.5)

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
        for prof in self.data.get("profiles", []):
            try:
                if prof.get("autoStart"):
                    self._start_profile(prof)
            except Exception:
                pass

    def on_stop_all(self):
        for prof in list(self.data.get("profiles", [])):
            try:
                self._stop_profile(prof)
            except Exception:
                pass

    def _auto_start_profiles(self):
        try:
            for prof in self.data.get("profiles", []):
                try:
                    if prof.get("autoStart"):
                        self._start_profile(prof)
                except Exception:
                    pass
            self.status.set("Auto start complete")
        except Exception:
            # keep UI resilient even if auto start has issues
            self.status.set("Auto start complete (with errors)")


if __name__ == "__main__":
    app = ProfilesApp()
    app.mainloop()
