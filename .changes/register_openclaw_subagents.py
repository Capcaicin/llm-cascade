"""Register the router's subagent aliases in OpenClaw's config.

Idempotent: re-running leaves an already-registered config unchanged. Makes
a timestamped backup before first modification so the old config is recoverable.

Schema note:
  - `agents.defaults.model` accepts ONLY {primary, fallbacks}. Anything else
    triggers `agents.defaults.model: Invalid input` from OpenClaw's doctor.
  - The per-model alias map lives at `agents.defaults.models` (plural sibling).
  - Provider model lists live at `models.providers.vllm.models` (array).
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

CFG = Path(os.path.expanduser("~/.openclaw/openclaw.json"))

# Mirror core/prompts.py:SUBAGENTS. We repeat the list here so this script can
# be run without importing the project (handy for a post-install hook).
SUBAGENT_ALIASES = {
    "router":             "Two-Tier Router (auto 4b->35b)",
    "router-assistant":   "cross-workspace assistant (35b abliterated + Clarity)",
    "router-fast":        "speed-first: free models for safe tasks",
    "router-sub-coder":   "code worker (qwen2.5-coder:14b)",
    "router-sub-draft":   "fast first-pass drafter (llama3.1:8b)",
    "router-sub-critic":  "reviewer (mistral:latest)",
    "router-sub-research": "research synthesizer (35b + cross-workspace RAG)",
}

# Keys that must never appear inside agents.defaults.model (schema allows only
# these two)
MODEL_LEGAL = {"primary", "fallbacks"}

# Regex for garbled legacy keys (mojibake-era "sort 4b -> 35b" artifacts)
_GARBLED_RE = re.compile(r"[\u0080-\u009f]|\u2020|vllm/sort\s+.*qwen")


def main() -> int:
    if not CFG.exists():
        print(f"[!] {CFG} not found - is OpenClaw installed?", flush=True)
        return 1

    with open(CFG, encoding="utf-8") as f:
        cfg = json.load(f)

    changed = False

    defaults = cfg.setdefault("agents", {}).setdefault("defaults", {})
    model    = defaults.setdefault("model", {})
    models   = defaults.setdefault("models", {})   # plural - alias map

    # 1) Migrate any illegal nested agents.defaults.model.* keys out
    for bad in [k for k in list(model.keys()) if k not in MODEL_LEGAL]:
        if bad == "models" and isinstance(model[bad], dict):
            for k, v in model[bad].items():
                models.setdefault(k, v)
        del model[bad]
        changed = True

    # 2) Scrub garbled legacy keys from the alias map
    for k in [k for k in list(models.keys()) if _GARBLED_RE.search(k)]:
        del models[k]
        changed = True

    # 3) Add alias entries (visible in OpenClaw UI)
    for alias, desc in SUBAGENT_ALIASES.items():
        key = f"vllm/{alias}"
        if key not in models:
            models[key] = {"alias": desc}
            changed = True

    # 4) Append to fallbacks so supervisors can retry through them
    fallbacks = model.setdefault("fallbacks", [])
    for alias in SUBAGENT_ALIASES:
        key = f"vllm/{alias}"
        if key not in fallbacks:
            fallbacks.append(key)
            changed = True

    # 5) Register each alias with the vllm provider's model list
    vllm_models = (
        cfg.setdefault("models", {})
           .setdefault("providers", {})
           .setdefault("vllm", {})
           .setdefault("models", [])
    )
    existing_ids = {m.get("id") for m in vllm_models if isinstance(m, dict)}
    for alias, desc in SUBAGENT_ALIASES.items():
        if alias in existing_ids:
            continue
        vllm_models.append({
            "api": "openai-completions",
            "contextWindow": 32768,
            "cost": {"cacheRead": 0, "cacheWrite": 0, "input": 0, "output": 0},
            "id": alias,
            "input": ["text"],
            "maxTokens": 4096,
            "name": desc,
            "reasoning": False,
        })
        changed = True

    if not changed:
        print("[=] OpenClaw config already aligned - no changes.")
        return 0

    # Timestamped backup
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = CFG.with_suffix(f".json.pre-subagent-{ts}.bak")
    shutil.copy2(CFG, backup)
    print(f"[+] Backup: {backup}")

    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    print(f"[+] Aligned {len(SUBAGENT_ALIASES)} subagent aliases in {CFG}")
    for alias in SUBAGENT_ALIASES:
        print(f"    - vllm/{alias}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
