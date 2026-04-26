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


def extract_section(text: str, heading: str) -> str:
    """Extrai o conteúdo bruto de uma seção markdown (## heading)."""
    pattern = rf"(?:^|\n)## {re.escape(heading)}\n(.*?)((?=\n## )|\Z)"
    m = re.search(pattern, text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def format_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h{m:02d}" if h > 0 else f"{m}min"


def find_current_day_file(all_day: Path) -> Path:
    """Retorna o único arquivo de data em 0 - All Day/. Ignora arquivos fixos."""
    excluded = {"backlog.md", "metrics.md", "cursos.md"}
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
    max_dur = cfg.get("max_event_duration_seconds", 7200)  # 2h default, idle filter
    cats = cfg.get("categories", {})

    buckets = aw_get_buckets(host)
    start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    start_iso, end_iso = start.isoformat(), end.isoformat()

    if isinstance(buckets, dict):
        bucket_list = [{"id": k, **v} for k, v in buckets.items()]
    else:
        bucket_list = buckets

    # Separate primary (window) and secondary (web) buckets to avoid double-counting time
    primary_buckets = [b for b in bucket_list if b.get("type", "").startswith("currentwindow")]
    web_buckets = [b for b in bucket_list if b.get("type", "").startswith("web.")]

    # Fetch primary events (these define the actual time spent)
    primary_events = []
    for b in primary_buckets:
        primary_events.extend(aw_get_events(host, b["id"], start_iso, end_iso))

    if not primary_events:
        return None

    # Fetch web events to enrich URLs for better categorization
    web_events = []
    for b in web_buckets:
        web_events.extend(aw_get_events(host, b["id"], start_iso, end_iso))

    # Build a lookup: for each web event, map its time range to its URL
    # We'll match by timestamp overlap for enrichment
    web_by_time = []
    for ev in web_events:
        ts = ev.get("timestamp", "")
        dur = ev.get("duration", 0)
        url = ev.get("data", {}).get("url", "")
        if url and ts:
            web_by_time.append({"ts": ts, "dur": dur, "url": url})

    def find_url_for_event(ev):
        """Find a matching web URL for a window event based on timestamp proximity."""
        ev_ts = ev.get("timestamp", "")
        if not ev_ts:
            return ""
        # Simple heuristic: find web event with closest timestamp
        best_url = ""
        best_diff = float("inf")
        for w in web_by_time:
            # naive string compare; for production use datetime parsing
            diff = abs((datetime.fromisoformat(ev_ts.replace("Z", "+00:00")) -
                       datetime.fromisoformat(w["ts"].replace("Z", "+00:00"))).total_seconds())
            if diff < best_diff and diff < 10:  # within 10 seconds
                best_diff = diff
                best_url = w["url"]
        return best_url

    categories = defaultdict(float)
    app_times = defaultdict(float)

    for ev in primary_events:
        dur = ev.get("duration", 0)
        if dur < min_sec:
            continue
        if dur > max_dur:
            # Cap overly long events (likely idle/away)
            dur = max_dur
        d = ev.get("data", {})
        title = d.get("title", "")
        app = d.get("app", "")
        url = d.get("url", "")
        if not url:
            url = find_url_for_event(ev)
        cat = classify_event(title, app, url, cats)
        categories[cat] += dur
        app_times[app or title or "?"] += dur

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
        "raw_events": len(primary_events),
        "web_enriched": len([u for u in web_by_time if u["url"]])
    }


# ───────────────────────────────────────────────
# LLM — ANALISE INTELIGENTE DE PRODUTIVIDADE
# ───────────────────────────────────────────────

