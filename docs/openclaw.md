# OpenClaw — Deep-Research Capability Inventory (verified against primary sources)

> Status: **REWRITTEN 2026-08-17**. The prior version of this file was a 37-line
> stub saved by a peer audit tool that stopped mid-sentence (§2 "Embedded runtime
> per agent", §4 "Supply Chain Poisonin") and never verified anything. This version
> is a from-scratch inventory built from live fetches of the primary sources
> (`docs.openclaw.ai`, the `openclaw/openclaw` GitHub repo + issue tracker, vendor
> security write-ups) on 2026-08-17. Verified-by-table below; every claim is traced
> to a fetched page unless marked "unverified".

---

## 1. What OpenClaw is

- **Positioning:** "Any OS gateway for AI agents" — a single, self-hostable,
  daemonized gateway that connects an AI agent runtime to messaging channels +
  a browser Control-UI. Developed by the **OpenClaw Foundation** (non-profit,
  `openclaw.org`); not an OS or a cloud service. Rebranded from an earlier
  product ("Warelay"), see §11.
- **Slogan basis:** "Discord, Google Chat, iMessage, Matrix, Microsoft Teams,
  Signal, Slack, Telegram, WhatsApp, Zalo, and more" (docs main page).
- **Form factor:** one binary + `~/.openclaw/` state. Optional browser dashboard
  at `http://127.0.0.1:18789/`.
- **Reference hardware:** 2-core CPU / 4 GB RAM / 100 GB disk; Ubuntu 24.04
  recommended. Node 26 recommended (22.22.3+, 24.15+, 25.9+ also OK).

### 1.1 Install + onboard surface
- `npm install -g openclaw@latest --allow-scripts=openclaw`
- `openclaw onboard --install-daemon` (guided: config, channels, pairing),
  `openclaw configure`, `openclaw dashboard`, `openclaw doctor` (+`--fix`).
- `setup --baseline` variant applies a hardened baseline config.
- **VERIFIED version line:** current stable as of mid-2026 was `2026.6.x`;
  `v2026.3.22` shipped 45 features / 13 breaking changes / 82 bug fixes /
  20 security patches. The CVE patch landed in `v2026.1.29` (30 Jan 2026).

---

## 2. Config system (root)

- Location: `~/.openclaw/openclaw.json`, JSON5 (comments + trailing commas OK),
  hot-reloads. Override path via `OPENCLAW_CONFIG_PATH`.
- **Two-bucket rule** (the semantics that matter):
  - **Root siblings** = infrastructure + cross-agent defaults (channels, model
    catalog, gateway auth, nodes).
  - `agents.defaults` = defaults applied to every agent-loop (model, tools,
    sandbox, skills, heartbeat, session scope, prompt headers).
  - `agents.entries` = per-agent overrides (`main`, plus any others you define);
    an entry overrides `defaults` by key.
- **Strict schema validation**: an unknown key refuses to start the gateway
  (fail-closed config), and a JSON5 parse error keeps the last-known-good file.
- CLI: `openclaw config get|set|unset|file|schema|validate` + `doctor --fix`.
- Channel config pattern: `channels.<name>.enabled`, `allowFrom` (allowlist),
  per-channel tokens/credentials.

---

## 3. Channels

- **Core install ships exactly two:** **Telegram** (bot token) and **WebChat**
  (browser chat UI against the same agent). Everything else is a **plugin**.
- Official plugin channels (`openclaw plugins install @openclaw/<id>`):
  Discord, Feishu, Google Chat, iMessage, IRC, LINE, Matrix, Mattermost,
  Microsoft Teams, Nextcloud Talk, Nostr, QQ Bot, Raft, Signal, Slack, SMS,
  Synology Chat, Tlon, Twitch, Voice Call, WhatsApp, Zalo, Zalo Personal
  (23 listed).
- External/maintained-elsewhere: WeChat, Yuanbao, Zalo ClawBot.
- **Count: ~25 first-party (2 core + 23 plugin) + 3 external. The "36 channels"
  figure that circulates is UNVERIFIED against the docs; the verified count is
  ~25–28.**
- **Direct-message policy** (`dmPolicy`): `pairing` | `allowlist` | `open` |
  `disabled`. Default `pairing`: the owner runs `openclaw pairing list/approve`
  to accept an incoming DM; pairing codes expire after 1 hour and only 3 pending
  pairings are kept (rate-bounded). `allowlist` = fixed allowFrom list.
- **Session scoping** (`dmScope`): `main` (one shared main session), or
  per-channel-peer / per-account-channel-peer / per-peer isolation, plus
  `threadBindings` so a thread stays pinned to one session. Sessions reset on
  a schedule (daily or idle).
- Media in/out: images, audio, video, documents; voice transcription +
  multi-provider TTS; image/video generation forwarded to the agent.
- Mobile: iOS/Android **nodes** pair via QR/code (camera + Canvas + voice).

---

## 4. Agent runtime

- One embedded agent runtime per agent-loop; default agent `main` (+ you can
  define `writer`, `docs`, coding agents, etc. — an entry per role).
- Provider pool: `agents.defaults.model.primary` + `fallbacks` array; 35+
  providers; model identifiers are `provider/model` refs; a
  `modelPolicy.allow` allowlist restricts which models may be used;
  `imageMaxDimensionPx` (default 1200) bounds vision input.
- Model failover and **auth rotation** on failures (falls through the fallback
  list when a provider 4xx/5xxs).
- **Heartbeat**: `agents.defaults.heartbeat.every` (default 30 min) has the
  agent message the owner if there's nothing else to say — a periodic
  "still here" / state-check-in pattern.
- Multi-agent orchestration ("workspaces" / Paperclip doc): specialist worker
  agents each own their session/memory/skills; routing picks a worker per
  task; sessions isolated per agent-workspace-sender.
- Tools: built-in toolchain + external MCP servers; skills (see §6); browser
  control tool; exec tool (shell); memory + wiki tools; web-search providers
  (Brave, DuckDuckGo, Exa, Firecrawl AI, Gemini, Grok, Kimi, MiniMax, Ollama
  WS, Perplexity, SearXNG, Tavily).

---

## 5. Cron / automations

- Primary command family: `openclaw automations …`; **`openclaw cron …` is an
  alias**. Subcommands: `add|create|edit|run|runs|list|get|show|rm|enable|
  disable|status|scratch`.
- Schedule syntax is a standard **cron expression**; `--at <time>` = one-shot.
- Launch modes:
  - `--agent <name>` (default `main`) — run the automation as a full agent turn.
  - `--command …` + `--command-argv` / `--command-cwd` / `--command-env` /
    `--command-input` — run a shell command (executes in the Gateway process).
  - `--webhook <url>` — POST the result to an external endpoint.
- Delivery flags: `--announce` (broadcast even if a conversation is idle),
  `--no-deliver` (run but don't message anyone), `--channel` / `--to` /
  `--thread-id` (pin delivery to a channel/thread).
- Model flags: `--model`, `--fallbacks`, `--thinking`.
- Session scoping for the job: `--session main|isolated|current|session:<id>`.
- **Retries:** exponential backoff, fixed ladder 30 s / 1 m / 5 m / 15 m /
  60 m on failure; **failure alerts** are delivered to the owner channel.
- **State:** SQLite-backed with a legacy `jobs.json` migration path; run
  history capped (~2000 rows; `agentRetention`/`sessionRetention` 24 h prune);
  `automations runs` lists recent runs.
- **NO_REPLY suppression**: runs that produce no user-facing output don't spam
  the channel.
- Local-provider **preflight** before dispatch (skip a run if the local model
  is down, instead of firing a doomed cron job).

---

## 6. Skills system

- **Format:** `SKILL.md` (AgentSkills spec) — Markdown file + YAML frontmatter
  (`name`, `description`, `version`, `metadata.openclaw.requires`
  [bins/env/config], etc.).
- **Load precedence** (highest wins):
  1. workspace skills (`.agents/skills` in the project / work directory)
  2. project-agent skills (`<workspace>/.agents/skills`)
  3. personal (`~/.agents/skills`)
  4. managed (state-directory / installed)
  5. bundled defaults
  - `extraDirs` can inject more.
- **Per-agent allowlists** — an agent only sees the skills its config allows
  (unlike a global "everyone can use everything" registry).
- **Invocation:** `$skill` references in prompts (max 8 per message), slash
  commands (`/skill-name args`), and `command-dispatch:tool` (skill maps to a
  tool call).
- **Skill Workshop** (`openclaw skills workshop list|inspect|evaluate|apply`):
  agents can draft a skill proposal; a human *applies* it — an approval queue,
  not auto-install.
- **ClawHub** marketplace install: `openclaw skills install @owner/slug` with a
  trust-envelope fetch; install verified, then security-scanned.

### 6.1 Skills supply-chain security (see §8 for the attack)
- Install-time scanning: **VirusTotal** + **ClawScan** static analysis;
  `installPolicy` is **fail-closed** (an unscannable skill is refused).
- Path containment + **secret-injection scope** for skills that use exec.

---

## 7. Memory

- `openclaw memory status|index|search`; agent message history is persisted,
  embeddable/indexed; a wiki-style long-term store is a documented feature.
- Config store is SQLite; backups via `openclaw backup` / `migrate`.

---

## 8. Security model + the 2026 supply-chain incident

- **Trust model is explicitly a personal assistant, NOT a multi-tenant host.**
  The docs state the gateway is a single-trust-boundary (one operator) — do not
  deploy it facing untrusted users. This is a documented security limitation,
  not a feature gap.
- **Auth**: `gateway.auth` — token / password / trusted-proxy / device trust;
  optional `SWARM_*`-style API token on the gateway HTTP surface.

### 8.1 CVE-2026-25253 (this is the "supply chain poisoning" Gemini flagged)
- **CVE-2026-25253, CVSS 8.8 HIGH**, "Cross-Site WebSocket Hijacking" in
  ClawHub/ClawHavoc. Reported in GitHub issue **`openclaw/openclaw#16052`**
  (closed as `not_planned`, patched instead).
- **Discovered 2026-02-13**; patched in **`v2026.1.29` (30 Jan 2026)**.
- **The attack the CVE is bundled with:** ~**341 malicious skills of 2,857
  audited (≈12%)** analyzed by Koi Security (report 2026-02-01). Malware
  families: **Atomic Stealer / AMOS (macOS)** + **ClickFix (Windows)**. Targets:
  crypto wallets, browser passwords, SSH keys, API tokens.
- Attacker pattern: fake "skill packs" + **14 compromised contributor accounts**
  (per Bitdefender); the seed sample `openclawdir.com/skills/deeps-agnw6h`.
- Downstream audits: Snyk 76 malware / 1,467 skill-issues (their "ToxicSkills"
  ~13.4%); Bitdefender ~900 malicious (~20%); Bitsight **30,000+ exposed
  gateway instances** on the public internet.
- **ClawHub countermeasures (live):** VirusTotal integration shipped 2026-02-08;
  Koi shipped a check tool (**Clawdex**); community scanner (ClawScan);
  fail-closed install policy. The ecosystem's response is ongoing.
- **MCP backdoor detail**: the malicious skills could be staged as MCP servers
  with a **reverse tunnel on port 44876**; the fake repo
  `toolitletolate/openclaw_windriver` carried the Windows payload.

### 8.2 Security audit surface (for an auditor to run on a live instance)
- `openclaw security audit [--deep|--fix|--json]`; `--deep` runs live probes.
- Check families have typed IDs: `fs.*`, `gateway.*`, `hooks.*`, `browser.*`,
  `sandbox.*`, `tools.exec.*`, `plugins.*`, `skills.*`, `security.exposure.*`.
- Hardened baseline via `setup --baseline`; `doctor --fix` repairs drift.
- Prompt-injection arena numbers quoted by the docs (2026): Claude Opus 4.5 ~0.5%,
  Sonnet 4.5 ~1.0%, Haiku 4.5 ~1.3%, Gemini 2.5 Pro ~8.5% — used to justify the
  model allowlist + tool-boundary enforcement posture.

---

## 9. Sandbox & exec

- `agents.defaults.sandbox.mode`: `off` | `non-main` (only non-main agents
  sandboxed) | `all`. `scope`: `session` | `agent` | `shared`.
- Sandbox provisioning via `scripts/sandbox-setup.sh`; exec tool + browser
  control run inside the sandbox when enabled.
- Exec-policy + approvals:
  `openclaw approvals`, `openclaw exec-policy`, `openclaw sandbox`, `openclaw tui`.

---

## 10. CLI surface (condensed, verified)

- Onboard/admin: `setup`, `onboard`, `configure`, `config (get|set|unset|file|
  schema|validate)`, `completion`, `doctor`, `dashboard`, `backup`, `migrate`,
  `reset`, `uninstall`, `update`.
- Messaging: `message (send|broadcast|poll|react|read|edit|delete|…)`,
  `channels`, `pairing (list|approve|…)`, `qr`, `transcripts`.
- Agent: `agent`, `agents`, `attach`, `acp` (Agent Client Protocol),
  `browser`, `mcp`, `tools`.
- Ops: `status`, `health`, `sessions`, `resume`, `audit`, `gateway`,
  `logs`, `system`, `devices`, `nodes`, `node`, `worker`, `directory`,
  `tui`, `browser`, `dns`, `docs`.
- Jobs: `cron` (alias `automations`), `tasks`, `hooks`, `webhooks`.
- Learning/knowledge: `memory (status|index|search)`, `wiki`,
  `models`, `promos`, `infer`, `skills (install|workshop|…)`,
  `security`, `secrets`, `plugins`, `proxy`, `approvals`, `exec-policy`,
  `sandbox`.
- Legacy aliases: `daemon`, `clawbot`.
- Global flags: `--dev`, `--profile`, `--container`, `--log-level`,
  `--no-color`, `--update`, `-V`.
- Output convention: humans get tables/pretty-text; `--json` gives structured
  output; errors are an `{ ok: false, error: { type, message } }` envelope.

---

## 11. Team / governance (context, verified)

- Founded Nov 2025 by Steinberger as **"Warelay"**, repeatedly rebranded, the
  OpenClaw name officially landed January 2026; Steinberger joined OpenAI
  mid-Feb 2026; the **OpenClaw Foundation** (non-profit) stewards the project.
- Sponsors/backers listed: OpenAI, GitHub, NVIDIA, Vercel, Blacksmith, Convex.
- Metrics circulating: ~380k GitHub stars / ~79.6k forks — **unverified by me**
  (repo API not re-fetched); treat as community-reported.

---

## 12. Verified-by table

| Claim | Source (fetched live 2026-08-17) |
|---|---|
| Gateway positioning + channel list | docs.openclaw.ai main page |
| Plugin-channel list, 2 core + 23 official + 3 external | docs.openclaw.ai/concepts/features |
| Install + Node versions + dashboard port | docs.openclaw.ai/start/install (port unchanged since 18789) |
| onboard/setup/configure/dashboard/doctor | docs.openclaw.ai/cli (index) |
| Config two-bucket semantics + strict schema + hot reload | docs.openclaw.ai/gateway/configuration |
| automations (alias cron) subcommands + flags + retries + session scope | docs.openclaw.ai/cli/cron |
| dmPolicy values + pairing expiry/caps | docs.openclaw.ai/channels (dmPolicy) |
| skills precedence + workshop + ClawHub + scanning | docs.openclaw.ai/tools/skills |
| security audit surface + trust model + gateway.auth | docs.openclaw.ai/gateway/security |
| CVE-2026-25253 CVSS 8.8 HIGH, issue #16052, 2026-02-13, patched 2026-01-30 | GitHub issue + vendor write-ups + Koi/Snyk/Bitdefender reports |
| heartbeat default 30m, modelPolicy.allow, imageMaxDimensionPx | docs.openclaw.ai (gateway/model + agent pages) |
| sandbox modes/scopes, exec-policy, approvals, browser tool | docs.openclaw.ai (gateway/sandbox, cli tree) |
| team/provenance + version cadence | docs + press (openclaw.ai/about + news) |
| "36 channels" claim | **UNVERIFIED — the verified count is ~25 first-party + 3 external** |

---

## 13. What this doc does NOT claim

- Desktop automation parity, Node/plugin internals, or the full per-plugin CLI
  trees (not needed for the capability map).
- The star/fork counts (community-reported only).
- Live-run evidence: no OpenClaw instance was installed; everything above is
  documentation + incident reporting, not a hands-on probe.