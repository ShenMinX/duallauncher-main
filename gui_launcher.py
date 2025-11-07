import tkinter as tk
from tkinter import ttk, messagebox
import threading
import json
from urllib.parse import urlparse
from pathlib import Path
import launcher

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "apps" / "config.json"

class AppGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("双启动器控制")
        self.geometry("560x260")
        self.resizable(False, False)

        self.config_data = self._load_config()

        # Redis 连接性（Ping URL）
        frame = ttk.LabelFrame(self)
        frame.pack(fill="x", padx=8, pady=6)
        # 顶部标题 + 状态
        header = ttk.Frame(frame)
        header.grid(row=0, column=0, columnspan=5, sticky="we")
        ttk.Label(header, text="连接状态 / Redis", font=("Segoe UI", 10, "bold")).pack(side="left", padx=(6, 0), pady=(6, 0))
        self.ping_status = tk.StringVar(value="未知")
        # 显示一个彩色状态点 + 粗体文字
        self.status_canvas = tk.Canvas(header, width=14, height=14, highlightthickness=0)
        self.status_canvas.pack(side="left", padx=(8, 2), pady=(6, 0))
        self._status_dot = self.status_canvas.create_oval(2, 2, 12, 12, outline="", fill="#999999")
        self.status_label = tk.Label(header, textvariable=self.ping_status, font=("Segoe UI", 10, "bold"))
        self.status_label.pack(side="left", padx=(2, 6), pady=(6, 0))
        # 输入区域
        ttk.Label(frame, text="Redis（主机[:端口]）：").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        initial_ping = self._initial_ping_value()
        self.ping_var = tk.StringVar(value=initial_ping)
        ttk.Entry(frame, textvariable=self.ping_var, width=40).grid(row=1, column=1, padx=6)
        # 海图 URL 输入
        ttk.Label(frame, text="海图网址：").grid(row=2, column=0, sticky="w", padx=6, pady=6)
        initial_chart = self.config_data.get("chart_url", getattr(launcher, "CHART_URL", ""))
        if initial_chart:
            launcher.CHART_URL = initial_chart
        self.chart_var = tk.StringVar(value=initial_chart)
        ttk.Entry(frame, textvariable=self.chart_var, width=60).grid(row=2, column=1, columnspan=3, sticky="we", padx=6)

        save_btn = ttk.Button(frame, text="应用并保存", command=self.save_config)
        save_btn.grid(row=1, column=2, padx=6)
        # 初始化状态显示
        self._update_status_visual(None)

        # 控制区
        ctrl_frame = ttk.LabelFrame(self, text="应用程序")
        ctrl_frame.pack(fill="x", padx=8, pady=6)

        # 无人船远程控制
        ttk.Label(ctrl_frame, text="无人船远程控制：").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.ctrl_status = tk.StringVar(value="已停止")
        ttk.Label(ctrl_frame, textvariable=self.ctrl_status).grid(row=0, column=1, sticky="w")
        self.ctrl_btn = ttk.Button(ctrl_frame, text="启动", command=self.toggle_ctrl)
        self.ctrl_btn.grid(row=0, column=2, padx=6)

        # 海图浏览器
        ttk.Label(ctrl_frame, text="海图浏览器：").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        self.chart_status = tk.StringVar(value="已关闭")
        ttk.Label(ctrl_frame, textvariable=self.chart_status).grid(row=1, column=1, sticky="w")
        self.chart_btn = ttk.Button(ctrl_frame, text="打开", command=self.toggle_chart)
        self.chart_btn.grid(row=1, column=2, padx=6)

        # 线程安全的轮询
        self._polling = True
        self._online = None  # Redis 连通性：True/False/None（未知）
        self.after(500, self._poll_status)
        self.after(1000, self._poll_ping)

        # 进程引用
        self._ctrl_proc = None
        self._browser_opened = False
        # 自启动：等待连通性后启动无人船和浏览器
        self._cancel_event = threading.Event()
        self._start_on_launch()

    def _load_config(self):
        if not CONFIG_PATH.exists():
            return {}
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _initial_ping_value(self) -> str:
        # Prefer ping_url; else compose from redis.host:redis.port; else launcher.PING_URL
        if isinstance(self.config_data, dict):
            if self.config_data.get("ping_url"):
                return str(self.config_data.get("ping_url"))
            r = self.config_data.get("redis") or {}
            host = r.get("host")
            port = r.get("port")
            if host and port:
                return f"{host}:{port}"
            if host:
                return str(host)
        return str(launcher.PING_URL)

    def _resolve_ping_target_for_connectivity(self) -> str:
        raw = (self.ping_var.get() or "").strip()
        host = None
        port = None
        if "://" in raw:
            pr = urlparse(raw)
            host = pr.hostname or "localhost"
            port = pr.port or 6379
        else:
            if ":" in raw:
                h, p = raw.split(":", 1)
                host = h or "localhost"
                try:
                    port = int(p)
                except Exception:
                    port = 6379
            else:
                host = raw or (self.config_data.get("redis", {}).get("host") or "localhost")
                port = int(self.config_data.get("redis", {}).get("port", 6379))
        return f"{host}:{port}"

    def _start_on_launch(self):
        # Start a new background worker; prior worker will observe cancel flag
        t = threading.Thread(target=self._auto_start_worker, daemon=True)
        t.start()

    def _auto_start_worker(self):
        try:
            target = self._resolve_ping_target_for_connectivity()
            ok = launcher.wait_for_connectivity(target, launcher.PING_TOTAL_TIMEOUT, launcher.PING_RETRY_INTERVAL, cancel_event=self._cancel_event)
            if not ok:
                self.after(0, lambda: messagebox.showerror("连接", f"无法连接到 {target}，不启动应用。"))
                return
            # Start USV app (MultiMonitorTool in launcher will move it)
            self._ctrl_proc = launcher.launch_process(launcher.CTRL_EXE)
            # Start browser (MultiMonitorTool will move it)
            launcher.open_browser_fullscreen(launcher.CHART_URL)
            self._browser_opened = True
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("启动错误", str(e)))

    def _poll_ping(self):
        try:
            target = self._resolve_ping_target_for_connectivity()
            # Avoid blocking UI; do in thread and update UI on completion
            def worker():
                ok = launcher._can_reach(target)
                def apply():
                    self._online = bool(ok)
                    self.ping_status.set("在线" if ok else "离线")
                    self._apply_connectivity_to_buttons()
                    self._update_status_visual(ok)
                self.after(0, apply)
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            self.ping_status.set("Error")
        finally:
            if self._polling:
                self.after(2000, self._poll_ping)

    def _apply_connectivity_to_buttons(self):
        """Disable Start/Open when offline; keep Stop/Close enabled."""
        # Determine current running/open states
        ctrl_running = bool(self._ctrl_proc and self._ctrl_proc.poll() is None)
        browser_open = False
        if getattr(launcher, "_browser_process", None) and launcher._browser_process.poll() is None:
            browser_open = True
        else:
            browser_open = bool(self._browser_opened)

        if self._online is False:
            # If offline, only disable actions that would start things
            if ctrl_running:
                self.ctrl_btn.state(["!disabled"])  # Stop must remain enabled
            else:
                self.ctrl_btn.state(["disabled"])   # Start disabled while offline

            if browser_open:
                self.chart_btn.state(["!disabled"])  # Close must remain enabled
            else:
                self.chart_btn.state(["disabled"])   # Open disabled while offline
        else:
            # Online or unknown -> enable both; specific Start/Stop text handled elsewhere
            self.ctrl_btn.state(["!disabled"])
            self.chart_btn.state(["!disabled"])

    def _update_status_visual(self, ok):
        """Update the colored dot and label color based on connectivity state.
        ok=True -> green, ok=False -> red, ok=None -> gray.
        """
        if ok is True:
            fill = "#2e7d32"  # green
            fg = "#2e7d32"
        elif ok is False:
            fill = "#c62828"  # red
            fg = "#c62828"
        else:
            fill = "#999999"  # gray / unknown
            fg = "#666666"
        try:
            self.status_canvas.itemconfig(self._status_dot, fill=fill)
            # status_label is a tk.Label so we can set fg directly
            self.status_label.configure(fg=fg)
        except Exception:
            pass

    def save_config(self):
        # Interpret ping field as Redis endpoint; update config.json and launcher
        raw = self.ping_var.get().strip()
        chart_url = self.chart_var.get().strip()
        host = None
        port = None
        if "://" in raw:
            pr = urlparse(raw)
            host = pr.hostname or "localhost"
            port = pr.port or 6379
        else:
            if ":" in raw:
                h, p = raw.split(":", 1)
                host = h or "localhost"
                try:
                    port = int(p)
                except Exception:
                    port = 6379
            else:
                host = raw or "localhost"
                # keep existing port if available else default 6379
                port = int(self.config_data.get("redis", {}).get("port", 6379))

        self.config_data.setdefault("redis", {})["host"] = host
        self.config_data.setdefault("redis", {})["port"] = port
        # Store ping_url as typed for traceability
        self.config_data["ping_url"] = raw
        # Store chart_url
        self.config_data["chart_url"] = chart_url
        try:
            with CONFIG_PATH.open("w", encoding="utf-8") as f:
                json.dump(self.config_data, f, indent=2, ensure_ascii=False)
            # Cancel any ongoing connectivity wait and apply to running launcher
            try:
                self._cancel_event.set()
                # Replace with a new event for future waits
                self._cancel_event = threading.Event()
            except Exception:
                pass
            # apply to running launcher: prefer TCP format host:port
            launcher.PING_URL = f"{host}:{port}"
            if chart_url:
                launcher.CHART_URL = chart_url
            messagebox.showinfo("已保存", "配置已保存并生效。")
            # Optionally restart auto-start if nothing is running yet
            if not (self._ctrl_proc and self._ctrl_proc.poll() is None):
                self._start_on_launch()
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def toggle_ctrl(self):
        if self._ctrl_proc and self._ctrl_proc.poll() is None:
            # stop just the ctrl process (entire tree)
            try:
                launcher.terminate_process_tree(self._ctrl_proc)
            except Exception:
                pass
            try:
                launcher._processes.remove(self._ctrl_proc)
            except Exception:
                pass
            self._ctrl_proc = None
            return
        # start
        # MultiMonitorTool in launcher will move it; no monitor index needed here
        self._ctrl_proc = launcher.launch_process(launcher.CTRL_EXE)

    def toggle_chart(self):
        # toggle browser only
        if self._browser_opened:
            # try to terminate the browser process launched by launcher
            try:
                if hasattr(launcher, '_browser_process') and launcher._browser_process:
                    # Use same termination path as other children
                    launcher.terminate_process_tree(launcher._browser_process)
                    try:
                        launcher._processes.remove(launcher._browser_process)
                    except Exception:
                        pass
            except Exception:
                pass
            self._browser_opened = False
            # also clear launcher._browser_process
            try:
                launcher._browser_process = None
            except Exception:
                pass
            return
        launcher.open_browser_fullscreen(launcher.CHART_URL)
        self._browser_opened = True

    # chart backend toggle removed

    def _poll_status(self):
        # update statuses
        if self._ctrl_proc and self._ctrl_proc.poll() is None:
            self.ctrl_status.set("运行中")
            self.ctrl_btn.config(text="停止")
        else:
            self.ctrl_status.set("已停止")
            self.ctrl_btn.config(text="启动")
        # chart backend status removed
        # Prefer actual process check for browser
        if getattr(launcher, "_browser_process", None) and launcher._browser_process.poll() is None:
            self._browser_opened = True
        else:
            self._browser_opened = False

        if self._browser_opened:
            self.chart_status.set("已打开")
            self.chart_btn.config(text="关闭")
        else:
            self.chart_status.set("已关闭")
            self.chart_btn.config(text="打开")

        # Apply enable/disable rules after we set the button texts
        self._apply_connectivity_to_buttons()

        if self._polling:
            self.after(500, self._poll_status)

    def on_close(self):
        self._polling = False
        launcher.terminate_children()
        self.destroy()


if __name__ == "__main__":
    app = AppGUI()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
