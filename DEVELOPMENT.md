# FrameNest Development Launcher

## Status

This is the current browser-development workflow guide. It documents the local
launcher that runs the pre-alpha web application in an external browser.
Optional `FRAMENEST_LOCAL_OWNER_LOGIN` names a login that is already present
in the identity map. Loopback TCP and the local operator channel can use that
mapped role. It does not create an administrator by itself, and it does not
apply to the public published composition.

Classification: development operator guide.

Consumers: Cooperator, Orchestrator, Worker, and local FrameNest developers.

Retention: remains while the browser-development launcher exists.

Inbound links: [README.md](README.md),
[docs/WORKER_EXECUTION_CONTRACT.md](docs/WORKER_EXECUTION_CONTRACT.md),
[AGENTS.md](AGENTS.md).

Cleanup/update owner: future explicitly authorized Worker under an Orchestrator
task. Git history remains the archive.

Worker runtime, Poetry/`.venv` authority, exact-source worktree testing, and
test invocation rules live in
[docs/WORKER_EXECUTION_CONTRACT.md](docs/WORKER_EXECUTION_CONTRACT.md). This
launcher guide does not replace that contract.

## Kronika Transition Boundary

[ADR-0082](docs/adr/0082-kronika-one-product-and-private-records.md) and
[ADR-0083](docs/adr/0083-modular-research-providers-and-administrator-curated-timeline.md)
accept one Kronika in this repository. The commands below still describe the
existing FrameNest development launcher. They do not start a research provider,
start the parked capture service, or prove browser or provider readiness.

The capture module is `src/kronika_capture`, command `kronika-capture`, and
stays parked. ADR-0085 is the sole-identity authority; the ordered identity cuts
implement it. The application and capture use separate processes: a web
restart must not close the persistent capture browser. Do not port the source
manager, local accounts or library.

Research configuration is disabled by default. An absent research section
means disabled and must not block ordinary startup. Tests of the research
boundary use a fake provider. They must not call the live OpenAI API and must
not require a real key. The selected live provider, fixed model
`gpt-5.5-2026-04-23`, stays unimplemented until S4-A and S4-B and stays
unactivated until a separate grant.

Declared test commands:

```text
./.ap/ap project check --root <physical-repository-root> --baseline <full-commit-id>
./.ap/ap exec --root <physical-repository-root> --baseline <full-commit-id> --operation test-focus -- <pytest-args>
node --test tests/<name>.test.js
```

Worker Python evidence uses that AP route, as recorded in
[docs/WORKER_EXECUTION_CONTRACT.md](docs/WORKER_EXECUTION_CONTRACT.md). The
local `poetry run pytest` command later in this guide is the operator launcher
workflow, not a second Worker route. JavaScript evidence uses `node --test`.

NUC releases continue through `deploy/ubuntu/framenest-release`. Do not add
another deployment system. Host tooling, services, ports, browser readiness
and research-credential provisioning require separately authorized preflight
before host changes. Login and profile operations belong to the Cooperator.
S9 resets only the exact unwanted test databases after stopping writers.
Launcher commands are not reset authority. See [ROADMAP.md](ROADMAP.md) and
the [NUC runbook](docs/UBUNTU_NUC_DEPLOYMENT.md).

## First Run

From the repository root:

```text
git submodule update --init --recursive
./.ap/ap doctor
./framenest setup
./framenest start
```

The AP doctor validates the pinned canonical Analytic Programming submodule and
the managed block in [AGENTS.md](AGENTS.md). It does not replace product tests.

`setup` locates the uv-managed CPython `3.13.14`, installs it with `uv` if it is
missing, configures Poetry to use that interpreter with `.venv/`, and runs
`poetry install --no-interaction`. Re-run `./framenest setup` after committed
dependency or lockfile changes. `uv` here is only the interpreter provider;
Poetry remains the dependency and lockfile authority. Do not adopt an untracked
`uv.lock`. Ordinary Workers must not casually recreate, move, symlink, or
replace the canonical project `.venv` — see
[docs/WORKER_EXECUTION_CONTRACT.md](docs/WORKER_EXECUTION_CONTRACT.md).

`start` automatically performs the same setup flow only when the expected
project environment or installed controller is missing.

## Commands

```text
./framenest setup              Prepare the Poetry environment.
./framenest start              Start, migrate, wait for health, and open a browser.
./framenest start --no-open    Start without opening a browser.
./framenest stop               Stop only the verified launcher-owned server.
./framenest restart            Verified stop, then start.
./framenest restart --no-open  Restart without opening a browser.
./framenest status             Show managed process, database, and log state.
./framenest open               Open the healthy managed server.
./framenest logs               Show recent server log lines.
./framenest logs --follow      Follow the development server log.
./framenest --help             Show wrapper help.
./framenest <command> --help   Show command help.
```

The launcher does not implement client start/stop commands. The current client
is an external browser tab, and FrameNest does not close browser processes.

## Runtime Locations

On ordinary macOS development hosts, launcher-owned data is outside the Git
worktree:

```text
~/Library/Application Support/FrameNest/development/catalog.sqlite3
~/Library/Application Support/FrameNest/development/runtime/
~/Library/Logs/FrameNest/development/server.log
```

