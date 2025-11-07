# Dual Launcher

A simple Python launcher that starts two existing applications and positions them on different monitors:

1. `apps/usv_remote_ctrl.exe` (PyQt, already fullscreen)
2. `apps/chart.exe` (Flask backend providing a web UI at http://127.0.0.1:5000) plus a fullscreen browser window on a chosen monitor.

## Features
- Starts both executables.
- Waits until the Flask port (5000) is reachable before opening the browser (with a timeout fallback).
- Attempts kiosk / app mode in Chrome or Edge with fullscreen on a specific monitor.
- Graceful shutdown on Ctrl+C (terminates child processes).
- Monitor enumeration via WinAPI; choose which monitor each app uses.
- Post-launch window adjuster: if browser doesn't honor size flags, a background thread will locate the first window of the launched browser process and maximize/move it to the target monitor.

## Requirements
- Windows
- Python 3.10+ (for running `launcher.py`)
- Chrome or Edge installed (recommended) for best fullscreen experience.

## Usage
From the project root:

```cmd
python launcher.py
```

The script will:
1. Launch the PyQt app (`usv_remote_ctrl.exe`).
2. Launch the chart backend (`chart.exe`).
3. Wait for port 5000 to respond.
4. Open the chart UI fullscreen on the configured monitor.

Press `Ctrl+C` in the console to shutdown both processes.

## Configuration
Adjust top constants in `launcher.py` as needed:

- `TARGET_MONITOR_FOR_CHART = 1`  -> Which monitor (0-based) to place the browser.
- `TARGET_MONITOR_FOR_CTRL = 0`   -> Monitor index for the PyQt exe (if it respects `TARGET_MONITOR` env var—otherwise it will use its default).
- `CHART_BROWSER = ""`           -> Force specific browser: `"chrome"`, `"edge"`, or leave blank to try both.
- `PORT_TIMEOUT = 30`             -> Seconds to wait for Flask backend.
- `START_BROWSER_DELAY = 1.0`     -> Extra delay after port opens before launching browser.

If you have more than two monitors, adjust indices accordingly. The script enumerates monitors using WinAPI order.

## Browser Fallback
If Chrome / Edge aren’t found, it falls back to the system default browser (you may need to manually set fullscreen (F11)).

## Packaging (Optional)
You can build the launcher into an executable with PyInstaller:

```cmd
pyinstaller --onefile launcher.py
```

Result will be in `dist/launcher.exe`.

## Troubleshooting
- If the browser opens on the wrong monitor, confirm monitor indices by temporarily adding a print of `list_monitors()` in the script.
- If port 5000 never opens, verify `chart.exe` actually starts the Flask server and no firewall rules block it.
- Use Task Manager to ensure child processes terminate after closing.
- Some Chromium versions ignore `--start-fullscreen` with `--app=`; the script falls back to normal tab mode variants and then forcibly maximizes the window. If you still see window chrome, press F11 once (it will usually persist for future launches).

## Future Enhancements (Ideas)
- Add a small GUI for selecting monitors before launch.
- Log to a file with rotation.
- Add watchdog to auto-restart if one process crashes.

---
MIT License (add a LICENSE file if distributing).
