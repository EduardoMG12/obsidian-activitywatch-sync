# Obsidian ActivityWatch Sync

Automate your daily productivity workflow in **Obsidian** by syncing time-tracking data from **ActivityWatch**, generating LLM-powered daily insights, and rolling over unfinished tasks — all without cluttering your vault.

---

## ✨ What It Does

Every day at **23:55** (or on demand), the script:

1. **Reads** your current daily note from your "All Day" folder
2. **Fetches** ActivityWatch data and categorizes time spent
3. **Generates** an AI summary of your day (optional, via OpenRouter/Ollama)
4. **Archives** the completed day to your planner's history
5. **Updates** a rolling `Metrics.md` table with daily stats
6. **Updates** a `Backlog.md` with any unchecked tasks
7. **Creates** a clean note for tomorrow, carrying over pending tasks

The result: **your "All Day" folder always contains only today** — a clean desk every morning.

---

## 📁 Vault Structure

```
📂 Your Vault/
├── 📂 0 - All Day/
│   └── 2026-04-24.md          ← Only today lives here
│
├── 📂 7 - Planner/
│   ├── 📂 Daily Notes/
│   │   └── 2026-04-23.md      ← Archived days
│   ├── Metrics.md             ← Auto-updated stats table
│   └── Backlog.md             ← Carried-over tasks
```

> Folder names (`0 - All Day`, `7 - Planner`, etc.) are fully configurable.

---

## 🚀 Quick Start

### 1. Prerequisites

- [ActivityWatch](https://activitywatch.net/) running on your machine
- Python 3.8+
- An Obsidian vault

### 2. Clone & Setup

```bash
git clone https://github.com/YOUR_USERNAME/obsidian-activitywatch-sync.git
cd obsidian-activitywatch-sync
./setup.sh
```

This will:
- Install the `requests` Python dependency
- Symlink `daily-roll` to `~/.local/bin/`
- Create a config file at `~/.config/obsidian-activitywatch-sync/config.json`
- Optionally add a cron job

### 3. Configure

```bash
# Edit the config
nano ~/.config/obsidian-activitywatch-sync/config.json
```

**Required:** Set `vault_path` to your Obsidian vault.

**Recommended:** Customize the `categories` section with your actual apps, websites, and projects. Use lowercase keywords.

See [`config.example.json`](./config.example.json) for the full reference.

### 4. Run

```bash
# Just sync ActivityWatch data into today's note
daily-roll --sync

# Full end-of-day rollover (archive + create tomorrow + metrics + backlog)
daily-roll --roll

# Sync + force LLM analysis
daily-roll --sync --llm
```

---

## ⚙️ Configuration

### Categories

Map apps, URLs, and window titles to productivity categories:

```json
"categories": {
  "work": {
    "emoji": "💼",
    "label": "Work",
    "keywords": ["slack", "jira", "vscode", "github.com/mycompany"]
  },
  "study": {
    "emoji": "📚",
    "label": "Study",
    "keywords": ["coursera", "overleaf", "zotero"]
  }
}
```

- Keywords are matched **case-insensitively** against window titles, app names, and URLs.
- Longer keywords get higher priority (more specific matches win).

### LLM Analysis (Optional)

Enable AI-powered daily summaries:

```json
"llm": {
  "enabled": true,
  "provider": "openrouter",
  "api_key": "sk-or-v1-...",
  "model": "anthropic/claude-3.5-haiku",
  "base_url": "https://openrouter.ai/api/v1"
}
```

**Free/Cheap providers:**
- [OpenRouter](https://openrouter.ai/) — generous free credits, many models
- [Ollama](https://ollama.com/) — run models locally for free

**Ollama example:**
```json
"llm": {
  "enabled": true,
  "provider": "ollama",
  "api_key": "ollama",
  "model": "llama3",
  "base_url": "http://localhost:11434/v1"
}
```

---

## 📝 Daily Note Format

The script expects a simple format:

```markdown
---
date: 2026-04-24
---

# 2026-04-24

## PESO
- [ ] Something weighing on my mind
- [x] Already done

## FOCO
- [ ] What actually matters today

## NOTES
Anything goes here.
```

- `- [ ]` tasks carry over to tomorrow and the backlog
- `- [x]` tasks are considered done and archived

---

## ⏰ Automation

The `setup.sh` can install a cron job. To do it manually:

```bash
# Run full rollover every night at 23:55
55 23 * * * ~/.local/bin/daily-roll --roll >> /tmp/daily-roll.log 2>&1
```

Logs: `tail -f /tmp/daily-roll.log`

---

## 🔒 Security & Privacy

- **No data leaves your machine** except to your chosen LLM provider (if enabled).
- Your `config.json` contains your API key — it lives **outside this repo** in `~/.config/`.
- This repo ships with `config.example.json` and `.gitignore` to prevent accidental commits of secrets.

---

## 🛠️ Advanced

### Custom config path

```bash
daily-roll --roll --config ~/Dropbox/my-sync-config.json
```

### Override vault path

```bash
daily-roll --roll --vault ~/Documents/Work-Vault
```

### Custom folder names

In `config.json`:

```json
"all_day_folder": "Inbox",
"planner_folder": "Meta",
"daily_notes_subfolder": "Archive",
"metrics_file": "Stats.md",
"backlog_file": "TODO.md"
```

---

## 🤝 Contributing

Issues and PRs welcome! Ideas:
- Support for Windows PowerShell setup
- Additional LLM providers
- Better categorization heuristics
- Dataview dashboard templates

---

## 📄 License

MIT
