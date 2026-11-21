#!/usr/bin/env python3
"""
🛡️ Auto IP Changer — macOS Menu Bar App (v2.2)
- Automatically starts IP rotation and traffic protection on launch
- Background execution via macOS LaunchAgent (Start at Mac Login)
- Thread-safe PyObjC / Cocoa UI updates via AppHelper.callAfter
- Automatic Tor process management & circuit rotation (NEWNYM)
- Reliable curl-based IP & leak detection
- macOS System-wide SOCKS5 Proxy automation
- Real-time notifications on IP changes
"""

import atexit
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time

from Foundation import NSThread
from PyObjCTools import AppHelper
import rumps

# ─────────────────────────── Config ─────────────────────────
APP_DIR          = os.path.expanduser("~/.tor-ip-changer")
TOR_BIN          = "/opt/homebrew/bin/tor"
TOR_CONFIG       = os.path.join(APP_DIR, "torrc")
TOR_DATA_DIR     = os.path.join(APP_DIR, "data")
SETTINGS_FILE    = os.path.join(APP_DIR, "settings.json")
PID_FILE         = os.path.join(APP_DIR, "menubar.pid")
LAUNCH_AGENT     = os.path.expanduser("~/Library/LaunchAgents/com.user.autoipchanger.plist")

SOCKS_PORT       = 9050
HTTP_PORT        = 9080
CONTROL_PORT     = 9051
PASSWORD         = "autoipchanger"
DEFAULT_INTERVAL = 30            # seconds between IP rotations

IP_SERVICES = [
    "https://api.ipify.org",
    "https://icanhazip.com",
    "https://ifconfig.me/ip",
]


# ─────────────────── Settings Persistence ───────────────────

