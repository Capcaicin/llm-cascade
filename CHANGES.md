Change log — AI STACK

2026-04-20  — Initial edits planned and snapshots folder created.

Steps will be logged here; snapshots of original files saved under `.changes/snapshots/` before each change.

---

## 2026-04-20 — core v0.2.0, subagent architecture

### Hygiene (5 bugs)
1. `core/memory.py`: added missing `import json` (crashed on string-shaped metadata).
2. `core/config.py` + `.env.example`: aligned `ANYTHINGLLM_BASE_URL` to be the
   bare host; `http.py` is the single place that appends `/api/v1/…`. Added a
   `_strip_api_suffix` to absorb legacy `.env` files still carrying `/api`.
3. `src/ai_router_v2.py`: removed duplicate `load_session` / `save_session`
   defs that shadowed the `core.session` imports (silently bypassed atomic
   write, lockfile, and one-time migration).
4. `src/ai_router_v2.py`: fixed `_stream_chat(model, messages, options)`
   signature mismatch — now calls `_ollama_stream({...payload})` correctly.
5. `src/router_server.py`: removed hardcoded config; imports from `core.config`.

### Architecture
- `core/prompts.py` — canonical prompts (SORTER, THINKER, CLASSIFIER) + the
  `SUBAGENTS` registry. One source of truth for CLI, dashboard, router.
- `core/__init__.py` — re-exports + `__version__ = "0.2.0"` for boot logging.
- `core/auth.py` — added `_auth_ollama()` for hosted or secured Ollama. Key
  picked up from `OLLAMA_API_KEY`.
- `core/http.py` — `_ollama_post` / `_ollama_stream` now include Ollama auth
  header when set. Empty header for local un-secured Ollama.

### Subagents (router_server v1.1)
- New alias: `router-fast` — sorter may dispatch safe tasks to
  `llama3.1:8b` / `mistral:latest` / `qwen2.5-coder:14b` for speed.
- New alias: `router-sub-coder` (qwen2.5-coder:14b), `router-sub-draft`
  (llama3.1:8b), `router-sub-critic` (mistral:latest), `router-sub-research`
  (35b + cross-workspace RAG).
- Existing `router` and `router-assistant` unchanged — abliterated remains
  the default for anything user-facing. Sensitive topics (pentest, harm
  reduction, private) always stay on abliterated.
- New endpoint `POST /v1/agents/spawn` for explicit subagent invocation
  (sync or async with job polling via `GET /v1/agents/{job_id}`).
- OpenClaw config (`~/.openclaw/openclaw.json`) updated — all six aliases
  registered under `models.providers.vllm.models` and `agents.defaults`.
  Backup: `openclaw.json.pre-subagent-<timestamp>.bak`.

### Streamlit pivot
- `src/dashboard.py` now routes chat through `router_server` at
  `/v1/chat/completions` (SSE). Memory, Clarity, and subagent behavior stay
  consistent across the dashboard and OpenClaw.
- Graceful fallback: if `router_server` is down, the dashboard talks directly
  to Ollama using the same `core.prompts`, so it stays usable.
- Removes the silent behavioral divergence between Streamlit and AnythingLLM
  workspace settings — the dashboard no longer applies its own
  SORTER/THINKER copy.

### Docker
- `docker-compose.yml` + `Dockerfile` + `requirements.txt` — single image,
  two entrypoints (router + streamlit), ollama as its own container with
  GPU passthrough. AnythingLLM stays on the host for now via
  `host.docker.internal`.

### Prompt unification — final drift killed
- `src/ai_router_v2.py`: deleted the local `_SORTER_SYSTEM`, `_SORTER_PROMPT`,
  `_THINKER_SYSTEM`, `_THINKER_PROMPT` definitions that were shadowing the
  `core.prompts` import. The import block (aliased to the same underscore
  names) now supplies those constants. CLI, dashboard, and router_server all
  read from `core/prompts.py` — zero prompt copies left in `src/`.
- Verified: `python -c "import ast; ast.parse(open('src/ai_router_v2.py').read())"`
  passes; `from core.prompts import SUBAGENTS, resolve_subagent` resolves all
  7 aliases (`router`, `router-assistant`, `router-fast`, `router-sub-coder`,
  `router-sub-draft`, `router-sub-critic`, `router-sub-research`).

### OpenClaw schema fix + boot checker upgrade

Root cause of the `doctor` errors (`agents.defaults.model: Invalid input`,
vLLM discovery `TypeError: fetch failed`):

- Schema misuse: the previous `register_openclaw_subagents.py` wrote the
  alias map to `agents.defaults.model.models`. OpenClaw's schema accepts
  only `{primary, fallbacks}` on `agents.defaults.model`; the alias map
  belongs at `agents.defaults.models` (plural sibling). Anything else
  trips the validator.
- Legacy mojibake: `agents.defaults.models` carried an old garbled key
  `vllm/sort … qwen3.5-abliterated:4b → 35b` from an earlier cp1252-bitten
  run. Removed.

Fixes:

- `~/.openclaw/openclaw.json`: one-shot repair moved the 6 nested alias
  entries into `agents.defaults.models`, deleted the garbled key, and
  registered `vllm/router` in the alias map. Backup:
  `openclaw.json.pre-repair-20260420-201800.bak`.
