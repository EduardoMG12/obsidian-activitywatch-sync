#!/usr/bin/env python3
"""
Obsidian ActivityWatch Sync — Daily Roll

Automatiza o fim do dia no Obsidian:
  1. Coleta dados do ActivityWatch
  2. Atualiza o arquivo do dia atual
  3. Gera análise LLM (se configurado)
  4. Arquiva o dia na pasta histórica
  5. Atualiza métricas e backlog (dentro de 0 - All Day)
  6. Cria arquivo limpo para o próximo dia

Uso:
  daily_roll.py --roll              # Fim de dia completo (cron)
  daily_roll.py --sync              # Apenas sync AW no arquivo atual
  daily_roll.py --sync --llm        # Sync + análise LLM manual
  daily_roll.py --roll --config ~/minha-config.json
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone, date
from collections import defaultdict
from pathlib import Path

try:
    import requests
except ImportError:
    print("❌ Dependência faltando: pip install requests")
    sys.exit(1)

# ───────────────────────────────────────────────
# CONFIG DISCOVERY
# ───────────────────────────────────────────────

def discover_config_path(cli_path: str = None) -> Path:
    """Descobre onde está o config.json seguindo prioridade."""
    if cli_path:
        return Path(cli_path).expanduser().resolve()

    xdg = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    candidates = [
        Path(xdg) / "obsidian-activitywatch-sync" / "config.json",
        Path.home() / ".obsidian-activitywatch-sync" / "config.json",
        Path(__file__).parent / "config.json",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def load_config(path: Path):
    """Carrega e valida configuração."""
    if not path.exists():
        print(f"""❌ Configuração não encontrada: {path}

Crie o arquivo com:
  mkdir -p {path.parent}
  cp config.example.json {path}
  # Edite {path} com seu vault_path e categorias