The runtime state directory stores the managed process state and operation lock.
The log file contains the managed child process output used by `./framenest
logs`.

The launcher honors an existing absolute `FRAMENEST_DATABASE_PATH`. Tests and
manual disposable runs may also use:

```text
FRAMENEST_DEVELOPMENT_RUNTIME_DIR=/absolute/path/to/runtime
FRAMENEST_DEVELOPMENT_LOG_DIR=/absolute/path/to/log-directory
FRAMENEST_PORT=8123
```

Overrides must be absolute paths. They are launcher/runtime inputs only and are
not web API response data.

## Identity-Path Migration

`kronika-dev migrate-identity-paths check|apply` resolves the owned local
development and AI state locations beside their canonical destinations.
`check` is read-only for state: it reports sanitized counts and records the
source/destination mapping in a private receipt outside the repository. `apply`
copies consistent databases and durable files only to absent destinations,
never overwrites a conflicting destination, refuses to revive a
managed-process liveness record and leaves every explicitly overridden path
unchanged. Both operations refuse to run while the managed development server
is running.

The runtime default locations themselves do not change: until the later
identity switch, launcher-managed state continues at its current locations and
the migration copies it to the canonical destinations. The private migration
receipts are the evidence required before that switch.

## Local `.env` And Explicit Environment Files

FrameNest commands never read a `.env` file from the caller's current working
directory. An environment file is applied only when explicitly requested
through the `FRAMENEST_ENV_FILE` environment variable or the explicit
`load_settings(env_file=...)` parameter. A missing or unreadable explicit
file fails closed with a sanitized error; process environment variables keep
the highest precedence.

For launcher-driven development commands (`ai`, `backup`, `library`,
`previews`, `youtube`), the launcher deterministically exposes a regular
non-symlink repository-root `.env` as `FRAMENEST_ENV_FILE` when one exists
and the operator has not already set the variable. An ignored local `.env`
anywhere else is never discovered implicitly. Direct `poetry run` usage can
opt in explicitly:

```text
FRAMENEST_ENV_FILE=.env poetry run framenest-db status
```

The managed development server (`./framenest start`, `framenest-dev`) resolves
its own explicit settings and never reads an environment file.
`framenest-production` likewise reads only the process environment.

## Start And Migration

`./framenest start` enforces loopback host `127.0.0.1`, resolves the development
database, migrates it to the packaged Alembic head, starts a detached server
using the current Poetry environment's Python, writes managed state atomically,
waits for `GET /health` to return `{"status": "ok"}`, and opens the default
browser unless `--no-open` was supplied.

The raw server command remains a lower-level foreground boundary:

```text
poetry run framenest-server
```

That command does not migrate automatically, does not manage background state,
and is stopped directly from its terminal.

## Status And Exit Behavior

`status` reports one of:

```text
running
stopped
stale
unhealthy
conflict
```

Healthy running status exits with code `0`. Stopped or stale status exits with
code `3`, unhealthy with `4`, and conflict with `5`. Usage errors exit with
code `2`.

`conflict` means the launcher cannot safely prove ownership, or the selected
port is occupied by an unmanaged process. The launcher refuses to adopt,
replace, or kill that process.

## Logs

Use:

```text
./framenest logs
./framenest logs --follow
```

The command reads only the FrameNest development log. It does not classify
wrapper, Poetry, or shell diagnostics as FrameNest structured application
records, and it does not delete or rotate logs.

## Recovery

If status is `stale`, the recorded launcher-owned process no longer exists.
`start` and `stop` can clear stale launcher state safely.

If status is `conflict`, inspect the message and stop the unrelated process
through the tool that started it, or choose a different `FRAMENEST_PORT`. The
launcher does not use broad process search, `pkill`, `killall`, port-based
killing, or automatic force-kill recovery.

## Tests

Python:

```text
poetry run pytest
```

Isolated worktree exact-source Python evidence uses the canonical interpreter
plus `PYTHONPATH=<exact-worktree>/src` as documented in
[docs/WORKER_EXECUTION_CONTRACT.md](docs/WORKER_EXECUTION_CONTRACT.md).

JavaScript (`node:test` suites under `tests/*.test.js`; no npm test script):

```text
node --test tests/<name>.test.js
```

Opt-in repository browser evidence (system Chrome/CDP, not Playwright authority):

```text
FRAMENEST_RUN_BROWSER_EVIDENCE=1 node --test tests/browser_<name>_evidence.test.js
```

## Manual No-Browser Run

For disposable validation:

```text
FRAMENEST_DATABASE_PATH=/tmp/framenest-dev/catalog.sqlite3 \
FRAMENEST_DEVELOPMENT_RUNTIME_DIR=/tmp/framenest-dev/runtime \
FRAMENEST_DEVELOPMENT_LOG_DIR=/tmp/framenest-dev/logs \
FRAMENEST_PORT=8123 \
./framenest start --no-open
```

Use `./framenest stop` with the same environment values to stop that managed
server. To completely stop FrameNest browser-development mode, stop the managed
server and close any external browser tabs manually.
