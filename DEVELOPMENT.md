# Kronika Development Launcher

## Status

This is the current browser-development workflow guide. It documents the local
launcher that runs the pre-alpha web application in an external browser.
Optional `KRONIKA_LOCAL_OWNER_LOGIN` names a login that is already present
in the identity map. Loopback TCP and the local operator channel can use that
mapped role. It does not create an administrator by itself, and it does not
apply to the public published composition.

Classification: development operator guide.

Consumers: Cooperator, Orchestrator, Worker, and local Kronika developers.

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
existing Kronika development launcher. They do not start a research provider,
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

NUC releases continue through `deploy/ubuntu/kronika-release`. Do not add
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
./kronika setup
./kronika start
```

The AP doctor validates the pinned canonical Analytic Programming submodule and
the managed block in [AGENTS.md](AGENTS.md). It does not replace product tests.

`setup` locates the uv-managed CPython `3.13.14`, installs it with `uv` if it is
missing, configures Poetry to use that interpreter with `.venv/`, and runs
`poetry install --no-interaction`. Re-run `./kronika setup` after committed
dependency or lockfile changes. `uv` here is only the interpreter provider;
Poetry remains the dependency and lockfile authority. Do not adopt an untracked
`uv.lock`. Ordinary Workers must not casually recreate, move, symlink, or
replace the canonical project `.venv` — see
[docs/WORKER_EXECUTION_CONTRACT.md](docs/WORKER_EXECUTION_CONTRACT.md).

`start` automatically performs the same setup flow only when the expected
project environment or installed controller is missing.

## Commands

```text
./kronika setup              Prepare the Poetry environment.
./kronika start              Start, migrate, wait for health, and open a browser.
./kronika start --no-open    Start without opening a browser.
./kronika stop               Stop only the verified launcher-owned server.
./kronika restart            Verified stop, then start.
./kronika restart --no-open  Restart without opening a browser.
./kronika status             Show managed process, database, and log state.
./kronika open               Open the healthy managed server.
./kronika logs               Show recent server log lines.
./kronika logs --follow      Follow the development server log.
./kronika --help             Show wrapper help.
./kronika <command> --help   Show command help.
```

The launcher does not implement client start/stop commands. The current client
is an external browser tab, and Kronika does not close browser processes.

## Runtime Locations

On ordinary macOS development hosts, launcher-owned data is outside the Git
worktree:

```text
~/Library/Application Support/Kronika/development/catalog.sqlite3
~/Library/Application Support/Kronika/development/runtime/
~/Library/Logs/Kronika/development/server.log
```

The runtime state directory stores the managed process state and operation lock.
The log file contains the managed child process output used by `./kronika
logs`.

The launcher honors an existing absolute `KRONIKA_DATABASE_PATH`. Tests and
manual disposable runs may also use:

```text
KRONIKA_DEVELOPMENT_RUNTIME_DIR=/absolute/path/to/runtime
KRONIKA_DEVELOPMENT_LOG_DIR=/absolute/path/to/log-directory
KRONIKA_PORT=8123
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

Launcher defaults are already `~/Library/Application Support/Kronika` on macOS
and `$XDG_DATA_HOME/Kronika` elsewhere. `kronika-dev migrate-identity-paths`
remains the workstation copy tool for a leftover legacy tree, with the
refusal safeguard in front of creating an empty catalog.

## Local `.env` And Explicit Environment Files

Kronika commands never read a `.env` file from the caller's current working
directory. An environment file is applied only when explicitly requested
through the `KRONIKA_ENV_FILE` environment variable or the explicit
`load_settings(env_file=...)` parameter. A missing or unreadable explicit
file fails closed with a sanitized error; process environment variables keep
the highest precedence.

For launcher-driven development commands (`ai`, `backup`, `library`,
`previews`, `youtube`), the launcher deterministically exposes a regular
non-symlink repository-root `.env` as `KRONIKA_ENV_FILE` when one exists
and the operator has not already set the variable. An ignored local `.env`
anywhere else is never discovered implicitly. Direct `poetry run` usage can
opt in explicitly:

```text
KRONIKA_ENV_FILE=.env poetry run kronika-db status
```

The managed development server (`./kronika start`, `kronika-dev`) resolves
its own explicit settings and never reads an environment file.
`kronika-production` likewise reads only the process environment.

## Start And Migration

`./kronika start` enforces loopback host `127.0.0.1`, resolves the development
database, migrates it to the packaged Alembic head, starts a detached server
using the current Poetry environment's Python, writes managed state atomically,
waits for `GET /health` to return `{"status": "ok"}`, and opens the default
browser unless `--no-open` was supplied.

The raw server command remains a lower-level foreground boundary:

```text
poetry run kronika-server
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
./kronika logs
./kronika logs --follow
```

The command reads only the Kronika development log. It does not classify
wrapper, Poetry, or shell diagnostics as Kronika structured application
records, and it does not delete or rotate logs.

## Recovery

