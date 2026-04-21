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
| `router` | Auto-route: the 4b sorter picks 4b or 35b based on complexity |
| `router-assistant` | Forces 35b + injects cross-workspace RAG + live Clarity Engine state (private workspace excluded) |
| `huihui_ai/qwen3.5-abliterated:4b` | Direct call to the 4b sorter/drafter |
| `huihui_ai/qwen3.5-abliterated:35b` | Direct call to the 35b thinker |

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