def get_recent_history(paths: dict, days: int = 3) -> str:
    """Le os ultimos N arquivos arquivados para dar contexto de tendencia."""
    archive = paths["archive"]
    if not archive.exists():
        return ""
    files = sorted([f for f in archive.iterdir() if f.suffix == ".md"], reverse=True)
    context = []
    for f in files[:days]:
        try:
            text = f.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(text)
            date = f.stem
            # Extrai apenas tasks feitas e LLM
            done = re.findall(r"^\s*- \[x\] (.*)$", body, re.MULTILINE)
            llm_match = re.search(r"## LLM\n\n(.*?)(?=\n## |\Z)", body, re.DOTALL)
            llm_bit = llm_match.group(1).strip()[:100] if llm_match else ""
            summary = f"- {date}: {len(done)} tasks feitas"
            if llm_bit:
                summary += f" | {llm_bit}"
            context.append(summary)
        except Exception:
            continue
    return "\n".join(context) if context else ""


def get_metrics_summary(paths: dict) -> str:
    """Le as ultimas 5 linhas do Metrics.md para contexto numerico."""
    metrics = paths["metrics"]
    if not metrics.exists():
        return ""
    lines = metrics.read_text(encoding="utf-8").strip().split("\n")
    # Pula header e separator
    data_lines = [l for l in lines if l.startswith("| 2")]
    return "\n".join(data_lines[-5:]) if data_lines else ""


def llm_analyze(day_content: str, aw_data: dict, cfg: dict, paths: dict = None) -> str:
    llm_cfg = cfg.get("llm", {})
    # Agora a LLM roda SEMPRE, a menos que explicitamente desativada
    if llm_cfg.get("enabled") is False:
        return "LLM desativada pelo usuario."

    api_key = llm_cfg.get("api_key", "")
    model = llm_cfg.get("model", "gpt-3.5-turbo")
    base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")
    is_ollama_local = "localhost:11434" in base_url
    is_ollama_cloud = "ollama.com" in base_url and not is_ollama_local
    is_ollama = is_ollama_local or is_ollama_cloud

    # Contexto do dia
    today = datetime.now().strftime("%A, %d/%m/%Y")
    dias_pt = {
        "Monday": "Segunda-feira", "Tuesday": "Terça-feira", "Wednesday": "Quarta-feira",
        "Thursday": "Quinta-feira", "Friday": "Sexta-feira", "Saturday": "Sábado", "Sunday": "Domingo"
    }
    dia_semana = dias_pt.get(datetime.now().strftime("%A"), datetime.now().strftime("%A"))

    pending, done = extract_tasks(day_content)
    tasks_summary = f"Feitas: {len(done)}. Pendentes: {len(pending)}."

    # O que estava planejado no PESO
    peso = extract_section(day_content, "PESO")
    peso_lines = [l.strip() for l in peso.split("\n") if l.strip().startswith("- [ ]") or l.strip().startswith("- [x]")]
    planned = "\n".join(peso_lines[:15]) if peso_lines else "Nenhum item estruturado no PESO."

    # Dados do ActivityWatch
    aw_summary = f"Total: {aw_data['total_hours']}h"
    for cat, meta in cfg.get("categories", {}).items():
        v = aw_data.get("metrics", {}).get(cat, 0)
        if v > 0:
            aw_summary += f"\n- {meta.get('emoji', '')} {meta.get('label', cat)}: {v}h"

    # Historico recente
    history = get_recent_history(paths, days=3) if paths else ""
    metrics = get_metrics_summary(paths) if paths else ""

    system_prompt = (
        "Voce e um coach de produtividade e crescimento pessoal. "
        "Analise o dia do usuario de forma estrategica, como um mentor que acompanha sua evolucao. "
        "Responda SEMPRE em portugues do Brasil. Seja direto, mas encorajador."
    )

    user_prompt = f"""HOJE: {dia_semana} ({today})

RESUMO DE TAREFAS:
{tasks_summary}

PLANEJADO NO PESO:
{planned}

DADOS DE TEMPO (ActivityWatch):
{aw_summary}

HISTORICO RECENTE:
{history if history else "Sem historico disponivel."}

METRICAS RECENTES:
{metrics if metrics else "Sem metricas disponiveis."}

CONTEUDO DO DIA (parcial):
{day_content[:1500]}

---
INSTRUCOES DE ANALISE:
1. DIA DA SEMANA: comente brevemente o dia (ex: "segunda produtiva", "sexta cansativa").
2. PROGRESSAO: O que avancou hoje? O que foi concluido do PESO?
3. REGRESSAO: O que ficou para tras? O que tomou tempo sem gerar valor?
4. FOCO: O tempo foi gasto no que importa? Compare planejado vs real.
5. HABITOS: Ha padroes repetidos (bom ou ruim)?
6. CRESCIMENTO: Qual aprendizado ou evolucao de hoje?
7. PROXIMA ACAO: Uma sugestao pratica e especifica para amanha.

Responda em 4-6 frases curtas e diretas. Seja realista, nao so elogioso."""

    try:
        headers = {"Content-Type": "application/json"}
        if api_key and not is_ollama_local:
            headers["Authorization"] = f"Bearer {api_key}"
        if "openrouter" in base_url:
            headers["HTTP-Referer"] = "https://github.com"
            headers["X-Title"] = "obsidian-activitywatch-sync"

        if is_ollama:
            r = requests.post(
                f"{base_url}/api/chat",
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "stream": False
                },
                timeout=120
            )
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
        else:
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
                    "max_tokens": 500
                },
                timeout=60
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"LLM error: {e}"


