#!/usr/bin/env bash
echo "🗑️  Uninstalling Auto IP Changer..."

# Restore system proxy
networksetup -setsocksfirewallproxystate "Wi-Fi" off 2>/dev/null || true

# Stop processes
pkill -f "menubar_app.py" 2>/dev/null || true
pkill -f "tor -f.*tor-ip-changer" 2>/dev/null || true

# Remove LaunchAgent
rm -f "$HOME/Library/LaunchAgents/com.user.autoipchanger.plist"

# Remove App and config
rm -rf "$HOME/.tor-ip-changer"
rm -rf "$HOME/Applications/AutoIPChanger.app"

echo "✅ Auto IP Changer successfully uninstalled."