""")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    if not cfg.get("vault_path"):
        print("❌ Config 'vault_path' é obrigatório.")
        sys.exit(1)

    return cfg


# ───────────────────────────────────────────────
# PATHS DO VAULT
# ───────────────────────────────────────────────

def resolve_vault_paths(cfg: dict):
    vault = Path(cfg["vault_path"]).expanduser().resolve()
    all_day = vault / cfg.get("all_day_folder", "0 - All Day")
    archive = vault / cfg.get("archive_folder", "7 - Planner/Daily Notes")

    return {
        "vault": vault,
        "all_day": all_day,
        "archive": archive,
        "metrics": all_day / cfg.get("metrics_file", "Metrics.md"),
        "backlog": all_day / cfg.get("backlog_file", "Backlog.md"),
    }


# ───────────────────────────────────────────────
# UTILS — MARKDOWN
# ───────────────────────────────────────────────

def parse_frontmatter(text: str):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    data = {}
    for line in m.group(1).strip().split("\n"):
        if ":" in line and not line.strip().startswith("-"):
            k, v = line.split(":", 1)
            data[k.strip()] = v.strip()
    return data, m.group(2)


def build_frontmatter(data: dict, body: str) -> str:
    lines = ["---"]
    for k, v in data.items():
        lines.append(f"{k}: {v}")
    lines.append("---\n")
    return "\n".join(lines) + body


def update_frontmatter_field(text: str, key: str, value) -> str:
    data, body = parse_frontmatter(text)
    data[key] = value
    return build_frontmatter(data, body)


def extract_tasks(text: str):
    pending = re.findall(r"^[\s]*- \[ \] (.*)$", text, re.MULTILINE)
    done = re.findall(r"^[\s]*- \[x\] (.*)$", text, re.MULTILINE)
    return pending, done


def format_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h{m:02d}" if h > 0 else f"{m}min"


def find_current_day_file(all_day: Path) -> Path:
    """Retorna o único arquivo de data em 0 - All Day/. Ignora Backlog e Metrics."""
    excluded = {"backlog.md", "metrics.md"}
    files = [f for f in all_day.iterdir()
             if f.suffix == ".md" and f.name.lower() not in excluded]
    if not files:
        raise FileNotFoundError(f"Nenhum arquivo de dia encontrado em {all_day}")
    if len(files) > 1:
        raise ValueError(f"Mais de um arquivo de dia em {all_day}: {[f.name for f in files]}")
    return files[0]


# ───────────────────────────────────────────────
# ACTIVITYWATCH
# ───────────────────────────────────────────────

def aw_get_buckets(host: str):
    try:
        r = requests.get(f"{host}/api/0/buckets/", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"⚠️ ActivityWatch offline: {e}")
        return []


def aw_get_events(host: str, bucket_id: str, start: str, end: str):
    url = f"{host}/api/0/buckets/{bucket_id}/events"
    try:
        r = requests.get(url, params={"start": start, "end": end}, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"⚠️ Erro no bucket {bucket_id}: {e}")
        return []


def classify_event(title: str, app: str, url: str, cats: dict) -> str:
    text = f"{title} {app} {url}".lower()
    scores = defaultdict(int)
    for cat, meta in cats.items():
        for kw in meta.get("keywords", []):
            if kw.lower() in text:
                scores[cat] += len(kw)
    return max(scores, key=scores.get) if scores else "uncategorized"


def sync_activitywatch(target_date: date, cfg: dict):
    host = cfg.get("aw_host", "http://localhost:5600")
    min_sec = cfg.get("min_seconds", 5)
    cats = cfg.get("categories", {})

    buckets = aw_get_buckets(host)
    start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    start_iso, end_iso = start.isoformat(), end.isoformat()

    # ActivityWatch returns buckets as a dict {id: metadata}
    if isinstance(buckets, dict):
        bucket_list = [{"id": k, **v} for k, v in buckets.items()]
    else:
        bucket_list = buckets

    relevant = [b for b in bucket_list if b.get("type", "").startswith(("app.", "web.", "currentwindow"))]
    all_events = []
    for b in relevant:
        all_events.extend(aw_get_events(host, b["id"], start_iso, end_iso))

    if not all_events:
        return None

    categories = defaultdict(float)
    app_times = defaultdict(float)

    for ev in all_events:
        dur = ev.get("duration", 0)
        if dur < min_sec:
            continue
        d = ev.get("data", {})
        cat = classify_event(d.get("title", ""), d.get("app", ""), d.get("url", ""), cats)
        categories[cat] += dur
        app_times[d.get("app", d.get("title", "?"))[:40]] += dur

    metrics = {cat: round(sec / 3600, 2) for cat, sec in categories.items()}
    for c in list(cats.keys()) + ["uncategorized"]:
        if c not in metrics:
            metrics[c] = 0.0

    total = sum(metrics.values())
    top_apps = sorted(app_times.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "metrics": metrics,
        "total_hours": round(total, 2),
        "top_apps": [(a, format_duration(s)) for a, s in top_apps],
        "raw_events": len(all_events)
    }


# ───────────────────────────────────────────────
# LLM
# ───────────────────────────────────────────────

def llm_analyze(day_content: str, aw_data: dict, cfg: dict) -> str:
    llm_cfg = cfg.get("llm", {})
    if not llm_cfg.get("enabled"):
        return "LLM desativada."

    api_key = llm_cfg.get("api_key", "")
    model = llm_cfg.get("model", "gpt-3.5-turbo")
    base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")

    if not api_key:
        return "LLM ativada mas sem API key."

    pending, done = extract_tasks(day_content)
    tasks_summary = f"Done: {len(done)}. Pending: {len(pending)}."

    aw_summary = f"Total: {aw_data['total_hours']}h. "
    aw_summary += ", ".join([f"{k}: {v}h" for k, v in aw_data["metrics"].items() if v > 0])

    system_prompt = llm_cfg.get("system_prompt",
        "You are a productivity assistant. Analyze the user's day briefly (3-4 sentences). "
        "What worked well, what to improve, and one insight. Be direct and encouraging. "
        "Respond in the user's language."
    )

    user_prompt = f"""Tasks: {tasks_summary}
