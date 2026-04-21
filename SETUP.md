# AI Stack — Setup & Usage

## What's Here

Two-tier local LLM router with Streamlit dashboard, all self-contained.

- **Ollama** — 4b sorter + 35b thinker (RTX 4070 Super, max GPU offload)
- **AnythingLLM** — 10 pre-seeded RAG workspaces
- **Router API** — OpenAI-compatible two-tier routing on :3839
- **Streamlit dashboard** — chat UI on :8501
- **OpenClaw gateway** — Discord / tool-calling on :18789

---

## Quick Start

| Goal | Action |
|------|--------|
| Start the router (as a service) | Double-click `launch_router.bat` or desktop `AI Router` shortcut |
| Start the dashboard | Double-click `launch_dashboard.bat` |
| Interactive CLI | PowerShell: `.\invoke.ps1` |
| Check all services | PowerShell: `.\invoke.ps1 -Check` |

The router-exe auto-clears port 3839 if a previous instance is still bound, runs a boot checklist (Ollama, AnythingLLM, OpenClaw gateway, Clarity Engine, models, RAG workspace counts, OpenClaw routing config), prints a "How to use" panel with all service URLs + model aliases, then serves the API.

### Persistent memory

Every turn is summarized by the 4b and saved to the `memory` AnythingLLM workspace. The last 5 summaries are semantically recalled and injected as a system block on every request, so Discord threads and dashboard chats retain context across restarts.

### router-assistant mode

Call with `model: "router-assistant"` to get the cross-workspace assistant. It forces the 35b, pulls the top chunks from every non-private workspace, snapshots the Clarity Engine tree, and ingests the round's text into the tree asynchronously. `private` is never read.

---

## Services

| Service | URL | What it's for |
|---------|-----|---------------|
| Router API | http://localhost:3839 | OpenAI-compatible `/v1/chat/completions` endpoint |
| Router health | http://localhost:3839/health | Liveness + readiness JSON (Docker/monitor-friendly) |
| Router metrics | http://localhost:3839/metrics | Prometheus text (`/metrics.json` for a JSON mirror) |
| API docs | http://localhost:3839/docs | Interactive Swagger UI |
| Dashboard | http://localhost:8501 | Streamlit chat UI + workspace browser |
| Ollama | http://localhost:11434 | Local LLM runtime (4b sorter + 35b thinker) |
| AnythingLLM | http://localhost:3001 | RAG workspaces + embeddings (desktop app) |
| OpenClaw gateway | http://localhost:18789 | Discord + tool-calling bridge |
| Clarity Engine | http://localhost:3747 | Typed priority-tree SDK (powers `router-assistant`) |

---

## Model Aliases

Pick which one to set as `model` when calling `/v1/chat/completions`:

| Alias | Behavior |
|-------|----------|
| `router` | Auto-route: the 4b sorter picks 4b or 35b based on complexity. Auto-refine kicks in when the sorter flags `needs_refinement=true`. |
| `router-assistant` | Forces 35b + injects cross-workspace RAG + live Clarity Engine state (private workspace excluded) |
| `router-fast` | Speed-first: sorter may dispatch safe tasks to free models (llama3.1:8b, qwen2.5-coder:14b, mistral:latest). Sensitive workspaces still stay on abliterated. |
| `router-sub-coder` | Direct dispatch to qwen2.5-coder:14b |
| `router-sub-draft` | Direct dispatch to llama3.1:8b |
| `router-sub-critic` | Direct dispatch to mistral:latest (also used internally by the auto-refine critic pass) |
| `router-sub-research` | 35b + cross-workspace RAG, tuned for citing sources across workspaces |
| `router-two-pass` | Two-pass: 35b draft + 35b critic-refine. Trades ~2× latency for higher quality. |
| `router-two-pass-uncensored` | Same, plus red-team prepend. Use only for authorized pentest / harm-reduction work. |
| `huihui_ai/qwen3.5-abliterated:4b` | Direct call to the 4b sorter/drafter (no subagent plumbing) |
| `huihui_ai/qwen3.5-abliterated:35b` | Direct call to the 35b thinker (no subagent plumbing) |