If status is `stale`, the recorded launcher-owned process no longer exists.
`start` and `stop` can clear stale launcher state safely.

If status is `conflict`, inspect the message and stop the unrelated process
through the tool that started it, or choose a different `KRONIKA_PORT`. The
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
KRONIKA_RUN_BROWSER_EVIDENCE=1 node --test tests/browser_<name>_evidence.test.js
```

## Companion And Gallery Preview Backfill

Load the unpacked companion by following
[docs/X_COMPANION.md](docs/X_COMPANION.md).

`KRONIKA_GALLERY_PREVIEW_BACKFILL_ENABLED` defaults to true. When it is true,
the server fills missing gallery previews in-process. Set it to `false` to
disable that coordinator. A gallery-preview GET never generates a preview.

Timeline media rows reuse ready Gallery previews, with the kind icon retained while loading and after every failure. Question rows appear on the Timeline and make no media-preview requests. There is no separate History view. [SPEC.md](SPEC.md) owns these presentation requirements. The focused validation route is:

```text
# [MacBook / fish]
./.ap/ap project check --root /Users/agile/Projects/kronika --baseline <exact-authorized-baseline>
./.ap/ap exec --root /Users/agile/Projects/kronika --baseline <exact-authorized-baseline> --operation test-focus -- tests/integration/persistence/test_kronika_record_repository.py tests/contract/test_records_api.py tests/contract/test_gallery_preview_api.py tests/contract/test_local_web_application.py
node --test tests/kronika_ui.test.js tests/gallery_search_tag_filters.test.js
#------------------------------------------------------
```

Use only synthetic records, temporary catalogs and harmless fixture bytes for
manual checks. A video or animated-image card with an authorized ready
derivative should reveal the JPEG; still images, non-media records, question
rows and every missing, unavailable, denied or failed derivative should keep
the kind icon with no broken-image chrome and no automatic retry. Rendered
acceptance requires the exact accepted commit to be published on public `main`
and refreshed onto the NUC first; do not request visual acceptance against a
local or unpublished checkout.

[docs/UX_AUDIT.md](docs/UX_AUDIT.md) is the historical record of one UX round.
It is not task authority.

## Upload Flow

Normative upload behavior, including automatic start after a permitted selection or drop, explicit Resume, and truthful progress, is [SPEC.md](SPEC.md) §18. Companion `open_upload` only opens that hosted dialog; progress and results stay inside it.

Focused checks for this slice:

```text
# [MacBook / fish]
./.ap/ap project check --root /Users/agile/Projects/kronika --baseline <exact-authorized-baseline>
./.ap/ap exec --root /Users/agile/Projects/kronika --baseline <exact-authorized-baseline> --operation test-focus -- tests/contract/test_local_web_application.py tests/contract/test_upload_api.py tests/contract/test_ordinary_upload_ownership_boundary.py tests/contract/test_upload_session_architecture.py tests/contract/test_atomic_upload_publication_contract.py tests/integration/test_local_web_upload_cockpit.py tests/integration/test_atomic_upload_publication.py -q -p no:cacheprovider
node --test tests/upload_cockpit_async_ownership.test.js tests/companion_web_bridge.test.js tests/x_companion_extension.test.js tests/youtube_acquisition_cockpit.test.js tests/x_acquisition_cockpit.test.js tests/kronika_ui.test.js tests/gallery_search_tag_filters.test.js tests/automatic_analysis_lifecycle.test.js tests/cover_frontend.test.js
#------------------------------------------------------
```

Synthetic-fixture checklist:

- Use harmless fixture bytes, temporary catalogs, and synthetic identities.
- Exercise an empty file, a file over the server limit, capability failure, and one normal GIF, MP4, JPEG, or PNG selection.
- Confirm selecting or dropping a file starts automatically, no Start control exists, a failed gate allows another selection, and automatic start does not resume a recovered upload.
- Confirm an in-flight or recovering upload rejects a replacement file, administrator duplicate review still waits for keep or discard, and an ordinary completion shows review wording without duplicate evidence.
- Confirm a full server offset can Resume through completion without sending another chunk, and a partial recovery still waits for explicit Resume.
- Do not use provider credentials, private media, or a live automatic-analysis call.

Rendered geometry, the native file picker, scrolling, clipping, focus, contrast, and actual upload timing are not established by these commands. They require the accepted commit on public `main`, refreshed onto the NUC, before Cooperator acceptance.

## Manual No-Browser Run

For disposable validation:

```text
KRONIKA_DATABASE_PATH=/tmp/kronika-dev/catalog.sqlite3 \
KRONIKA_DEVELOPMENT_RUNTIME_DIR=/tmp/kronika-dev/runtime \
KRONIKA_DEVELOPMENT_LOG_DIR=/tmp/kronika-dev/logs \
KRONIKA_PORT=8123 \
./kronika start --no-open
```

Use `./kronika stop` with the same environment values to stop that managed
server. To completely stop Kronika browser-development mode, stop the managed
server and close any external browser tabs manually.
