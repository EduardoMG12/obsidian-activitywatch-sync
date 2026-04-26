#!/bin/bash
# Setup script for obsidian-activitywatch-sync
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/obsidian-activitywatch-sync"
BIN_DIR="$HOME/.local/bin"
SYSTEMD_USER="$HOME/.config/systemd/user"

echo "🚀 Setting up obsidian-activitywatch-sync..."

# 1. Python dependencies
echo "   📦 Checking Python dependencies..."
python3 -c "import requests" 2>/dev/null || {
    echo "   ⬇️  Installing requests..."
    pip3 install --user requests 2>/dev/null || pip install --user requests
}

# 2. Make executable
chmod +x "$REPO_DIR/daily_roll.py"

# 3. Symlink to PATH
mkdir -p "$BIN_DIR"
ln -sf "$REPO_DIR/daily_roll.py" "$BIN_DIR/daily-roll"

# 4. Ensure PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    echo "   ⚠️  Added ~/.local/bin to PATH. Run 'source ~/.bashrc' or restart your terminal."
fi

# 5. Create config if not exists
mkdir -p "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/config.json" ]]; then
    cp "$REPO_DIR/config.example.json" "$CONFIG_DIR/config.json"
    echo ""
    echo "   📝 Config created at: $CONFIG_DIR/config.json"
    echo "   ⚠️  EDIT THIS FILE before using: set your vault_path and API key (if using LLM)."
else
    echo "   ℹ️  Config already exists at $CONFIG_DIR/config.json"
fi

# 6. Systemd timers
mkdir -p "$SYSTEMD_USER"
cp "$REPO_DIR/systemd/"*.service "$REPO_DIR/systemd/"*.timer "$SYSTEMD_USER/" 2>/dev/null || true
systemctl --user daemon-reload

# Enable all timers
systemctl --user enable daily-roll-checkin-morning.timer 2>/dev/null || true
systemctl --user enable daily-roll-checkin-lunch.timer 2>/dev/null || true
systemctl --user enable daily-roll-checkin-evening.timer 2>/dev/null || true
systemctl --user enable daily-roll.timer 2>/dev/null || true

# Start all timers
systemctl --user start daily-roll-checkin-morning.timer 2>/dev/null || true
systemctl --user start daily-roll-checkin-lunch.timer 2>/dev/null || true
systemctl --user start daily-roll-checkin-evening.timer 2>/dev/null || true
systemctl --user start daily-roll.timer 2>/dev/null || true

echo ""
echo "   ⏰ Systemd timers activated:"
echo "      • 9:00  (Mon-Fri) — Check-in da manha"
echo "      • 12:00 (Mon-Fri) — Check-in do meio-dia"
echo "      • 18:00 (Mon-Fri) — Check-in fim de expediente"
echo "      • 23:55 (Daily)   — Rollover completo"

# 7. Remove old cron if exists
(crontab -l 2>/dev/null | grep -v "daily-roll" || true) | crontab - 2>/dev/null || true

# 8. Test ActivityWatch
echo ""
echo "   📡 Testing ActivityWatch..."
if curl -s http://localhost:5600/api/0/buckets/ > /dev/null 2>&1; then
    echo "   ✅ ActivityWatch is online"
else
    echo "   ⚠️  ActivityWatch not responding (start AW to activate)"
fi

echo ""
echo "🎉 Setup complete!"
echo ""
echo "Next steps:"
echo "   1. Edit: $CONFIG_DIR/config.json"
echo "   2. Run:  daily-roll --sync     (sync now)"
echo "   3. Run:  daily-roll --checkin  (light check-in)"
echo "   4. Run:  daily-roll --roll     (end-of-day full rollover)"
echo ""
echo "View timers:  systemctl --user list-timers"
echo "View logs:    journalctl --user -u daily-roll.service -f"