ActivityWatch: {aw_summary}
Day content:
{day_content[:2000]}
"""

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        if "openrouter" in base_url:
            headers["HTTP-Referer"] = "https://github.com"
            headers["X-Title"] = "obsidian-activitywatch-sync"

        r = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 350
            },
            timeout=60
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"LLM error: {e}"


# ───────────────────────────────────────────────
# ROLLOVER
# ───────────────────────────────────────────────

def update_metrics_file(target_date: date, fm: dict, aw: dict, paths: dict, cats: dict, llm_enabled: bool):
    paths["archive"].mkdir(parents=True, exist_ok=True)

    cells = [target_date.isoformat()]
    for field in ["mood", "energy", "focus"]:
        cells.append(fm.get(field, ""))

    cat_order = list(cats.keys())
    for cat in cat_order:
        cells.append(str(aw["metrics"].get(cat, 0)) if aw else "0")
    cells.append(str(aw["total_hours"]) if aw else "0")
    cells.append(fm.get("win_of_the_day", ""))
    cells.append("✅" if llm_enabled else "—")

    line = "| " + " | ".join(cells) + " |"
    marker = "<!-- obsidian-activitywatch-sync marker -->"

    text = paths["metrics"].read_text(encoding="utf-8") if paths["metrics"].exists() else ""
    if marker in text:
        text = text.replace(marker, line + "\n" + marker)
    else:
        headers = ["Date", "Mood", "Energy", "Focus"] + [c.title() for c in cat_order] + ["Total", "Win", "LLM"]
        header_line = "| " + " | ".join(headers) + " |"
        sep_line = "|" + "|".join([" --- " for _ in headers]) + "|"
        text = header_line + "\n" + sep_line + "\n" + line + "\n" + marker + "\n"

    paths["metrics"].write_text(text, encoding="utf-8")
    print("   📊 Metrics updated")


def update_backlog_file(pending: list, paths: dict):
    critical, important, future = [], [], []
    for task in pending:
        t = task.strip()
        if not t:
            continue
        if t.startswith("🔴") or "critico" in t.lower() or "crítico" in t.lower():
            critical.append(t)
        elif t.startswith("🟡") or "importante" in t.lower():
            important.append(t)
        elif t.startswith("🟢") or "futuro" in t.lower():
            future.append(t)
        else:
            important.append(t)

    lines = ["# Backlog", "", "> Pending tasks carried over automatically.", ""]
    for name, items in [("🔴 Critical", critical), ("🟡 Important", important), ("🟢 Future", future)]:
        lines += [f"## {name}", ""]
        for t in items:
            lines.append(f"- [ ] {t}")
        lines.append("")

    paths["backlog"].write_text("\n".join(lines), encoding="utf-8")
    print(f"   📋 Backlog updated ({len(pending)} pending)")


def archive_and_create_next(current_file: Path, target_date: date, aw_data: dict, llm_text: str, paths: dict, cats: dict):
    paths["archive"].mkdir(parents=True, exist_ok=True)

    content = current_file.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(content)

    aw_section = "\n\n## AW\n\n"
    if aw_data:
        aw_section += f"**Total:** {aw_data['total_hours']}h\n\n"
        aw_section += "| Category | h |\n|----------|---|\n"
        for cat in cats:
            v = aw_data["metrics"].get(cat, 0)
            if v > 0:
                label = cats.get(cat, {}).get("label", cat)
                aw_section += f"| {label} | {v} |\n"
        aw_section += f"\n**Top apps:** {', '.join([f'{a} ({t})' for a, t in aw_data['top_apps']])}\n"
    else:
        aw_section += "ActivityWatch offline.\n"

    llm_section = f"\n\n## LLM\n\n{llm_text}\n"
    archived = content.rstrip() + aw_section + llm_section

    archived_path = paths["archive"] / f"{target_date.isoformat()}.md"
    archived_path.write_text(archived, encoding="utf-8")
    print(f"   📁 Archived: {archived_path.relative_to(paths['vault'])}")

    pending, _ = extract_tasks(content)
    next_date = target_date + timedelta(days=1)
    next_file = paths["all_day"] / f"{next_date.isoformat()}.md"

    next_body = f"""---
date: {next_date.isoformat()}
mood:
energy:
focus:
win_of_the_day:
---

# {next_date.isoformat()}

> 🎯 **Focus:**

---

## 🏋️ PESO (What's weighing on me)
"""
    for t in pending:
        next_body += f"- [ ] {t}\n"
    if not pending:
        next_body += "- [ ] \n"

    next_body += """
---

## ⚡ FOCO (What matters today)
- [ ] 

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

