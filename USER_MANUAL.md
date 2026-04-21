# AI STACK — User Manual

Personal local AI routing stack. Two-tier Ollama (4b sorter → 35b thinker),
AnythingLLM RAG, Streamlit dashboard, OpenClaw/Discord integration, Docker
optional. Built to stay **uncensored by default** with free-model speed
boosters for safe tasks.

- **Core package:** `v0.2.0`
- **Default models:** `huihui_ai/qwen3.5-abliterated:4b` (sorter) →
  `huihui_ai/qwen3.5-abliterated:35b` (thinker)
- **Speed boosters (opt-in):** `llama3.1:8b`, `mistral:latest`,
  `qwen2.5-coder:14b`

---

## 1. First-time setup

```powershell
# 1) Pull the models
ollama pull huihui_ai/qwen3.5-abliterated:4b
ollama pull huihui_ai/qwen3.5-abliterated:35b
ollama pull llama3.1:8b
ollama pull mistral
ollama pull qwen2.5-coder:14b

# 2) Copy the example env and edit keys
cp .env.example .env        # then open .env and fill in keys

# 3) Install deps (if not using Docker)
pip install -r requirements.txt

# 4) Register subagents with OpenClaw (one-shot; idempotent)
python .changes/register_openclaw_subagents.py
```

### .env — what goes in it

```
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_API_KEY=                        # optional; set only if your Ollama is auth-gated
ANYTHINGLLM_BASE_URL=http://localhost:3001   # BARE host — no /api suffix
ANYTHINGLLM_API_KEY=<your key>
BROWSER_EXT_KEY=<your browser-ext key>
ROUTER_SMALL_MODEL=huihui_ai/qwen3.5-abliterated:4b
ROUTER_BIG_MODEL=huihui_ai/qwen3.5-abliterated:35b
ROUTER_FAST_DRAFT_MODEL=llama3.1:8b
ROUTER_FAST_CRITIC_MODEL=mistral:latest
ROUTER_FAST_CODER_MODEL=qwen2.5-coder:14b
ROUTER_SERVER_URL=http://localhost:3839
```

---

## 2. Starting the stack

### Local (Windows)

```powershell
python src\router_server.py
```

The router prints a boot-check panel: services, models, RAG workspaces,
OpenClaw config alignment, the full alias list, and a quick-manual cheat
sheet. It auto-spawns the Streamlit dashboard on port 8501 and tries to
start Clarity Engine + OpenClaw Gateway if they're installed.

### Docker

```bash
docker compose up -d
# first run only — pull the models inside the ollama container
docker compose exec ollama ollama pull huihui_ai/qwen3.5-abliterated:4b
docker compose exec ollama ollama pull huihui_ai/qwen3.5-abliterated:35b
docker compose exec ollama ollama pull llama3.1:8b
docker compose exec ollama ollama pull mistral
docker compose exec ollama ollama pull qwen2.5-coder:14b
```

Compose runs three services: `ollama` (GPU passthrough on Linux or
Docker Desktop WSL2), `router` on 3839, `streamlit` on 8501.
AnythingLLM stays on the host via `host.docker.internal:3001`.

**Docker Desktop must be running** before `docker compose up`.

### CLI (no server)

```powershell
python src\ai_router_v2.py
```

Stand-alone interactive CLI router. Uses the same core/ prompts, memory,
RAG, and session helpers — no FastAPI needed.

---

## 3. Model aliases

Everything below is an OpenAI-compatible `model` field. Hit the router at
`http://localhost:3839/v1/chat/completions`.

| Alias                                 | Routes to                              | When to use                         |
|---------------------------------------|----------------------------------------|-------------------------------------|
| `router`                              | 4b sorter → 4b or 35b                  | Default auto-route                  |
| `router-assistant`                    | 35b + cross-workspace RAG + Clarity    | Cross-project assistant queries     |
| `router-fast`                         | Free models (safe tasks) or 35b        | Speed-first for low-risk chat       |
| `router-sub-coder`                    | `qwen2.5-coder:14b`                    | Code tasks                          |
| `router-sub-draft`                    | `llama3.1:8b`                          | Fast first drafts                   |
| `router-sub-critic`                   | `mistral:latest`                       | Reviewer                            |
| `router-sub-research`                 | 35b + cross-workspace RAG              | Research synthesis                  |
| `router-two-pass`                     | 35b draft + 35b critic refine          | **High-quality answers**            |
| `router-two-pass-uncensored`          | Same + red-team prepend + temp=0 pass 2| **Pentest / red-team**              |
| `claude-opus-4-7-two-pass`            | Cloud (Opus 4.7) — falls back to local | Max intelligence (needs cloud key)  |
| `claude-opus-4-7-two-pass-uncensored` | Cloud + red-team prepend — falls back  | Hardest pentest tasks               |
| `gpt-5-mini-two-pass`                 | Cloud (GPT-5 Mini) — falls back        | Fast refined responses              |

