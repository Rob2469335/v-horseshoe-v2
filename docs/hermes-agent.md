# Hermes Agent — Capability Inventory

Research snapshot of the open-source **Hermes Agent** (Nous Research) as of 2026.
Sources: official docs (`hermes-agent.nousresearch.com/docs`) and the repos
`github.com/NousResearch/hermes-agent` (MIT) and `github.com/NousResearch/atropos`.

Origin: web research session 2026-08-17. Exact names/config keys/commands come
from the docs except where noted. Install: `curl -fsSL
https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash`
(Linux, macOS, WSL2, Termux). One-liner: *"terminal-native autonomous coding and
task agent ... persistent memory ... agent-created skills ... messaging gateway on
21+ platforms (19 native + IRC + Microsoft Teams via plugins) ... Runs on local,
Docker, SSH, Daytona, Modal, or Singularity backends ... works with Nous Portal,
OpenRouter, OpenAI, Anthropic, Google, or any OpenAI-compatible endpoint."*

---

## 1. Cron / Background Jobs

Scheduler is unified and full-featured; a single background process owns sessions,
platform gateways, AND cron. Job state is durable on disk and resumable.

- **CLI**: `hermes cron <list|create|edit|pause|resume|run|remove|status|tick>`
  - `create` / `add` — from a prompt, attach one or more skills via repeated `--skill`
  - `edit`, `pause`, `resume`, `run` (manual, async), `list`, `status`, `tick` (inspect/tick the scheduler)
- **In-chat**: `/cron add 30m "Remind me..."`, `/cron add "every 2h" "..." --skill blogwatcher`
- **Natural language**: the internal `cronjob` tool lets the agent create/pause/edit/remove
  jobs in chat. Cron-run sessions **cannot** recurse-create cron jobs (loop guard).
- **Schedule formats**: relative delays (one-shots), intervals (recurring), cron
  expressions, ISO timestamps.
