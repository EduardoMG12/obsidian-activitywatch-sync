#!/bin/bash
# Setup script for obsidian-activitywatch-sync
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/obsidian-activitywatch-sync"
BIN_DIR="$HOME/.local/bin"

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

# 6. Cron job (optional)
read -p "   ⏰ Add cron job for 23:55 daily? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    CRON_LINE="55 23 * * * $HOME/.local/bin/daily-roll --roll >> /tmp/daily-roll.log 2>&1"
    (crontab -l 2>/dev/null | grep -v "daily-roll" || true) > /tmp/crontab_tmp
    if ! grep -q "daily-roll" /tmp/crontab_tmp 2>/dev/null; then
        echo "$CRON_LINE" >> /tmp/crontab_tmp
        crontab /tmp/crontab_tmp
        echo "   ✅ Cron added (23:55 daily)"
    else
        echo "   ℹ️  Cron already exists"
    fi
    rm -f /tmp/crontab_tmp
fi

# 7. Test ActivityWatch
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
echo "   2. Run:  daily-roll --sync"
echo "   3. Run:  daily-roll --roll  (end-of-day full rollover)"
echo ""
