# llm-cascade

![Rust](https://img.shields.io/badge/Rust-1.70%2B-orange)
![License](https://img.shields.io/badge/License-MIT-blue)
![Status](https://img.shields.io/badge/Status-Active-success)

**Resilient LLM routing with failover, cooldowns, and persistence.**

`llm-cascade` is a Rust crate that routes a single request across multiple LLM providers in sequence until one succeeds. It handles failures, tracks provider health, and avoids retrying broken APIs like an optimistic intern.

---

## 🧠 Why this exists

LLM APIs fail. They rate-limit, timeout, hallucinate, or just decide today isn’t their day.

Instead of:

> “hope OpenAI works”

You get:

> “try OpenAI → fallback to Anthropic → fallback to Gemini → fallback to local Ollama”

Automatically.

---

## ✨ Features

* 🔁 **Cascade Execution** — Try multiple providers in order
* 🚫 **Cooldown System** — Skip providers that recently failed
* 🧠 **Failure Tracking** — Persistent attempt logs in SQLite
* 🔐 **Secure Secrets** — OS keyring + env fallback
* ⚙️ **TOML Config** — Simple, explicit control over behavior
* 💾 **Persistence** — Failed conversations saved for debugging

---

## 📦 Installation

Add to your `Cargo.toml`:

```toml
[dependencies]
llm-cascade = "*"
```

(Replace `*` with an actual version when you’re feeling responsible.)

---

## 🚀 Quick Start

```rust
use llm_cascade::{run_cascade, load_config, db, Conversation};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config = load_config(&"config.toml".into())?;
    let conn = db::init_db(&config.database.path)?;

    let conversation = Conversation::single_user_prompt("Explain recursion simply");

    match run_cascade("default", &conversation, &config, &conn).await {
        Ok(response) => println!("{}", response.text_only()),
        Err(e) => eprintln!("All providers failed: {e}"),
    }

    Ok(())
}
```

---

## ⚙️ Configuration

Everything is controlled via `config.toml`.

### Example

```toml
[database]
path = "~/.llm-cascade/state.sqlite"

[failures]
cooldown_seconds = 300
max_failures = 3

[[cascades]]
name = "default"
providers = [
  { type = "openai", model = "gpt-4o-mini" },
  { type = "anthropic", model = "claude-3-5-sonnet-latest" },
  { type = "gemini", model = "gemini-2.0-flash" },
  { type = "ollama", model = "llama3.1" }
]
```

### How it works

1. Providers are tried **top to bottom**
2. Failures are recorded
3. Providers exceeding `max_failures` enter cooldown
4. Cooldown expires → provider re-enters rotation
5. First success wins

No overthinking. Just reliability.

---

## 🔌 Supported Providers

* OpenAI
* Anthropic
* Gemini
* Ollama (local)

Mix cloud + local like a rational person who doesn’t trust any single vendor.

---

## 🧩 Project Structure

```
src/
├── cascade/        # Execution engine
├── config/         # Config parsing + types
├── db/             # SQLite state + logs
├── providers/      # API integrations
├── models/         # Messages, responses, roles
├── persistence/    # Save failed conversations
├── secrets/        # API key handling
└── error/          # Error types
```

---

## 🧪 Testing

```bash
cargo test
```

---

## 💡 Use Cases

* Reliable production chat systems
* Cheap-first → expensive fallback routing
* Rate-limit resilience
* Local-first setups with cloud backup

---

## ⚠️ Philosophy

This is **not** an agent framework.

It doesn’t:

* plan tasks
* spawn agents
* pretend to be sentient

It does one job:

> make LLM calls more reliable

And it does it without turning your codebase into a personality disorder.

---

## 📄 License

MIT

---

Built for people who are tired of their AI stack failing silently and calling it "edge cases."