- **Model resolution at fire time**: per-job pin → `cron.model` / `cron.model_provider`
  in `config.yaml` → global default from `hermes model`. Model choices are user-owned
  (per-job pins can't be set by the agent). A drift guard fails closed: if an unpinned
  job's global model changes, the job **skips, makes no inference call, alerts once**
  (`#44585`). Disable with the drift-guard override.
- **No-agent mode (script-only)**: `hermes cron create "every 5m" --no-agent
  --script memory-watchdog.sh --deliver telegram --name "..."`. Script stdout delivered
  verbatim; empty stdout = silent tick; non-zero exit/timeout = error alert;
  `{"wakeAgent": false}` on the last line also silences. No tokens, no model. Scripts
  must resolve inside `$HERMES_HOME/scripts/`; subprocess env is sanitized (provider
  credentials stripped).
- **Chaining**: `context_from=<job_id>` prepends Job A's most recent output to Job B.
- **Delivery**: `--deliver telegram` (+ `--deliver discord:<target>`, etc.).
- **Storage**: jobs in `~/.hermes/cron/jobs.json`; execution state through `.tick.lock`
  and `executions.db` (execution history/status).
- **Config/env knobs** (reference page confirmed): `cron.script_timeout_seconds`
  (default 3600 — bounds the pre-run script only; skill/agent jobs use the separate
  inactivity budget `HERMES_CRON_TIMEOUT`), `HERMES_CRON_SCRIPT_TIMEOUT`,
  `HERMES_CRON_MAX_PARALLEL`, plus per-platform cron delivery targets
  (`TELEGRAM_CRON_THREAD_ID`, `GOOGLE_CHAT_HOME_CHANNEL`, `SIMPLEX_HOME_CHANNEL`, …).
- **Wait-agent semantics**: `wakeAgent` gates LLM invocations on demand.

Verified detail note: exact job schema fields (aside from the above + `no_agent`,
`name`, `skill`, `script`, `deliver`, `context_from`, `schedule`, `prompt`) live in
the repo's `cron/` package (`cron/jobs.py`, `cron/executions.py`); the docs page
`/docs/user-guide/features/cron` also has "Manual runs are asynchronous" and
"Toolsets available to cron jobs".

## 2. Skills

Two distinct systems: **user-authored skill files** in `~/.hermes/skills/` (SKILL.md)
and a **Skills Hub** for install/management.

- **Anatomy**: SKILL.md (YAML frontmatter + markdown body), optional supporting dirs
  (`references/`, `scripts/`, `assets/`, `examples/`, `templates/`). Progressive
  disclosure; platform-specific skills; `[[as_document]]` forces doc-style delivery;
  fallback/conditional activation skills; secure setup on load.
- **First-class agent tool**: `skill_manage` — actions `create` / `patch` / `edit` /
  `delete` / `write_file` / `remove_file`. Agent can also `write_file` SKILL.md then
  manage. `/learn <source>` auto-authoring; large sources become knowledge-base skills.
- **Write gating**: `skills.write_approval: true` stages every agent skill write under
  `~/.hermes/pending/skills/` (survives restarts). Review with `/skills pending`,
  `/skills diff <id>`, `/skills approve <id>|all`, `/skills reject <id>|all`,
  `/skills approval on|off`. Separate content scanner: `skills.guard_agent_created`.
- **Skills Hub CLI**: `hermes skills <subcommand>` — `browse`, `search <q>
  --source <src>`, `inspect`, `install`, `check`, `update`, `audit`, `uninstall`,
  `reset`, `publish`, `snapshot export`, `opt-out`, `opt-in`, `tap add|list|remove`,
  `bundle` management (`hermes bundles`). Slash equivalents under `/skills`.
- **Hub sources**: `official` (repo optional-skills/, built-in trust), `skills-sh`
  (Vercel's skills.sh directory), `well-known` (`/.well-known/skills/index.json`
  endpoints), `url` (direct HTTP(S) SKILL.md), `github` (+ custom taps), plus
  `clawhub`, `lobehub`, `browse-sh` marketplaces. Default github taps: `openai/skills`,
  `anthropics/skills`, `huggingface/skills`, `NVIDIA/skills` (verifiable `skill.oms.sig`),
  `garrytan/gstack`.
- **Trust levels**: `builtin` / `official` (always trusted) / `trusted` (permissive
  policy) / `community` (everything else). Every hub install runs a **security scanner**
  (exfiltration, prompt injection, destructive commands, supply-chain signals).
  `--force` overrides caution/warn findings but **never** a `dangerous` verdict.
- **Upstream drift tracking**: `hermes skills check` / `update` compares stored source
  ident + content hash. Bundled skills sync via a hash manifest
  (`~/.hermes/skills/.bundled_manifest`); pristine copies auto-update, user-modified ones
  are preserved (use `hermes skills reset <name>` / `--restore` to un-stick).
- **Discoverability build**: `/docs/reference/skills-catalog` documents the shipped set.

## 3. Memory

Deliberately small, deterministic, and human-gated — not a vector store loop.

- **Two files** in `~/.hermes/memories/`: `MEMORY.md` (agent's personal notes, cap
  **2,200 chars** ≈ 800 tokens) and `USER.md` (user profile/preferences, cap **1,375
  chars** ≈ 500 tokens).
- **Injection**: rendered into the system prompt at session start as a frozen block
  with usage % + per-entry `§` delimiters; never changes mid-session (preserves KV
  prefix cache). Tool responses always reflect live state.
- **`memory` tool actions**: `add`, `replace`, `remove` — substring matching via
  `old_text` (unique match required). **No `read` action**; content is always in context.
- **Capacity discipline**: no auto-compaction — an overflowing write returns an error
  with `current_entries` + usage; the agent consolidates/removes in the same turn.
  Duplicate-prevention inside the tool. Best practice: consolidate above 80% full.
- **Write gating**: `memory.write_approval` (default `false`) mirrors the skills gate.
  When on, messaging sessions and background self-improvement stage writes for
  `/memory pending` → `/memory approve <id>` / `/memory reject <id>`; `/memory
  approval on|off` toggles at runtime.
- **Config keys** (`config.yaml` → `memory:`): `memory_enabled`, `user_profile_enabled`,
  `memory_char_limit` (2200), `user_char_limit` (1375), `write_approval`.
- **External memory providers** exist (`/docs/user-guide/features/memory-providers`)
  for shared/cross-profile memory; `hermes memory <subcommand>` and `hermes plugins`
  manage providers.
- Prompt-injection protection applied to context files (AGENTS.md, .cursorrules,
  SOUL.md) before inclusion (see section 7).

## 4. Subagents / Delegation

Recursive `delegate_task` fan-out with full observability and in-flight control.

- **Tool**: `delegate_task` — spawns a child agent in a fresh isolated conversation.
  Populated by the model itself (parent orchestrates its own running children via the
  same tool: actions `list`, `steer`, `stop`). Also interactive `/agents` (alias
  `/tasks`) TUI overlay: live tree by parent, per-branch cost/token/file rollups,
  per-subagent kill/pause, post-hoc turn-by-turn review.
- **Defaults**: 3 concurrent subagents (configurable). **No wall-clock timeout by
  default** — children fail from API/tool errors or iteration budget only. Opt-in hard
  cap: `delegation: child_timeout_seconds: 1800` (floor 30s); on fire, the result
  carries structured `timeout_seconds` / `timed_out_after_seconds` / `timeout_phase`
  (`before_first_llm_call` | `after_llm_calls`). Zero-call timeouts log a diagnostic to
  `~/.hermes/logs/subagent-timeout-<session>-<timestamp>.log` (config snapshot,
  credential-resolution trace, stack traces for all live threads).
- **Config** (`config.yaml` → `delegation:`): `max_concurrent_children` (default 3,
  floor 1; also `DELEGATION_MAX_CONCURRENT_CHILDREN`), `max_spawn_depth` (1 default =
  flat — children can't spawn; 2-3 nest, up to 3×3×3 = 27 concurrent leaves),
  `worktree_isolation` (per-child git worktree from HEAD), `orchestrator_enabled`
  (kill switch for `role="orchestrator"`), and `provider`/`model`/`base_url`/`api_key`
  overrides (subagents inherit the parent's provider:model by default;
  `delegation.base_url` beats `delegation.provider`).
- **Background delegations** (`delegate_task(background=true)`): watched by a
  progress-based **stall monitor** (on by default, zero config). Progressing children
  never touched; frozen past `stall_threshold_seconds` (450s idle / 1200s in-tool) →
  interrupted with a 120s grace window → force-finalized with a terminal `stalled`
  event carrying `stalled_after_quiet_seconds`, `stall_phase` (`idle`/`in_tool`),
  `stall_grace_seconds`. Root wedge fixed: children run OpenAI-wire requests inline on
  their own conversation thread, not a nested worker thread.
- **Steering**: `/steer` and the model-facing `steer` action queue text into a live
  child at its next tool-result boundary without cutting the in-flight tool.
  `subagent.steer` / `subagent.interrupt` gateway-RPC methods mirror it
  (`{"method": "subagent.steer", "params": {session_id, subagent_id, text}}`); only the
  spawning UI/gateway session can steer (missing/foreign/stale session rejected).
  Acceptance is synchronized: results surface `pending_steer` / `missed_steer`.
- **Live transcripts**: every dispatch pre-creates append-only logs at
  `<hermes_home>/cache/delegation/live/<delegation_id>/task-<n>.log` (timestamped
  assistant text, thoughts, tool calls/results, final status) + a `manifest.json`.
  Pruned after 7 days; readable from remote terminal backends.
- **vs `execute_code`**: delegate_task = full LLM reasoning loop, fresh isolated
  conversation, all non-blocked tools (higher token cost). execute_code = pure Python
  execution, no conversation, 7 tools **via RPC without reasoning**, 3 concurrent by
  default, single script, only stdout returned (lower cost).

## 5. Messaging Gateway

Single background process connecting every configured platform; handles sessions,
runs cron jobs, delivers voice messages. CLI microphone mode + spoken replies +
Discord voice-channel conversations exist (Voice Mode).

- **Supported (intro quote)**: *"Chat with Hermes from Telegram, Discord, Slack,
  WhatsApp, Signal, SMS, Email, Home Assistant, Mattermost, Matrix, DingTalk,
  Feishu/Lark, WeCom, Weixin, BlueBubbles (iMessage), QQ, Yuanbao, Microsoft Teams,
  LINE, ntfy, or your browser."* (19 native + IRC + MS Teams via plugins; "21+").
- **Confirmed broader set** (allowlist env vars on the reference page): Google Chat
  (Pub/Sub or HTTP events), WhatsApp Cloud API, SimpleX (`SIMPLEX_ALLOWED_USERS`,
  `SIMPLEX_GROUP_ALLOWED`, `SIMPLEX_AUTO_ACCEPT`, WS daemon), Photon/Spectrum iMessage
  (`PHOTON_*`, node sidecar, mention wake-words + tapbacks), Buzz (npub/pubkey
  allowlist), QQ (incl. `QQ_GROUP_ALLOWED_USERS`), LINE
  (`LINE_ALLOWED_USERS/GROUPS/ROOMS`), ntfy (topics as user ids), IRC (nicks), Weixin
  (incl. `WEIXIN_GROUP_ALLOWED_USERS`), plus per-platform group allowlists
  (`TELEGRAM_GROUP_ALLOWED_USERS`, `SIGNAL_GROUP_ALLOWED_USERS`) and most platforms'
  `*_ALLOW_ALL_USERS` dev switch.
- **Per-platform sub-binary processes** (gateway runs several at once): `hermes-
  telegram`, `hermes-discord`, `hermes-slack`, `hermes-whatsapp`, `hermes-signal`,
  `hermes-sms`, `hermes-email`, `hermes-mattermost`, `hermes-matrix`,
  `hermes-dingtalk`, `hermes-feishu`, `hermes-wecom`, `hermes-wecom-callback`,
  `hermes-weixin`, `hermes-bluebubbles`, `hermes-qqbot`, `hermes-teams`, + Home
  Assistant, ntfy, LINE, Weixin. Also `hermes send --to <target> [--file]`
  (scriptable outbound; supports `MEDIA:` and `[[as_document]]` directives).
- **Hermes Relay** (experimental): connector system fronting Discord/Telegram/Slack/
  WhatsApp through an external connector that owns the credentials; capabilities
  negotiated per connector at handshake.
- **Authorization / allowlists**: env vars per platform — `TELEGRAM_ALLOWED_USERS`,
  `DISCORD_ALLOWED_USERS`, `SIGNAL_ALLOWED_USERS`, `SMS_ALLOWED_USERS`,
  `EMAIL_ALLOWED_USERS`, `MATTERMOST_ALLOWED_USERS`, `MATRIX_ALLOWED_USERS`,
  `DINGTALK_ALLOWED_USERS`, `FEISHU_ALLOWED_USERS`, `WECOM_ALLOWED_USERS`,
  `WECOM_CALLBACK_ALLOWED_USERS`, `TEAMS_ALLOWED_USERS`, and the global
  `GATEWAY_ALLOWED_USERS` / `GATEWAY_ALLOW_ALL_USERS`. Default denies everyone not
  allowlisted or paired. **Do not run `GATEWAY_ALLOW_ALL_USERS=true` with terminal
  access in production.**
- **DM pairing**: unknown users get a one-time code; approve/revoke via
  `hermes pairing <list|approve|revoke|clear-pending>` (e.g. `hermes pairing approve
  telegram XKGH5N7P`). Codes expire in 1h, rate-limited, crypto-random. Email is the
  exception (ignored unless explicitly enabled).
- **Admin vs regular users**: per platform AND per scope (DM vs group/channel).
  `allow_admin_from` + `user_allowed_commands` (+ `group_allow_admin_from`,
  `group_user_allowed_commands`). Admins get every slash command; regular users only
  the enabled ones plus an always-on `/help` + `/whoami` floor. DM admin status does
  not imply group admin. Backward-compatible (unset split = full access).
- **Busy-agent input modes**: `redirect` (default — restarts generation with context,
  tools finish safely), `queue` (next turn), `steer` (inject into current run at tool
  boundary). `/stop` remains a hard stop.
- **Channel overrides**: e.g. Discord `channel_overrides` map channel/thread id →
  `{model, provider, system_prompt}`; threads inherit parent's override.
- **Operational**: `/platform` command + automatic per-platform circuit breaker
  (paused platforms); restart notifications; typing indicators; session resume across
  gateway restarts; service mgmt `hermes gateway <subcommand>` (+ `--all-profiles`)
  incl. `install` (systemd/-launchd `hermes-gateway-<name>` / s6-overlay in Docker) and
  `enroll` for Relay (`--token`, `--connector-url`, `--gateway-id`, `--wake-url` →
  writes `GATEWAY_RELAY_ID/SECRET/DELIVERY_KEY/URL/WAKE_URL`). `GATEWAY_GLOBAL_BYPASS`
  / `display.tool_progress: log` for audit (`~/.hermes/logs/tool_calls.log`, rotating
  5 MB × 3, secret-redacted).

## 6. Profiles

A profile = a separate Hermes home dir. First-class multi-agent support.

- **Scope**: each profile owns `config.yaml`, `.env`, `SOUL.md`, memories, sessions,
  skills, cron jobs, and state DB — plus its own gateway process and bot tokens.
  Never point two agents at one profile (memory writes compound).
- **Alias commands**: `hermes profile create coder` immediately provides `coder chat`,
  `coder setup`, `coder gateway start`, `coder doctor`, `coder skills list`, `coder
  config set model.default ...` (equivalent to `hermes -p <name> <cmd>`).
- **Segregation**: profiles are NOT sandboxes and NOT workspaces — `terminal.cwd`
  controls the working dir; a sandbox controls filesystem access; a profile is just
  state directory separation. Give a second agent its own profile; use external memory
  providers for shared memory.
- **Per-profile gateways**: separate process + token each; conflicting-bot-token
  detection (Telegram/Discord/Slack/WhatsApp/Signal); per-profile service names
  (`hermes gateway install` → `hermes-gateway-coder`); s6-overlay supervision in the
  Docker image (`hermes profile create` registers `/run/service/gateway-<name>/`).
- **Management**: `hermes profile use` (sticky default), `hermes profile delete
  <name> [--yes]`, `hermes -p <name> setup`, per-profile env files under
  `~/.hermes/profiles/<name>/`; kanban worker routing via `--description "<role>"`
  at create time; distributions/export (`profile-distributions`, `git-worktrees`,
  `multi-profile-gateways`, `multi-connection-desktop` docs).

## 7. Sandboxing / Security / Terminal Backends

Execution is a pluggable backend behind a layered approval + scanning model.

- **Terminal backends** (config `terminal.backend:`): `local`, `ssh`, `docker`,
  `singularity`, `modal`, `daytona`, `vercel_sandbox`.
  - Comparison: `local` (no isolation, dangerous-cmd check ✅, dev/trusted), `ssh`
    (remote machine, ✅), `docker` (container, boundary = isolation, ❌ skipped check),
    `singularity` (HPC), `modal`, `daytona` (persistent cloud workspaces),
    `vercel_sandbox` (microVM). Compatibility note: Singularity ships
    `singularity_image`; Modal/Daytona ship `modal_image`/`daytona_image`
    (nikolaik/python-nodejs default); `container_persistent: true` on cloud sandboxes
    preserves filesystem state only (no live-process promise; `TERMINAL_LIFETIME_SECONDS`
    governs idle cleanup).
  - Docker specifics (one long-lived container shared across every process, session,
    `/new`, and subagent): `container_persistent: false` = fresh container per session;
    `docker_persist_across_processes`, `docker_orphan_reaper`,
    `docker_run_as_host_user`, `docker_mount_cwd_to_workspace`, `docker_env` (literal)
    vs `docker_forward_env` (secrets never sit in config), `docker_volumes` (`:ro` for
    read-only), `docker_network: false` air-gaps the container,
    `container_cpu/memory/disk` resource limits, `lifetime_seconds` idle-reaper.
  - SSH creds live in `~/.hermes/.env` (`TERMINAL_SSH_HOST/USER/KEY`) so they aren't
    exported with profile configs.
- **Dangerous-command approval** (all backends): approval modes incl. YOLO mode;
  hardline always-on blocklist floor; user-defined deny rules (`approvals.deny`);
  approval timeout; CLI + gateway approval flows; permanent allowlist; mining approval
  history to suggest rules (`hermes approvals suggest`). `command_allowlist` in
  config.yaml. Config shape (`config.yaml` → `approvals:`): `mode: smart|manual|off`
  (default smart), `deny` (glob rules), `smart_policy` (free-text policy fed to the
  LLM classifier slot `auxiliary.approval`), `denial_breaker_threshold`.
- **File-write safety**: always-blocked protected paths, optional `HERMES_WRITE_SAFE_ROOT`
  sandbox root, cron/Hermes state protected.
- **Environment passthrough** (sandbox filters):
  - `execute_code`: blocks vars containing `KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL/
    PASSWD/AUTH`; only safe-prefix vars pass.
  - `terminal` (local): blocks explicit Hermes infrastructure vars.
  - `terminal` (Docker/Modal): no host env by default.
  - MCP: only safe sys vars (`PATH HOME USER LANG LC_ALL TERM SHELL TMPDIR` + `XDG_*`) +
    configured `env`.
  - Skill-scoped override: `required_environment_variables` in SKILL.md auto-register
    as passthrough (missing vars not registered). Manual: `terminal.env_passthrough`.
  - Credential **files**: `required_credential_files` / `terminal.credential_files`
    (relative to `~/.hermes/`) mount read-only into Docker (`-v host:container:ro`)
    and sync into Modal; `docker_forward_env` for explicit container forwarding.
- **MCP credential handling**: filtered env above, plus credential redaction of error
  messages before they reach the LLM (`ghp_`, `sk-`, bearer, `token=`/`key=`) → `[REDACTED]`.
- **Website access policy**: `security.website_blocklist: {enabled, domains,
  shared_files}` enforced across web_search / web_extract / browser_navigate / all
  URL-capable tools.
- **SSRF protection** on all URL-capable tools: RFC 1918 private ranges, loopback,
  link-local (incl. `169.254.169.254`), CGNAT `100.64.0.0/10`, cloud metadata
  hostnames (`metadata.google.internal`), reserved/multicast/unspecified.
- **Tirith pre-exec scanning** (`github.com/sheeki03/tirith`): homograph URL spoofing,
  pipe-to-interpreter (`curl | bash`), terminal injection. Config
  (`security.tirith_enabled`, `tirith_path`, `tirith_timeout`, `tirith_fail_open`).
  Verdict feeds the approval flow (deny-by-default for suspicious/blocked). Auto-install
  with SHA-256 + cosign verification; silent skip on Windows/WSL (run under WSL to use).
- **Context-file injection protection**: AGENTS.md / .cursorrules / SOUL.md scanned for
  instruction-override attempts, hidden HTML comments, secret-read patterns, invisible
  unicode (zero-width, bidi overrides). Blocked files → `[BLOCKED: ...]` warning.
- **Supply-chain advisory checking**: built-in scanner (`hermes_cli/
  security_advisories.py`) flags known-compromised venv package versions (e.g. the May
  2026 `mistralai 2.4.6` poisoning) at CLI banner / `hermes doctor` / gateway startup;
  `hermes doctor --ack <advisory-id>` dismisses (persisted `config.security.
  acked_advisories`). Stdlib-only, one `importlib.metadata.version()` per advisory.
- **Lazy dependency install** (`tools/lazy_deps.py`): extras (Mistral TTS, ElevenLabs,
  Honcho, Bedrock, Slack, Matrix) install on first use via venv-scoped, name-only,
  allowlisted `pip install`; `security.allow_lazy_installs: false` to disable;
  failures surface as `FeatureUnavailable` (no retry storms).
- **Production checklist**: explicit allowlists (never `GATEWAY_ALLOW_ALL_USERS`),
  docker backend, resource limits, `chmod 600 ~/.hermes/.env`, DM pairing over
  hardcoded ids, audit `command_allowlist`, set `terminal.cwd`, run gateway as
  non-root, monitor `~/.hermes/logs/`, `hermes update` regularly. Network isolation via
  separate VM + `terminal.backend: ssh`.

## 8. Web Dashboard / API Server

Machine-level management surface for every profile on the box.

- **Launch**: `hermes dashboard` → `http://127.0.0.1:9119` (`--port`, `--host`,
  `--no-open`; `--isolated` for per-profile server). Requires extras: `uv pip install
  -e ".[web,pty]"` (FastAPI/Uvicorn + ptyprocess for the Chat tab on POSIX/WSL2).
- **Pages**: Status (agent version, gateway+platforms, active/recent sessions,
  auto-refresh 5s, resource-pressure/OOM banners), **Chat** (real TUI via `/api/pty`
  WebSocket + xterm.js WebGL; `/chat?resume=<id>`; session switcher rail; needs POSIX
  PTY), Config, API Keys, Sessions, Logs, Analytics, **Cron** (aggregates across
  profiles), Profiles (profile switcher in sidebar; `?profile=<name>` deep links;
  managed-profile amber banner), Skills, MCP, Webhooks, Pairing, Channels, System.
- **Auth**: loopback-only by default; non-loopback bind engages an auth gate that fails
  closed at startup without a configured provider. Providers: username/password
  (`HERMES_DASHBOARD_BASIC_AUTH_USERNAME/PASSWORD/PASSWORD_HASH/SECRET/TTL_SECONDS`),
  self-hosted OIDC (`HERMES_DASHBOARD_OIDC_ISSUER/CLIENT_ID/SCOPES`), bear-token
  (non-interactive), and Nous Portal OAuth — provisioned via `hermes dashboard
  register` (`HERMES_DASHBOARD_OAUTH_CLIENT_ID`) — plus a drain-control service
  credential (`HERMES_DASHBOARD_DRAIN_SECRET`). DNS-rebinding
  guard + peer-IP checks for remote Desktop connections (`/api/ws` + `/api/pty` are the
  real gated sockets, not just `/api/status`).
- **REST API** (`/docs/user-guide/features/api-server`): `GET/PUT /api/config`,
  `/api/config/defaults`, `/api/config/schema`, `GET/PUT/DELETE /api/env`, sessions
  CRUD + messages + search, `/api/logs`, `/api/analytics/usage`, cron jobs CRUD +
  `pause`/`resume`/`trigger`, skills list/toggle, `/api/tools/toolsets`, admin endpoints.
- **Config** (`config.yaml` → `dashboard:`): `theme`, `show_token_analytics`
  (off by default), `public_url` (`HERMES_DASHBOARD_PUBLIC_URL`), `basic_auth`
  (`username`/`password_hash`/`password`/`secret`/`session_ttl_seconds`), `drain_auth`.
  Honest caveat: the Analytics token/cost figures are a **local lower-bound estimate**
  (auxiliary calls, retries, fallbacks, cache writes excluded), not the provider bill.

## 9. CLI / Command Surface

- **Entrypoint**: `hermes [global-options] <command> [subcommand/options]` with `-p
  <profile>`. Distinction: `hermes model` = full provider wizard (out-of-session);
  `/model` = in-chat model picker. One-shot mode: `hermes -z "question"` (capturable
  output, `--provider/--model`, `--usage-file` JSON incl. `estimated_cost_usd`).
- **In-repo commands** (grouped):
  - Core run: `chat`, TUI (`hermes --tui`), `setup [model|tts|terminal|gateway|tools|agent]`
    (`--portal` one-shot: OAuth Nous Portal + Tool Gateway), `portal status|open|tools`.
  - Providers: `model`, `auth <sub>` (list/add/remove/reset/status/logout/, OAuth incl.
    Spotify PKCE), `fallback` (chain mgmt), `moa` (mixture-of-agents).
  - Automation: `cron`, `kanban`, `project` (named multi-folder workspaces,
    `bind-board`), `webhook subscribe|list|remove|test`, `egress` (iron-proxy tunnel
    daemon: install/setup/start/stop/restart/reload/status/disable/config).
  - Skills/skill-storage: `skills`, `bundles`, `curator` (CLI + gateway self-curation),
    `plugins` (unified plugins/memory-providers/context-engines).
  - State/data: `memory`, `sessions`, `insights`, `logs`, `prompt-size`, `checkpoints`,
    `backup`, `import`, `dump`, `debug share`, `doctor [--fix]` (incl. `--ack
    <advisory-id>`), `status [--all|--deep]`, `security audit`.
  - Messaging: `gateway`, `send` (`--to`, `--list`, `MEDIA:`/`[[as_document]]`),
    `pairing`, `whatsapp`, `slack manifest [--write]`, `telegram`, `teams`, relay
    `gateway enroll`.
  - Tools/infra: `tools [--summary]`, `mcp` (`hermes mcp install`, catalog entries),
    `computer-use` (+ `cua-driver` install), `pets`, `acp`, `lsp`, `secrets
    bitwarden|bw`, `migrate`, `proxy` (subscription proxy), `hooks`, `config`, `update`,
    `import`, `desktop`/session grouping.
  - Removed/replaced: `hermes login` → `hermes auth`; bare `hermes setup` on existing
    install = alias behavior.
- **Slash commands in-chat** (separate reference `/docs/reference/slash-commands`):
  `/cron`, `/skills` (+ hub verbs), `/agents` (≠ `/tasks`), `/steer`, `/stop`,
  `/whoami`, `/platform`, `/model`, `/skills approval on|off`, etc.

## 10. Atropos (Nous LLM RL Environments)

Separate repo: `github.com/NousResearch/atropos` — *"Nous Research's LLM RL Gym"*.
"Atropos is a Language Model Reinforcement Learning Environments framework for
collecting and evaluating LLM trajectories through diverse environments."

- **Core**: environment microservice framework for **async RL with LLMs**. Environments
  run as services; a shared **trajectory API** collects environment data and lets the
  trainer pull batches (trainer/inference engine NOT bundled). Designed to accelerate
  LLM-based RL research across interactive settings.
- **Environment types**: 📚 dataset (GSM8K, MMLU, custom HF datasets), 🎮 online
  (Blackjack, Taxi, text-based games), 🤖 RLAIF/RLHF (LLM judges/reward models), 🔄
  multi-turn RL (deepresearch, internal tool calling), 💻 code execution (MBPP,
  HumanEval via `coding_server.py`), 🖼️ multimodal (OCR-VQA, Clevr via
  `multimodal_dpo/`).
- **Reported results**: Tool-calling benchmark — Berkeley Function Calling: Parallel
  `10% → 46%` (4.6×), Simple `21% → 51.75%` (2.5×); artifact
  `NousResearch/DeepHermes-ToolCalling-Specialist-Atropos`, via
  `environments/tool_calling_server.py`. Financial fundamentals: directional prediction
  20% → 50% (2.5×) via `fundamental_prediction_environment.py`.
- **Layout**: `environments/` (+ `environments/community/` for contributions),
  `example_trainer/`, `helpers/`, `CONFIG.md`, `.env.example`, `.gitmodules`,
  pre-commit + secrets-baseline hygiene.
- **Usage**: `pip install atroposlib` (Python 3.10+); each env exposes a CLI:
  `python environments/gsm8k_server.py serve --openai.model_name
  Qwen/Qwen2.5-1.5B-Instruct --slurm false` (or `--config ... example.yaml,
  `--env.group_size 8` overrides). Subcommands: `serve`, `process` (offline rollout
  prep → JSONL batches), `evaluate`. Environments track completion lengths, eval
  accuracies, full rollouts, scores; trajectory-handler ships local debug tools
  (scoring = environment reward logic) without the distributed infra.
- **Current status**: repository is archived/read-only as of ~2025 (classic trail:
  NL-RL / gym-trajectories lineage). Relevant for the DeepHermes tool-calling RL
  story and as a trajectory-evaluation reference.

---

## Notes / Caveats

- Docs pages fetched: cron, skills, memory, delegation, profiles, security, docker,
  web-dashboard (+ API/auth), messaging gateway, CLI commands, configuration,
  environment-variables reference, skills-catalog nav, atropos README. Still not
  content-mined: deeper per-platform pages (telegram, teams, dingtalk) and `tui`;
  the repo's `DEFAULT_CONFIG` remains the unverified ground truth against which the
  `config.yaml` key names above should be spot-checked before feature-parity wiring
  (the doc's config shapes are the source here).
- The `cronjob` tool schema and `cron/jobs.py` field list were not fully captured
  (truncated); the CLI + doc give `action`, `schedule`, `prompt`, `skill`, `no_agent`,
  `script`, `deliver`, `name`, `context_from`, `model`, `provider`.
  `cron.script_timeout_seconds` and the `HERMES_CRON_*` env vars above are confirmed
  from the environment-variables reference; `cron/jobs.py` full field schema is not.
- All command names are verbatim from the reference docs; model/provider examples
  (gpt-5-mini, claude-sonnet-4.6) are doc examples, not recommendations.