def load_settings() -> dict:
    defaults = {
        "auto_start": True,
        "start_at_login": True,
        "notifications": True,
        "interval": DEFAULT_INTERVAL,
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
                defaults.update(data)
        except Exception:
            pass
    return defaults


def save_settings(data: dict):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def configure_launch_agent(enable: bool):
    """Register or remove the macOS LaunchAgent for start-at-login."""
    try:
        os.makedirs(os.path.dirname(LAUNCH_AGENT), exist_ok=True)
        if enable:
            plist_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.autoipchanger</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/anaconda3/bin/python3</string>
        <string>{os.path.join(APP_DIR, "menubar_app.py")}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{os.path.join(APP_DIR, "menubar.log")}</string>
    <key>StandardErrorPath</key>
    <string>{os.path.join(APP_DIR, "menubar_err.log")}</string>
    <key>ProcessType</key>
    <string>Interactive</string>
</dict>
</plist>
"""
            with open(LAUNCH_AGENT, "w") as f:
                f.write(plist_xml)
        else:
            if os.path.exists(LAUNCH_AGENT):
                os.remove(LAUNCH_AGENT)
    except Exception:
        pass


# ────────────────── Thread-Safe UI Dispatch ──────────────────

def dispatch_main(func):
    """Decorator ensuring a method or function executes on the Cocoa main thread."""
    def wrapper(*args, **kwargs):
        if NSThread.isMainThread():
            return func(*args, **kwargs)
        else:
            AppHelper.callAfter(func, *args, **kwargs)
    return wrapper


# ─────────────────── System Proxy Helpers ────────────────────

def get_network_services() -> list:
    """Return list of active macOS network services (e.g. Wi-Fi)."""
    try:
        r = subprocess.run(["networksetup", "-listallnetworkservices"],
                           capture_output=True, text=True, timeout=5)
        services = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if line and not line.startswith("An asterisk") and not line.startswith("*"):
                services.append(line)
        return services if services else ["Wi-Fi"]
    except Exception:
        return ["Wi-Fi"]


def enable_system_proxy(services: list):
    """Configure and turn on macOS SOCKS5, HTTP, and HTTPS proxies on specified services."""
    for svc in services:
        try:
            # SOCKS5 Proxy (Port 9050)
            subprocess.run(["networksetup", "-setsocksfirewallproxy", svc, "127.0.0.1", str(SOCKS_PORT)], capture_output=True, timeout=5)
            subprocess.run(["networksetup", "-setsocksfirewallproxystate", svc, "on"], capture_output=True, timeout=5)
            # Web Proxy HTTP (Port 9080)
            subprocess.run(["networksetup", "-setwebproxy", svc, "127.0.0.1", str(HTTP_PORT)], capture_output=True, timeout=5)
            subprocess.run(["networksetup", "-setwebproxystate", svc, "on"], capture_output=True, timeout=5)
            # Secure Web Proxy HTTPS (Port 9080)
            subprocess.run(["networksetup", "-setsecurewebproxy", svc, "127.0.0.1", str(HTTP_PORT)], capture_output=True, timeout=5)
            subprocess.run(["networksetup", "-setsecurewebproxystate", svc, "on"], capture_output=True, timeout=5)
        except Exception:
            pass


def disable_system_proxy(services: list):
    """Turn off macOS SOCKS, HTTP, and HTTPS proxies on specified services."""
    for svc in services:
        try:
            subprocess.run(["networksetup", "-setsocksfirewallproxystate", svc, "off"], capture_output=True, timeout=5)
            subprocess.run(["networksetup", "-setwebproxystate", svc, "off"], capture_output=True, timeout=5)
            subprocess.run(["networksetup", "-setsecurewebproxystate", svc, "off"], capture_output=True, timeout=5)
        except Exception:
            pass


# ─────────────────────── IP Fetching ─────────────────────────

def _extract_ip(text: str) -> str:
    """Extract IPv4 or IPv6 address from raw response string."""
    m = re.search(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', text)
    if m:
        return m.group(0)
    m6 = re.search(r'\b(?:[a-fA-F0-9]{1,4}:){7}[a-fA-F0-9]{1,4}\b', text)
    if m6:
        return m6.group(0)
    return ""


def get_ip_via_tor() -> str:
    """Fetch exit IP routed through Tor's SOCKS5 proxy using curl."""
    for url in IP_SERVICES:
        try:
            r = subprocess.run(
                ["curl", "-s", "--socks5-hostname", f"127.0.0.1:{SOCKS_PORT}",
                 "--max-time", "6", url],
                capture_output=True, text=True, timeout=8
            )
            ip = _extract_ip(r.stdout)
            if ip:
                return ip
        except Exception:
            continue
    return ""


def get_real_ip() -> str:
    """Fetch direct public IP (bypassing proxies) using curl."""
    for url in IP_SERVICES:
        try:
            r = subprocess.run(
                ["curl", "-s", "--noproxy", "*", "--max-time", "5", url],
                capture_output=True, text=True, timeout=7
            )
            ip = _extract_ip(r.stdout)
            if ip:
                return ip
        except Exception:
            continue
    return ""


# ────────────────── Tor Control Protocol ─────────────────────

def tor_control(cmd: str) -> bool:
    """Send command to Tor control port and check for 250 OK."""
    try:
        s = socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=4)
        s.sendall(f'AUTHENTICATE "{PASSWORD}"\r\n'.encode())
        res = s.recv(1024).decode()
        if not res.startswith("250"):
            s.close()
            return False
        s.sendall(f"{cmd}\r\n".encode())
        resp = s.recv(4096).decode()
        s.close()
        return "250" in resp
    except Exception:
        return False


# ────────────────────── Menu Bar App ─────────────────────────