"""
    next_file.write_text(next_body, encoding="utf-8")
    print(f"   🌅 Created: {next_file.relative_to(paths['vault'])}")

    current_file.unlink()
    print(f"   🗑️  Removed: {current_file.name}")
    return pending


# ───────────────────────────────────────────────
# COMANDOS
# ───────────────────────────────────────────────

def cmd_roll(args, cfg, paths):
    current_file = find_current_day_file(paths["all_day"])
    print(f"\n🌙 Rollover: {current_file.name}")

    try:
        target_date = date.fromisoformat(current_file.stem)
    except ValueError:
        print("❌ Filename must be YYYY-MM-DD.md")
        sys.exit(1)

    content = current_file.read_text(encoding="utf-8")
    fm, _ = parse_frontmatter(content)

    print("   📡 Fetching ActivityWatch...")
    aw_data = sync_activitywatch(target_date, cfg)
    if aw_data:
        print(f"   ⏱️  {aw_data['total_hours']}h tracked")
    else:
        print("   ⚠️  No ActivityWatch data")

    llm_text = ""
    if args.llm or cfg.get("llm", {}).get("enabled"):
        print("   🧠 Generating LLM analysis...")
        llm_text = llm_analyze(content, aw_data or {"metrics": {}, "total_hours": 0}, cfg)
        print(f"   ✅ {llm_text[:80]}...")
    else:
        llm_text = "LLM disabled."

    if aw_data:
        content = update_frontmatter_field(content, "aw_total", aw_data["total_hours"])
        for cat, val in aw_data["metrics"].items():
            content = update_frontmatter_field(content, f"aw_{cat}", val)
    current_file.write_text(content, encoding="utf-8")

    pending = archive_and_create_next(current_file, target_date, aw_data, llm_text, paths, cfg.get("categories", {}))
    update_metrics_file(target_date, fm, aw_data, paths, cfg.get("categories", {}), cfg.get("llm", {}).get("enabled", False))
    update_backlog_file(pending, paths)

    print("\n✨ Done!")


def cmd_sync(args, cfg, paths):
    current_file = find_current_day_file(paths["all_day"])
    print(f"\n🔄 Sync AW: {current_file.name}")

    try:
        target_date = date.fromisoformat(current_file.stem)
    except ValueError:
        print("❌ Invalid filename")
        sys.exit(1)

    content = current_file.read_text(encoding="utf-8")
    aw_data = sync_activitywatch(target_date, cfg)

    if not aw_data:
        print("   ⚠️ No data")
        return

    content = update_frontmatter_field(content, "aw_total", aw_data["total_hours"])
    for cat, val in aw_data["metrics"].items():
        content = update_frontmatter_field(content, f"aw_{cat}", val)

    cats = cfg.get("categories", {})
    aw_block = "## AW\n\n**Total:** {0}h\n\n| Category | Time |\n|----------|------|\n".format(aw_data['total_hours'])
    for cat in cats:
        v = aw_data["metrics"].get(cat, 0)
        if v > 0:
            label = cats[cat].get("label", cat)
            aw_block += f"| {label} | {v}h |\n"
    aw_block += f"\n**Apps:** {', '.join([f'{a} ({t})' for a, t in aw_data['top_apps']])}\n"

    if "## AW" in content:
        content = re.sub(r"## AW\n.*?(?=\n## |\Z)", aw_block, content, flags=re.DOTALL)
    else:
        content = content.rstrip() + "\n\n" + aw_block + "\n"

    current_file.write_text(content, encoding="utf-8")
    print(f"   ✅ Updated: {aw_data['total_hours']}h")

    if args.llm:
        print("   🧠 Generating LLM...")
        text = llm_analyze(content, aw_data, cfg)
        print(f"   📝 {text[:200]}...")
        llm_block = f"\n## LLM\n\n{text}\n"
        if "## LLM" in content:
            content = re.sub(r"## LLM\n.*?(?=\n## |\Z)", llm_block, content, flags=re.DOTALL)
        else:
            content = content.rstrip() + "\n" + llm_block + "\n"
        current_file.write_text(content, encoding="utf-8")


# ───────────────────────────────────────────────
# MAIN
# ───────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Obsidian + ActivityWatch Daily Sync")
    parser.add_argument("--roll", action="store_true", help="End-of-day rollover")
    parser.add_argument("--sync", action="store_true", help="Only sync ActivityWatch now")
    parser.add_argument("--llm", action="store_true", help="Force LLM analysis")
    parser.add_argument("--config", type=str, default=None, help="Path to config.json")
    parser.add_argument("--vault", type=str, default=None, help="Override vault path")
    args = parser.parse_args()

    config_path = discover_config_path(args.config)
    cfg = load_config(config_path)

    if args.vault:
        cfg["vault_path"] = args.vault

    paths = resolve_vault_paths(cfg)

    # Garante que pastas existam
    paths["all_day"].mkdir(parents=True, exist_ok=True)
    paths["archive"].mkdir(parents=True, exist_ok=True)

    if args.roll:
        cmd_roll(args, cfg, paths)
    elif args.sync:
        cmd_sync(args, cfg, paths)
    else:
        now = datetime.now().hour
        if now >= 22 or now <= 5:
            print("🌙 Night time detected. Running rollover...")
            cmd_roll(args, cfg, paths)
        else:
            print("🔄 Use --roll or --sync.\n")
            parser.print_help()