---

## Workspaces

All 10 live in AnythingLLM with seeded context:

| Workspace | Slug | Password |
|-----------|------|----------|
| Tim | `tim` | — |
| TCG.bot | `tcg-dot-bot` | — |
| Movie_Poster | `movie_poster` | — |
| Projects | `projects` | — |
| Assistant | `assistant` | — |
| Pen Test | `pen-test` | — |
| Substances | `substances` | — |
| Journal | `journal` | — |
| Ideas | `ideas` | — |
| Private | `private` | env `PRIVATE_PASSWORD` or keyring `private-password` (fail-closed if unset) |

---

## CLI Router Commands

```
/workspace-list         All workspaces + URL patterns
/workspace <slug>       Switch manually
/project <name>         Switch + auto-map workspace
/projects               List known projects

/rag [on|off]           Toggle RAG
/rag-add <title>|<text> Add text to workspace
/rag-file <path>        Upload + embed file
/workspace-docs         List embedded docs

/capture                Embed current browser page
/export                 Save conversation as markdown

/history                Last 10 turns
/clear                  Clear history
/models                 Model + GPU info
/status                 Full config snapshot
quit                    Save + exit
```

---

## File Layout

```
C:\Users\timan\AI STACK\
├── bin\
│   ├── AI Router.exe            compiled router_server.py
│   └── AI Router CLI.exe        compiled ai_router_v2.py
├── src\                         (out of sight — won't open in VS Code on click)
│   ├── router_server.py         two-tier API server
│   ├── ai_router_v2.py          interactive CLI router
│   ├── dashboard.py             Streamlit web UI
│   └── dashboard_launcher.py    (optional exe wrapper for dashboard)
├── launch_router.bat            runs AI Router.exe
├── launch_dashboard.bat         runs streamlit on src\dashboard.py
├── invoke.ps1                   PowerShell entry point (-Check, -Dashboard, -Serve)
├── desktop.ps1                  desktop-ops skill script
├── router.ico                   shortcut icon
└── SETUP.md                     this file
```

---

## Rebuilding the Router .exe

```powershell
cd "C:\Users\timan\AI STACK"
python -m PyInstaller --onefile --name "AI Router" --icon router.ico --distpath bin src\router_server.py
```

After rebuilding, clean the transient PyInstaller artifacts:
```powershell
Remove-Item -Recurse -Force build, "AI Router.spec"
```

---

## Troubleshooting

- **Shortcut window flashes and closes** — port 3839 is held by an older instance. The exe now kills the holder on startup; if it still fails, run `launch_router.bat` (same binary) and read the boot output.
- **Dashboard won't start** — make sure Python + Streamlit are on PATH. Run from PowerShell: `python -m streamlit run src\dashboard.py` to see the actual error.
- **Router says "X failed to start"** — Ollama / AnythingLLM must be installed. The OpenClaw gateway auto-starts from `ensure_services()` if the CLI is on PATH.
- **Private workspace rejects every password** — nothing is configured. Either set `PRIVATE_PASSWORD` in `.env` **or** run `python -m core.keyring_helper set private-password`. The gate is fail-closed when empty (see Security & observability below).
- **`/health` shows `ollama_healthy: false`** — Ollama isn't listening on `$OLLAMA_BASE_URL`. Start it (`ollama serve`) or check the URL. The probe is cached for 5s so transient blips won't flap.
- **First chat takes 20+ s** — Ollama is loading the model. `/health` stays OK; subsequent calls hit the warm model.

---

## Security & observability

Everything in this section is opt-in or env-driven. Defaults aim for a
localhost-only dev box; tighten before exposing the router to a network.

### Private workspace password (fail-closed)

The Streamlit "Private" workspace gate compares against a SHA-256 hash of
`PRIVATE_PASSWORD` (env) or keyring account `private-password`. **Missing
secret ⇒ every unlock attempt rejected, input disabled.** Set it with:

```powershell
python -m core.keyring_helper set private-password     # prompts, hidden input
```

### Auto-refinement