def llm_checkin(day_content: str, aw_data: dict, cfg: dict) -> str:
    """Analise leve para check-ins intermediarios ao longo do dia."""
    llm_cfg = cfg.get("llm", {})
    if llm_cfg.get("enabled") is False:
        return "LLM desativada."

    api_key = llm_cfg.get("api_key", "")
    model = llm_cfg.get("model", "gpt-3.5-turbo")
    base_url = llm_cfg.get("base_url", "https://api.openai.com/v1")
    is_ollama_local = "localhost:11434" in base_url
    is_ollama_cloud = "ollama.com" in base_url and not is_ollama_local
    is_ollama = is_ollama_local or is_ollama_cloud

    # Determina o momento do dia
    hour = datetime.now().hour
    if 5 <= hour < 12:
        momento = "manha"
        momento_label = "da manha"
    elif 12 <= hour < 14:
        momento = "meio-dia"
        momento_label = "do meio-dia"
    elif 14 <= hour < 18:
        momento = "tarde"
        momento_label = "da tarde"
    elif 18 <= hour < 22:
        momento = "noite"
        momento_label = "da noite"
    else:
        momento = "madrugada"
        momento_label = "da madrugada"

    pending, done = extract_tasks(day_content)
    tasks_summary = f"Feitas: {len(done)}. Pendentes: {len(pending)}."

    # PESO parcial
    peso = extract_section(day_content, "PESO")
    peso_lines = [l.strip() for l in peso.split("\n") if l.strip().startswith("- [ ]") or l.strip().startswith("- [x]")]
    planned = "\n".join(peso_lines[:10]) if peso_lines else "Nenhum item no PESO."

    # AW parcial
    aw_summary = f"Total hoje: {aw_data.get('total_hours', 0)}h"
    for cat, meta in cfg.get("categories", {}).items():
        v = aw_data.get("metrics", {}).get(cat, 0)
        if v > 0:
            aw_summary += f"\n- {meta.get('emoji', '')} {meta.get('label', cat)}: {v}h"

    system_prompt = (
        "Voce e um coach de produtividade. "
        "Faca um check-in rapido e direto. Responda SEMPRE em portugues do Brasil. "
        "Maximo 2-3 frases curtas."
    )

    user_prompt = f"""CHECK-IN {momento_label.upper()}

TAREFAS: {tasks_summary}

PESO:
{planned}

TEMPO HOJE:
{aw_summary}

INSTRUCOES:
1. Comente brevemente como esta indo o dia ate agora.
2. Destaque 1 coisa que esta funcionando e 1 que precisa de atencao.
3. De uma sugestao rapida e pratica para o proximo bloco da rotina.

Responda em 2-3 frases curtas. Seja realista."""

    try:
        headers = {"Content-Type": "application/json"}
        if api_key and not is_ollama_local:
            headers["Authorization"] = f"Bearer {api_key}"
        if "openrouter" in base_url:
            headers["HTTP-Referer"] = "https://github.com"
            headers["X-Title"] = "obsidian-activitywatch-sync"

        if is_ollama:
            r = requests.post(
                f"{base_url}/api/chat",
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "stream": False
                },
                timeout=120
            )
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
        else:
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
                    "max_tokens": 250
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

    # Monta seção AW
    aw_lines = ["## AW", ""]
    if aw_data:
        aw_lines.append(f"Total: {aw_data['total_hours']}h")
        aw_lines.append("")
        for cat in cats:
            v = aw_data["metrics"].get(cat, 0)
            if v > 0:
                label = cats.get(cat, {}).get("label", cat)
                emoji = cats.get(cat, {}).get("emoji", "")
                aw_lines.append(f"- {emoji} {label}: {v}h")
        top = aw_data['top_apps']
        if top:
            aw_lines.append("")
            aw_lines.append(f"Apps: {', '.join([f'{a} ({t})' for a, t in top])}")
    else:
        aw_lines.append("ActivityWatch offline.")
    aw_section = "\n".join(aw_lines)

    # Monta seção LLM
    llm_section = f"## LLM\n\n{llm_text}"

    # Se o arquivo já usa o template com <details>, insere dentro dos blocos
    if "<summary>📊 ActivityWatch</summary>" in content:
        archived = re.sub(
            r'(<details>\s*<summary>📊 ActivityWatch</summary>\s*).*?(\s*</details>)',
            r'\1\n' + aw_section + r'\n\2',
            content,
            flags=re.DOTALL
        )
    else:
        archived = content.rstrip() + "\n\n" + aw_section + "\n"

    if "<summary>🤖 Análise LLM</summary>" in archived:
        archived = re.sub(
            r'(<details>\s*<summary>🤖 Análise LLM</summary>\s*).*?(\s*</details>)',
            r'\1\n' + llm_section + r'\n\2',
            archived,
            flags=re.DOTALL
        )
    else:
        archived = archived.rstrip() + "\n\n" + llm_section + "\n"

    archived_path = paths["archive"] / f"{target_date.isoformat()}.md"
    archived_path.write_text(archived, encoding="utf-8")
    print(f"   📁 Archived: {archived_path.relative_to(paths['vault'])}")

    pending, _ = extract_tasks(content)
    next_date = target_date + timedelta(days=1)
    next_file = paths["all_day"] / f"{next_date.isoformat()}.md"

    # Extrai seções preservando subcategorias e estrutura
    peso_content = extract_section(body, "PESO")
    peso_lines = peso_content.split('\n')
    peso_lines = [l for l in peso_lines if not re.match(r'^[\s]*- \[x\]', l)]
    peso_content = '\n'.join(peso_lines).strip()

    backlog_content = extract_section(body, "Backlog pessoal")
    backlog_lines = backlog_content.split('\n')
    backlog_lines = [l for l in backlog_lines if not re.match(r'^[\s]*- \[x\]', l)]
    backlog_content = '\n'.join(backlog_lines).strip()

    next_body = f"""---
date: {next_date.isoformat()}
mood:
energy:
focus:
---

# {next_date.isoformat()}

## PESO
{peso_content}
"""
    if not peso_content:
        next_body += "- [ ] \n"

    next_body += f"\n## Backlog pessoal\n{backlog_content}\n"
    if not backlog_content:
        next_body += "- [ ] \n"

    next_body += """
## FOCO
- [ ] 

## Quick Stats

<details>
<summary>📝 Notas & Reflexões</summary>

## Aprendizados do dia

## Bloqueios e problemas

## Decisões importantes

## Vitória do dia

## Reflexão e gratidão

</details>

<details>
<summary>🎯 Objetivos & Hábitos</summary>

## Objetivos anuais

## Objetivos diários

## Hábitos

</details>

<details>
<summary>📊 ActivityWatch</summary>

## AW

</details>

<details>
<summary>🤖 Análise LLM</summary>

## LLM

</details>

---

> Cursos e videos importantes vao aqui no final do arquivo.
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

    # LLM: agora roda SEMPRE por padrao (a menos que enabled seja explicitamente false)
    llm_cfg = cfg.get("llm", {})
    llm_explicitly_disabled = llm_cfg.get("enabled") is False
    llm_text = ""
    if not llm_explicitly_disabled:
        print("   🧠 Generating LLM analysis...")
        llm_text = llm_analyze(
            content,
            aw_data or {"metrics": {}, "total_hours": 0},
            cfg,
            paths=paths
        )
        print(f"   ✅ {llm_text[:80]}...")
    else:
        llm_text = "LLM desativada pelo usuario."

    if aw_data:
        content = update_frontmatter_field(content, "aw_total", aw_data["total_hours"])
        for cat, val in aw_data["metrics"].items():
            content = update_frontmatter_field(content, f"aw_{cat}", val)
    current_file.write_text(content, encoding="utf-8")

    pending = archive_and_create_next(current_file, target_date, aw_data, llm_text, paths, cfg.get("categories", {}))
    update_metrics_file(target_date, fm, aw_data, paths, cfg.get("categories", {}), not llm_explicitly_disabled)
    update_backlog_file(pending, paths)

    print("\n✨ Done!")


def cmd_checkin(args, cfg, paths):
    """Check-in leve ao longo do dia (sync + analise rapida)."""
    current_file = find_current_day_file(paths["all_day"])
    print(f"\n📍 Check-in: {current_file.name}")

    try:
        target_date = date.fromisoformat(current_file.stem)
    except ValueError:
        print("❌ Invalid filename")
        sys.exit(1)

    content = current_file.read_text(encoding="utf-8")
    aw_data = sync_activitywatch(target_date, cfg)

    if not aw_data:
        print("   ⚠️ No ActivityWatch data")
        return

    content = update_frontmatter_field(content, "aw_total", aw_data["total_hours"])
    for cat, val in aw_data["metrics"].items():
        content = update_frontmatter_field(content, f"aw_{cat}", val)

    cats = cfg.get("categories", {})
    aw_lines = ["## AW", f"", f"Total: {aw_data['total_hours']}h", f""]
    for cat in cats:
        v = aw_data["metrics"].get(cat, 0)
        if v > 0:
            label = cats[cat].get("label", cat)
            emoji = cats[cat].get("emoji", "")
            aw_lines.append(f"- {emoji} {label}: {v}h")
    top = aw_data['top_apps']
    if top:
        aw_lines.append(f"")
        aw_lines.append(f"Apps: {', '.join([f'{a} ({t})' for a, t in top])}")
    aw_block = "\n".join(aw_lines)

    if "<summary>📊 ActivityWatch</summary>" in content:
        content = re.sub(
            r'(<details>\s*<summary>📊 ActivityWatch</summary>\s*).*?(\s*</details>)',
            r'\1\n' + aw_block + r'\n\2',
            content,
            flags=re.DOTALL
        )
    elif "## AW" in content:
        content = re.sub(r"## AW\n.*?(?=\n## |\Z)", aw_block + "\n", content, flags=re.DOTALL)
    else:
        content = content.rstrip() + "\n\n" + aw_block + "\n"

    current_file.write_text(content, encoding="utf-8")
    print(f"   ✅ AW: {aw_data['total_hours']}h")

    # Check-in LLM (prompt leve)
    llm_cfg = cfg.get("llm", {})
    llm_explicitly_disabled = llm_cfg.get("enabled") is False
    if not llm_explicitly_disabled:
        print("   🧠 Generating check-in...")
        text = llm_checkin(content, aw_data, cfg)
        print(f"   📝 {text[:200]}...")
        llm_block = f"## LLM\n\n{text}"
        if "<summary>🤖 Análise LLM</summary>" in content:
            content = re.sub(
                r'(<details>\s*<summary>🤖 Análise LLM</summary>\s*).*?(\s*</details>)',
                r'\1\n' + llm_block + r'\n\2',
                content,
                flags=re.DOTALL
            )
        elif "## LLM" in content:
            content = re.sub(r"## LLM\n.*?(?=\n## |\Z)", llm_block + "\n", content, flags=re.DOTALL)
        else:
            content = content.rstrip() + "\n\n" + llm_block + "\n"
        current_file.write_text(content, encoding="utf-8")


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
    aw_lines = ["## AW", f"", f"Total: {aw_data['total_hours']}h", f""]
    for cat in cats:
        v = aw_data["metrics"].get(cat, 0)
        if v > 0:
            label = cats[cat].get("label", cat)
            emoji = cats[cat].get("emoji", "")
            aw_lines.append(f"- {emoji} {label}: {v}h")
    top = aw_data['top_apps']
    if top:
        aw_lines.append(f"")
        aw_lines.append(f"Apps: {', '.join([f'{a} ({t})' for a, t in top])}")
    aw_block = "\n".join(aw_lines)

    # Insere AW dentro do <details> se existir, senão substitui seção normal
    if "<summary>📊 ActivityWatch</summary>" in content:
        content = re.sub(
            r'(<details>\s*<summary>📊 ActivityWatch</summary>\s*).*?(\s*</details>)',
            r'\1\n' + aw_block + r'\n\2',
            content,
            flags=re.DOTALL
        )
    elif "## AW" in content:
        content = re.sub(r"## AW\n.*?(?=\n## |\Z)", aw_block + "\n", content, flags=re.DOTALL)
    else:
        content = content.rstrip() + "\n\n" + aw_block + "\n"

    current_file.write_text(content, encoding="utf-8")
    print(f"   ✅ AW: {aw_data['total_hours']}h")

    # LLM: roda SEMPRE no sync tambem (a menos que explicitamente desativada)
    llm_cfg = cfg.get("llm", {})
    llm_explicitly_disabled = llm_cfg.get("enabled") is False
    if not llm_explicitly_disabled:
        print("   🧠 Generating LLM...")
        text = llm_analyze(content, aw_data, cfg, paths=paths)
        print(f"   📝 {text[:200]}...")
        llm_block = f"## LLM\n\n{text}"
        if "<summary>🤖 Análise LLM</summary>" in content:
            content = re.sub(
                r'(<details>\s*<summary>🤖 Análise LLM</summary>\s*).*?(\s*</details>)',
                r'\1\n' + llm_block + r'\n\2',
                content,
                flags=re.DOTALL
            )
        elif "## LLM" in content:
            content = re.sub(r"## LLM\n.*?(?=\n## |\Z)", llm_block + "\n", content, flags=re.DOTALL)
        else:
            content = content.rstrip() + "\n\n" + llm_block + "\n"
        current_file.write_text(content, encoding="utf-8")


# ───────────────────────────────────────────────
# MAIN
# ───────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Obsidian + ActivityWatch Daily Sync")
    parser.add_argument("--roll", action="store_true", help="End-of-day rollover")
    parser.add_argument("--sync", action="store_true", help="Sync AW + full LLM analysis")
    parser.add_argument("--checkin", action="store_true", help="Light check-in (sync + quick LLM)")
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
    elif args.checkin:
        cmd_checkin(args, cfg, paths)
    elif args.sync:
        cmd_sync(args, cfg, paths)
    else:
        now = datetime.now().hour
        if now >= 22 or now <= 5:
            print("🌙 Night time detected. Running rollover...")
            cmd_roll(args, cfg, paths)
        else:
            print("🔄 Use --roll, --checkin or --sync.\n")
            parser.print_help()
