# Obsidian ActivityWatch Sync

Automate your daily productivity workflow in **Obsidian** by syncing time-tracking data from **ActivityWatch**, generating LLM-powered daily insights, and rolling over unfinished tasks — all while keeping your workspace clean.

---

## ✨ What It Does

Every day at **23:55** (or on demand), the script:

1. **Reads** your current daily note from `0 - All Day/`
2. **Fetches** ActivityWatch data and categorizes time spent
3. **Generates** an AI summary of your day (optional, via OpenRouter/Ollama)
4. **Archives** the completed day to your history folder
5. **Updates** `0 - All Day/Metrics.md` with daily stats
6. **Updates** `0 - All Day/Backlog.md` with any unchecked tasks
7. **Creates** a clean note for tomorrow in `0 - All Day/`

The result: **open `0 - All Day/` and you have everything you need** — today's note, your backlog, and your metrics history.

---

## 📁 Vault Structure

```
📂 Your Vault/
├── 📂 0 - All Day/              ← Your daily workspace (3 files only)
│   ├── 2026-04-24.md           ← Today
│   ├── Backlog.md              ← Pending tasks
│   └── Metrics.md              ← Stats table
│
└── 📂 7 - Planner/Daily Notes/  ← Archived history
    └── 2026-04-23.md
```

> Folder names are fully configurable.

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

The script generates a practical but clean format:

```markdown
---
date: 2026-04-24
mood:
energy:
focus:
win_of_the_day:
---

# 2026-04-24

> 🎯 **Focus:**

---

## 🏋️ PESO (What's weighing on me)
- [ ] Something consuming mental energy
- [x] Already resolved

---

## ⚡ FOCO (What matters today)
- [ ] The one thing that moves the needle

---

## 📊 AW (ActivityWatch)
<!-- Populated by daily-roll -->

---

## 🧠 LLM Analysis
<!-- Populated by daily-roll -->

---

## 🏆 Win of the Day

---

## 📝 Notes
```

**Rules:**
- `- [ ]` = pending (carries over to tomorrow + backlog)
- `- [x]` = done (stays archived, doesn't carry over)
- Fill `mood`, `energy`, `focus` (1-10) and `win_of_the_day` manually for richer metrics

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
"archive_folder": "Archive/Daily",
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