When the 4b sorter marks a task `needs_refinement=true` (hard technical
work, exploit chains, multi-step code), the router automatically runs
`router-sub-critic` (mistral) over the 35b draft and re-runs the 35b with
the critique folded in. Trades roughly 2× latency for a tighter answer.

Toggle: `ROUTER_AUTO_REFINE=1` (default) / `0` to disable. Explicit subagent
aliases bypass this gate, and a `router-two-pass` call is a separate flow.

### Rate limiter

Per-IP sliding window, 60-second bucket. Defaults to **120 req/min**.
Localhost (`127.0.0.1`, `::1`, `localhost`, `host.docker.internal`) is
exempt so the dashboard, OpenClaw, and Docker networking don't trip it.
Tune with `ROUTER_RATE_LIMIT_PER_MIN`; set `0` to disable entirely.
Returns HTTP 429 with a `Retry-After` header.

### CORS

Configured via `ROUTER_CORS_ORIGINS` (comma-separated). Default allows
Streamlit (8501) and Vite (3000) on localhost. `*` allows any origin but
drops credentials per spec — only safe on a strictly localhost-bound
deployment.

### Desktop control opt-in

`desktop.ps1` (mouse / keyboard / clipboard / windows primitives) refuses
every subcommand — including read-only screenshots — unless
`AI_ROUTER_DESKTOP_ENABLED=1` is set or `--enable` is passed. Rationale:
treat every action as surveillance; prevent a compromised router tool
from driving the host silently.

### Telemetry

- `GET /health` — liveness + readiness JSON (includes `auto_refine`,
  `rate_limiter`, `keyring_enabled`, `ollama_healthy`).
- `GET /metrics` — Prometheus text (counters, histograms, gauges for
  request latency, output chars/sec, auto-refine / two-pass / rate-limit
  events).
- `GET /metrics.json` — same data, JSON-shaped, for dashboards that
  don't want to parse the text format.
- Per-request structured log on the `ai_stack.telemetry` logger (one
  JSON line per completed request). Route it to a file or aggregator
  with standard Python logging config.

### Boot check

The console panel printed at startup surfaces the active state of every
knob above, so it's obvious at a glance what's on and what isn't.

---

## Migration Guide — plaintext `.env` → OS keyring

**Why bother?** `.env` files sit unencrypted on disk and are trivial to
accidentally commit. The OS keyring (Windows Credential Manager / macOS
Keychain / Linux Secret Service) encrypts at rest and scopes access to
the logged-in user. The router resolves secrets as **keyring first, env
var second** — no flag can invert that order (so a compromised env var
can't silently overtake a keyring-stored secret).

### One-time migration

```powershell
# 1. Install the keyring backend (once per Python env)
pip install -r requirements.txt   # includes keyring>=25.0

# 2. Copy each .env secret into the keyring (interactive; hidden input)
python -m core.keyring_helper set anythingllm
python -m core.keyring_helper set browser-ext
python -m core.keyring_helper set ollama
python -m core.keyring_helper set private-password     # optional

# 3. Confirm
python -m core.keyring_helper list

# 4. Blank the corresponding lines in .env (do not delete .env — Docker/CI
#    still read from it when the keyring isn't available)
#    Edit .env and leave:
#        ANYTHINGLLM_API_KEY=
#        BROWSER_EXT_KEY=
#        OLLAMA_API_KEY=
#        PRIVATE_PASSWORD=
```

Next time the router boots, the Security & refinement panel will show
`keyring ✔ N/4 stored` and the env vars will no longer be touched.

### What if keyring isn't available?

The keyring package can fail on a headless Linux box or a locked-down
CI runner. In that case, `python -m core.keyring_helper list` prints an
install hint, and the router silently falls back to env vars — the
`/health` endpoint reports `keyring_enabled: false`. **That's fine for
Docker / CI**; the env-var path is the documented fallback. Keyring is
the recommended path for workstation use.

### Rotating a secret

```powershell
python -m core.keyring_helper set anythingllm     # overwrites the existing entry
```

The router does not cache resolved secrets — a router restart picks up
the new value on its next boot (`/health` shows `keyring_entries` so you
can verify before restarting).
