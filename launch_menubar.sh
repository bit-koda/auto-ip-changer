#!/usr/bin/env zsh
# ─────────────────────────────────────────────────────────────
#  Auto IP Changer — Background Controller & Launcher
#  Usage:
#    ~/.tor-ip-changer/launch_menubar.sh          # start in background
#    ~/.tor-ip-changer/launch_menubar.sh stop     # stop background app
#    ~/.tor-ip-changer/launch_menubar.sh status   # check status
# ─────────────────────────────────────────────────────────────

APP_DIR="$HOME/.tor-ip-changer"
PID_FILE="$APP_DIR/menubar.pid"
PYTHON="/opt/anaconda3/bin/python3"

is_running() {
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        return 0
    fi
    # Check if process is running even without pid file
    pgrep -f "menubar_app.py" >/dev/null 2>&1
    return $?
}

start() {
    if is_running; then
        pid=$(pgrep -f "menubar_app.py" | head -1)
        echo "⚠️  Auto IP Changer is already running in the background (PID: $pid)."
        echo "   Look for the 🛡️ icon in your top menu bar."
        exit 0
    fi

    cd "$APP_DIR"
    nohup "$PYTHON" "$APP_DIR/menubar_app.py" > "$APP_DIR/menubar.log" 2>&1 &
    local new_pid=$!
    echo "$new_pid" > "$PID_FILE"

    echo "✅ Auto IP Changer launched in background!"
    echo "   PID: $new_pid"
    echo "   Log: $APP_DIR/menubar.log"
    echo "   🛡️  Menu Bar icon appeared at the top of your screen."
    echo "   ⚡ IP protection and auto-rotation started automatically."
}

stop() {
    echo "⏹ Stopping Auto IP Changer..."
    # Turn off system proxy on Wi-Fi if enabled
    networksetup -setsocksfirewallproxystate "Wi-Fi" off 2>/dev/null
    
    # Kill Python menubar app
    if [[ -f "$PID_FILE" ]]; then
        pid=$(cat "$PID_FILE")
        kill "$pid" 2>/dev/null
        rm -f "$PID_FILE"
    fi
    pkill -f "menubar_app.py" 2>/dev/null
    pkill -f "tor -f.*tor-ip-changer" 2>/dev/null
    
    echo "✅ Auto IP Changer stopped and network restored."
}

status() {
    if is_running; then
        pid=$(pgrep -f "menubar_app.py" | head -1)
        echo "🟢 Auto IP Changer is running in background (PID: $pid)"
        echo "--- Recent Activity Log ---"
        tail -8 "$APP_DIR/menubar.log" 2>/dev/null
    else
        echo "⚫ Auto IP Changer is stopped."
    fi
}

case "$1" in
    stop|--stop)
        stop
        ;;
    status|--status)
        status
        ;;
    *)
        start
        ;;
esac
