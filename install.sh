#!/usr/bin/env bash
set -e

echo "🛡️  Installing Auto IP Changer for macOS..."

TARGET_DIR="$HOME/.tor-ip-changer"
mkdir -p "$TARGET_DIR/data"

# Check Homebrew & Tor
if ! command -v tor >/dev/null 2>&1; then
    echo "📦 Tor not found. Installing via Homebrew..."
    brew install tor
fi

# Copy scripts & configs
cp menubar_app.py "$TARGET_DIR/"
cp torrc "$TARGET_DIR/"
cp launch_menubar.sh "$TARGET_DIR/"
chmod +x "$TARGET_DIR/launch_menubar.sh" "$TARGET_DIR/menubar_app.py"

# Copy App bundle
mkdir -p "$HOME/Applications"
cp -r AutoIPChanger.app "$HOME/Applications/"

# Create CLI alias in shell profiles
for rc in "$HOME/.zshrc" "$HOME/.bash_profile"; do
    if [ -f "$rc" ] && ! grep -q "auto-ip-changer" "$rc"; then
        echo 'alias ip-changer="$HOME/.tor-ip-changer/launch_menubar.sh"' >> "$rc"
    fi
done

echo "✅ Installation complete!"
echo "🚀 Run: ~/.tor-ip-changer/launch_menubar.sh"
echo "   Or double-click AutoIPChanger in ~/Applications"