class AutoIPChangerApp(rumps.App):
    def __init__(self):
        super().__init__("🛡️", quit_button=None)

        self._cfg = load_settings()
        self._tor_proc        = None
        self._running         = False
        self._interval        = self._cfg.get("interval", DEFAULT_INTERVAL)
        self._auto_start      = self._cfg.get("auto_start", True)
        self._start_at_login  = self._cfg.get("start_at_login", True)
        self._notif_enabled   = self._cfg.get("notifications", True)

        self._current_tor_ip  = ""
        self._real_ip         = ""
        self._rotation_count  = 0
        self._stop_event      = threading.Event()
        self._services        = get_network_services()

        # Write PID file
        try:
            with open(PID_FILE, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass

        # Sync LaunchAgent state
        configure_launch_agent(self._start_at_login)

        # Menu items
        self._status_item    = rumps.MenuItem("⚫  Status: Stopped")
        self._tor_ip_item    = rumps.MenuItem("🌐  Tor IP:  —")
        self._real_ip_item   = rumps.MenuItem("🏠  Real IP: (detecting…)")
        self._leak_item      = rumps.MenuItem("🔒  Leak Check: —")
        self._count_item     = rumps.MenuItem("🔄  Rotations: 0")

        self._start_item     = rumps.MenuItem("▶  Start (Protect All Traffic)", callback=self.start_rotation)
        self._stop_item      = rumps.MenuItem("⏹  Stop & Restore Network",      callback=self.stop_rotation)
        self._now_item       = rumps.MenuItem("⚡  Rotate IP Now",              callback=self.rotate_now)
        self._copy_item      = rumps.MenuItem("📋  Copy Tor IP",                callback=self.copy_ip)
        self._leaktest_item  = rumps.MenuItem("🔍  Run Leak Test",              callback=self.run_leak_test)

        # Interval sub-menu
        self._iv_menu = rumps.MenuItem(f"⏱  Rotation Interval ({self._interval}s)")
        for secs, label in [(10, "10 sec"), (30, "30 sec"), (60, "1 min"), (120, "2 min"), (300, "5 min")]:
            self._iv_menu.add(rumps.MenuItem(f"  {label}", callback=self._make_interval_cb(secs, label)))

        # Preferences sub-menu
        self._pref_menu = rumps.MenuItem("⚙️  Preferences")
        self._auto_start_item = rumps.MenuItem(
            f"⚡ Auto-Start Protection: {'ON' if self._auto_start else 'OFF'}",
            callback=self.toggle_auto_start
        )
        self._login_item = rumps.MenuItem(
            f"💻 Start at Mac Login: {'ON' if self._start_at_login else 'OFF'}",
            callback=self.toggle_start_at_login
        )
        self._notif_item = rumps.MenuItem(
            f"🔔 Notifications: {'ON' if self._notif_enabled else 'OFF'}",
            callback=self.toggle_notif
        )
        self._pref_menu.add(self._auto_start_item)
        self._pref_menu.add(self._login_item)
        self._pref_menu.add(self._notif_item)

        # Proxy info sub-menu
        self._proxy_menu = rumps.MenuItem("🔌  Proxy Info")
        self._proxy_menu.add(rumps.MenuItem(f"  SOCKS5: 127.0.0.1:{SOCKS_PORT}"))
        self._proxy_menu.add(rumps.MenuItem(f"  HTTP:   127.0.0.1:{HTTP_PORT}"))
        self._proxy_menu.add(rumps.MenuItem("  System proxy auto-configured on Start"))

        self._quit_item = rumps.MenuItem("Quit Auto IP Changer", callback=self.quit_app)

        self.menu = [
            self._status_item,
            self._tor_ip_item,
            self._real_ip_item,
            self._leak_item,
            self._count_item,
            None,
            self._start_item,
            self._stop_item,
            self._now_item,
            None,
            self._iv_menu,
            self._pref_menu,
            self._proxy_menu,
            None,
            self._leaktest_item,
            self._copy_item,
            None,
            self._quit_item,
        ]

        self._sync_ui_state()

        # Probe initial real IP
        threading.Thread(target=self._initial_ip_probe, daemon=True).start()

        # If auto-start is enabled, immediately begin protection in background
        if self._auto_start:
            threading.Thread(target=self._delayed_auto_start, daemon=True).start()

    def _delayed_auto_start(self):
        time.sleep(1)
        self.start_rotation(None)

    # ── UI State Synchronization (Thread-Safe) ────────────────

    @dispatch_main
    def _sync_ui_state(self):
        if self._running:
            if self._current_tor_ip:
                self.title = f"🛡 {self._current_tor_ip}"
                self._status_item.title = "🟢  Status: Running  •  All traffic protected"
                self._now_item.set_callback(self.rotate_now)
            else:
                self.title = "🛡 …"
                self._status_item.title = "🟡  Status: Connecting to Tor…"
                self._now_item.set_callback(None)
            self._start_item.set_callback(None)
            self._stop_item.set_callback(self.stop_rotation)
        else:
            self.title = "🛡️"
            self._status_item.title = "⚫  Status: Stopped"
            self._start_item.set_callback(self.start_rotation)
            self._stop_item.set_callback(None)
            self._now_item.set_callback(None)

        self._tor_ip_item.title  = f"🌐  Tor IP:  {self._current_tor_ip if self._current_tor_ip else '—'}"
        self._real_ip_item.title = f"🏠  Real IP: {self._real_ip if self._real_ip else '—'}"
        self._count_item.title   = f"🔄  Rotations: {self._rotation_count}"

    @dispatch_main
    def _set_leak_status(self, text: str):
        self._leak_item.title = text

    @dispatch_main
    def _notify(self, title: str, subtitle: str, msg: str):
        if self._notif_enabled:
            try:
                rumps.notification(title, subtitle, msg, sound=False)
            except Exception:
                pass

    def _make_interval_cb(self, secs, label):
        def cb(_):
            self._interval = secs
            self._cfg["interval"] = secs
            save_settings(self._cfg)
            self._iv_menu.title = f"⏱  Rotation Interval ({label})"
            self._notify("Auto IP Changer", "Interval Updated", f"Rotating IP every {label}")
        return cb

    # ── IP Probing ───────────────────────────────────────────

    def _initial_ip_probe(self):
        self._real_ip = get_real_ip()
        self._sync_ui_state()

    def run_leak_test(self, _):
        def _test():
            if not self._running:
                self._set_leak_status("🔒  Leak Check: Start protection first")
                return
            self._set_leak_status("🔍  Testing for leaks…")
            tor_ip = get_ip_via_tor()
            real_ip = get_real_ip() or self._real_ip
            self._real_ip = real_ip
            self._current_tor_ip = tor_ip
            self._sync_ui_state()

            if not tor_ip:
                self._set_leak_status("⏳  Tor circuit is not ready yet")
            elif tor_ip == real_ip:
                self._set_leak_status(f"⚠️  LEAK: IP matches Real IP ({tor_ip})")
                self._notify("⚠️ Leak Detected", "IPs Match!", f"Traffic is showing real IP: {real_ip}")
            else:
                self._set_leak_status(f"✅  No Leak: Tor ({tor_ip}) ≠ Real ({real_ip})")
                self._notify("✅ Protected!", f"Tor IP: {tor_ip}", f"Real IP ({real_ip}) is hidden")
        threading.Thread(target=_test, daemon=True).start()

    # ── Tor Process Lifecycle ────────────────────────────────

    def _start_tor(self) -> bool:
        if tor_control("GETINFO version"):
            return True

        subprocess.run(["pkill", "-f", "tor -f.*tor-ip-changer"], capture_output=True)
        time.sleep(1)

        os.makedirs(TOR_DATA_DIR, exist_ok=True)
        try:
            self._tor_proc = subprocess.Popen(
                [TOR_BIN, "-f", TOR_CONFIG],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self._notify("Error", "Tor not found", f"Could not find {TOR_BIN}")
            return False

        for _ in range(12):
            if self._stop_event.is_set():
                return False
            if tor_control("GETINFO version"):
                return True
            time.sleep(1)
        return False

    def _stop_tor(self):
        if self._tor_proc and self._tor_proc.poll() is None:
            self._tor_proc.terminate()
            try:
                self._tor_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._tor_proc.kill()
        self._tor_proc = None

    # ── Background Worker Loop ───────────────────────────────

    def _run_loop(self):
        self._set_leak_status("⏳  Starting Tor…")
        ready = self._start_tor()
        if not ready:
            self._running = False
            self._notify("Auto IP Changer", "Startup Error", "Tor could not connect.")
            self._sync_ui_state()
            return

        self._set_leak_status("⏳  Enabling macOS proxy…")
        enable_system_proxy(self._services)

        self._set_leak_status("⏳  Building circuit…")
        tor_ip = ""
        for _ in range(15):
            if self._stop_event.is_set():
                break
            tor_ip = get_ip_via_tor()
            if tor_ip:
                break
            time.sleep(2)

        self._current_tor_ip = tor_ip
        if tor_ip and tor_ip != self._real_ip:
            self._set_leak_status(f"✅  No Leak  •  IP: {tor_ip}")
            self._notify("🛡 Protected!", f"Current Tor IP: {tor_ip}", f"Rotating every {self._interval}s")
        elif tor_ip:
            self._set_leak_status("⚠️  Warning: Real and Tor IPs match")
        else:
            self._set_leak_status("⚠️  Circuit delayed — retrying…")

        self._sync_ui_state()

        while self._running and not self._stop_event.is_set():
            for _ in range(self._interval * 2):
                if self._stop_event.is_set():
                    break
                time.sleep(0.5)

            if self._stop_event.is_set():
                break

            self._rotate_circuit()

        disable_system_proxy(self._services)
        self._stop_tor()

    def _rotate_circuit(self):
        ok = tor_control("SIGNAL NEWNYM")
        if ok:
            time.sleep(3)
            new_ip = get_ip_via_tor()
            prev_ip = self._current_tor_ip
            self._rotation_count += 1
            if new_ip:
                self._current_tor_ip = new_ip
                if new_ip != self._real_ip:
                    self._set_leak_status(f"✅  No Leak  •  #{self._rotation_count}")
                else:
                    self._set_leak_status(f"⚠️  Possible Leak  •  #{self._rotation_count}")
                self._sync_ui_state()
                if new_ip != prev_ip:
                    self._notify(
                        "🛡 IP Rotated!",
                        f"New Tor IP: {new_ip}",
                        f"Previous: {prev_ip or '—'}  •  Rotation #{self._rotation_count}"
                    )
            else:
                self._set_leak_status(f"⏳  Circuit changing…  •  #{self._rotation_count}")
                self._sync_ui_state()

    # ── User Actions ─────────────────────────────────────────

    def start_rotation(self, _):
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        self._rotation_count = 0
        self._current_tor_ip = ""
        self._sync_ui_state()
        threading.Thread(target=self._run_loop, daemon=True).start()

    def stop_rotation(self, _):
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        disable_system_proxy(self._services)
        self._stop_tor()
        self._current_tor_ip = ""
        self._set_leak_status("🔒  Leak Check: —")
        self._sync_ui_state()
        self._notify("Auto IP Changer", "Protection Stopped", "System proxy turned off. Real IP restored.")

    def rotate_now(self, _):
        if not self._running:
            return
        threading.Thread(target=self._rotate_circuit, daemon=True).start()

    def copy_ip(self, _):
        ip = self._current_tor_ip or self._real_ip
        if ip:
            subprocess.run(["pbcopy"], input=ip.encode(), check=False)
            self._notify("Auto IP Changer", "Copied", f"{ip} copied to clipboard")

    def toggle_auto_start(self, _):
        self._auto_start = not self._auto_start
        self._cfg["auto_start"] = self._auto_start
        save_settings(self._cfg)
        self._auto_start_item.title = f"⚡ Auto-Start Protection: {'ON' if self._auto_start else 'OFF'}"

    def toggle_start_at_login(self, _):
        self._start_at_login = not self._start_at_login
        self._cfg["start_at_login"] = self._start_at_login
        save_settings(self._cfg)
        configure_launch_agent(self._start_at_login)
        self._login_item.title = f"💻 Start at Mac Login: {'ON' if self._start_at_login else 'OFF'}"
        status = "enabled" if self._start_at_login else "disabled"
        self._notify("Auto IP Changer", "Startup Setting Changed", f"Start at Mac Login is now {status}")

    def toggle_notif(self, _):
        self._notif_enabled = not self._notif_enabled
        self._cfg["notifications"] = self._notif_enabled
        save_settings(self._cfg)
        self._notif_item.title = f"🔔 Notifications: {'ON' if self._notif_enabled else 'OFF'}"

    def quit_app(self, _):
        self._running = False
        self._stop_event.set()
        disable_system_proxy(self._services)
        self._stop_tor()
        if os.path.exists(PID_FILE):
            try:
                os.remove(PID_FILE)
            except Exception:
                pass
        rumps.quit_application()


# ── Cleanup Handlers ─────────────────────────────────────────

def _cleanup_on_exit():
    try:
        disable_system_proxy(get_network_services())
    except Exception:
        pass
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

atexit.register(_cleanup_on_exit)
signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))


if __name__ == "__main__":
    app = AutoIPChangerApp()
    app.run()