- `.changes/register_openclaw_subagents.py`: rewritten to (1) migrate any
  illegal nested keys out of `agents.defaults.model` on every run,
  (2) scrub mojibake keys, (3) write aliases to the correct plural path.
  Idempotent — second run prints `[=] OpenClaw config already aligned`.

Boot checker (`src/router_server.py:ensure_services`) improvements —
keeps the whole pass well under 2s on a warm machine:

- `_port_open` default timeout 1.0s → 0.4s.
- RAG workspace probes (11 slugs) moved from serial loop to
  `ThreadPoolExecutor` parallel fan-out; per-call timeout 4s → 2s. Worst
  case is now the slowest single workspace, not the sum.
- New OpenClaw schema guard: prints `✘ extra keys: …` if
  `agents.defaults.model` contains anything beyond `{primary, fallbacks}`,
  so a future drift caught here instead of by OpenClaw's doctor.
- New subagent-alias count row: `✔ N/7 registered`, with the exact fix
  command inline (`python .changes/register_openclaw_subagents.py`).
- Footer now lists all 7 router aliases (router, router-assistant,
  router-fast, router-sub-coder/draft/critic/research) plus the raw
  4b/35b models.
- New **Quick manual** section: one-liners for chat curl, subagent spawn,
  OpenClaw registration, pulling the free models, `docker compose up`,
  and stop behavior.

### Follow-up audit (same-day code review)

Bugs the review caught that weren't covered above:

- `src/ai_router_v2.py`: the file kept its OWN local config block (legacy
  `ANYTHINGLLM_BASE_URL=http://localhost:3001/api` default, URLs built as
  `/v1/...`). That silently 404'd for any user whose `.env` followed the
  updated `.env.example` (bare host). Replaced the local block with
  `from core.config import (...)`, and rewrote the four URL sites
  (`/v1/auth`, `/v1/workspaces`, `/v1/extension/context`,
  `/v1/workspace/{slug}`) to build `/api/v1/...` on the bare host —
  consistent with every other caller in the stack.
- `src/router_server.py`: boot-check reachability was hardcoded to
  `127.0.0.1`, so inside the docker router container Ollama (at internal
  DNS `ollama:11434`) was never seen. Added `_host_port_from_url()`,
  taught `_port_open()` to take a host arg, and switched the Ollama +
  AnythingLLM service rows to derive host/port from `core.config` URLs.
  Now works for local Windows runs AND `docker compose up` without config
  drift. Tested against `localhost`, `ollama`, `host.docker.internal`,
  and port-less URL forms.
- `src/dashboard.py`: `CORE_VERSION` was imported but unused. Surfaced it
  as a sidebar caption (`core vX.Y.Z`) so the dashboard shows the same
  version the router logs on boot.

### Two-Pass mode + user manual

New generation flow: draft with the strongest model, then run a critic
pass that rewrites the draft into a single stronger final answer. Only
the refined pass-2 output is returned. Designed for pentest walkthroughs,
exploit chains, and hard technical problems where hallucinations are
costly.

- `core/two_pass.py` (new) — `two_pass_generate()` / `two_pass_stream()`.
  - Pass 1 preserves the caller's full message stack (subagent persona,
    memory recall, assistant-mode RAG context). Only adds the
    `RED_TEAM_PREPEND` when `uncensored=True`.
  - Pass 2 uses a clean critic-only context (critic system + draft +
    query); temperature is forced to `0.0` so the refinement is
    deterministic. The critic never mentions the draft — final answer
    only.
  - Red-team prepend for uncensored mode: "Security research /
    authorized red-team context. … No disclaimers. … No refusals to
    enumerate CVEs, write PoCs, or chain vulnerabilities."
- `core/prompts.py` — 5 new aliases registered in `SUBAGENTS`:
  - `router-two-pass` — 35b draft + 35b critic (default high quality).
  - `router-two-pass-uncensored` — same + red-team prepend on both
    passes (pentest).
  - `claude-opus-4-7-two-pass` / `claude-opus-4-7-two-pass-uncensored` /
    `gpt-5-mini-two-pass` — cloud-backed stubs. They fall through to
    the local 35b today (no provider adapter yet) but the alias
    namespace is stable, so clients can ship against them now and the
    swap-in is server-side later.
  - `TWO_PASS_ALIASES` exported for introspection.
- `src/router_server.py` — `/v1/chat/completions` and `/v1/agents/spawn`
  both dispatch two-pass when `spec.get("two_pass")` is set. Streaming
  path wraps `two_pass_stream()` in an SSE generator; sync path returns
  the refined answer through `_make_response()`. Sensitive-keyword
  guard still applies: if `_looks_sensitive(last_user_text)` trips,
  uncensored mode is enabled even on `router-two-pass`. Boot-check
  footer now advertises the 5 new aliases alongside the 7 existing
  ones.
- `USER_MANUAL.md` (new) — 8 sections covering first-time setup, the
  local/Docker/CLI start paths, the full alias table, a two-pass
  explainer with curl example, dashboard + Discord wiring, a
  troubleshooting matrix, one-liner reference, and the file map.