Sensitive topics (`pentest`, `substance`, `private`, etc.) **always stay
on abliterated** regardless of the alias chosen — even `router-fast`
won't dispatch them to a free model.

---

## 4. Two-Pass mode — when to use it

Two-pass runs **draft → critic refine** internally and returns only the
refined answer. Cost: ~1.8–2.2x a normal call. Quality: noticeably higher,
especially for structured output (exploit chains, step-by-step plans,
technical writeups).

```bash
curl -s http://localhost:3839/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "router-two-pass-uncensored",
    "messages": [
      {"role": "user", "content": "Walk through enumerating an internal AD domain from an unauth vantage point."}
    ]
  }'
```

What happens under the hood:

1. **Pass 1** — the 35b abliterated thinker produces a full draft using
   your RAG context, memory recall, and (for `-uncensored`) the red-team
   system prepend.
2. **Pass 2** — a critic pass sees the original query + the draft, is
   forced to `temperature=0`, and rewrites it into a single stronger
   answer. The draft is never shown.
3. Only pass-2 output is returned.

**Use two-pass for:** pentest walkthroughs, exploit chains, threat models,
hard technical problems, anything where hallucination matters.
**Don't bother for:** chit-chat, simple factual lookups — the extra
latency isn't worth it.

---

## 5. Dashboard & Discord

**Streamlit dashboard** on http://localhost:8501. Chat routes through the
router_server (same behavior as OpenClaw / curl). Sidebar switches
workspace; `Assistant` workspace forces `router-assistant`. Falls back to
direct Ollama if the router is down.

**OpenClaw + Discord.** OpenClaw's config at `~/.openclaw/openclaw.json`
is already wired — the vllm provider points at `http://localhost:3839`
and all 7 router aliases (plus the 5 two-pass ones) are registered.
In Discord, `@bot` + message uses the default alias; use `model:` tags or
OpenClaw's model-picker to pick a subagent.

---

## 6. Troubleshooting

| Symptom                                            | Fix                                                      |
|----------------------------------------------------|----------------------------------------------------------|
| `agents.defaults.model: Invalid input` from doctor | Run `python .changes/register_openclaw_subagents.py` — it migrates legacy nested keys out |
| `Failed to discover vLLM models: fetch failed`     | Router isn't running. Start it (`python src\router_server.py`) |
| `✘ unreachable` on every RAG workspace             | AnythingLLM desktop app isn't running, OR `.env` still has the legacy `/api` suffix — make it the bare host |
| Dashboard chat works but OpenClaw doesn't          | OpenClaw cached the old model list — `/doctor` then restart it |
| Two-pass returns empty                             | Pass 1 timed out or crashed — check Ollama is up and the 35b model is pulled |
| Free model never picked on `router-fast`           | Sorter flagged the task as sensitive OR the query contains a sensitive keyword — expected behavior |
| Boot-check shows `✘ primary model`                 | `openclaw.json`'s `agents.defaults.model.primary` should be `ollama/huihui_ai/qwen3.5-abliterated:4b` or `vllm/router` |

---

## 7. One-liners reference

```bash
# Chat with auto-route
curl -s http://localhost:3839/v1/chat/completions -d '{"model":"router","messages":[{"role":"user","content":"hi"}]}'

# Spawn a subagent explicitly (sync)
curl -s http://localhost:3839/v1/agents/spawn -d '{"alias":"router-sub-coder","prompt":"refactor this"}'

# Spawn async + poll
curl -s http://localhost:3839/v1/agents/spawn -d '{"alias":"router-two-pass","prompt":"long task","async":true}'
curl -s http://localhost:3839/v1/agents/<job_id>

# List available aliases
curl -s http://localhost:3839/v1/models | jq .

# Boot check only
python src\router_server.py --check      # if flag exists; otherwise just start the server

# Re-register OpenClaw aliases
python .changes/register_openclaw_subagents.py
```

---

## 8. File map

```
AI STACK/
├── core/                    # shared library (one source of truth)
│   ├── __init__.py          # __version__ = "0.2.0"
│   ├── auth.py              # _auth_ollama, _auth_anything, _auth_browser
│   ├── config.py            # all env-driven config
│   ├── http.py              # _ollama_post, _ollama_stream, _alm_request
│   ├── memory.py            # AnythingLLM-backed conversation memory
│   ├── prompts.py           # SORTER / THINKER / CLASSIFIER + SUBAGENTS registry
│   ├── rag.py               # workspace retrieve + ingest
│   ├── session.py           # atomic session persistence
│   ├── two_pass.py          # draft + critic-refine flow
│   └── utils.py             # _extract_json + misc
├── src/
│   ├── ai_router_v2.py      # CLI router
│   ├── dashboard.py         # Streamlit UI
│   └── router_server.py     # FastAPI /v1/chat/completions + /v1/agents/*
├── .changes/
│   ├── register_openclaw_subagents.py
│   └── snapshots/           # file backups
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
├── CHANGES.md
└── USER_MANUAL.md           # this file
```
