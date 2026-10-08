# FrameNest Ubuntu NUC Deployment Runbook

## Status

This is the current repository-native operator runbook for preparing and
operating FrameNest on the Intel NUC6i5SYH running Ubuntu Server 24.04 LTS as a
development-and-testing machine.

It is not a transcript of every historical host command and does not by itself
grant non-routine host-mutation authority. The NUC is routinely refreshed to
the exact public `main` SHA; the authoritative runtime readback is the
authenticated command `framenest-release status` (see the Routine Immutable
Release Update section below), never a committed SHA snapshot. A production release was
previously accepted at public/canonical commit
`aec2f0091c10aed2fc2033dac154a0d9651b2b6d` (schema `0028`) served from
`/opt/framenest/releases/aec2f0091c10aed2fc2033dac154a0d9651b2b6d` with
Tailscale Serve only; that fact is dated history, not a current guarantee.
Execute non-routine host mutations only under an authorized operator task.

Role note (2026-08-26): the Cooperator redefined the NUC as FrameNest's
development-and-testing machine
([ADR-0075](adr/0075-nuc-development-test-target-and-routine-release-refresh.md)).
Routinely refreshing it to the exact public `main` SHA — including schema jumps
through the documented `migration-required` continuation in section 5 — is
normal operation through `deploy/ubuntu/framenest-release` under standing
refresh authority. Non-routine host work still requires its own explicit
bounded task. Production-server phrasing elsewhere in this runbook is legacy
framing pending a dedicated editorial refactor; operational commands other
than the section 5 schema-jump annex are unchanged.

The public-reader direction in
[ADR-0074](adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md)
is retained as history. ADR-0082 keeps public composition off for the Kronika
transition; no new records may be exposed through it. A public listener or TLS
rollout is outside this stage and outside routine release updates.

Classification: deployment operator runbook.

Consumers: Cooperator, Orchestrator, Worker, Ubuntu operators, and security
reviewers.

Retention: remains while Ubuntu NUC deployment is the current server workflow.

Inbound links: [ADR-0032](adr/0032-ubuntu-nuc-deployment-foundation.md),
[NUC_HOST_BASELINE.md](NUC_HOST_BASELINE.md), [SERVER.md](../SERVER.md),
[SECURITY.md](../SECURITY.md), [ROADMAP.md](../ROADMAP.md), and
[OPERATOR_NETWORK.md](OPERATOR_NETWORK.md).

Cleanup/update owner: future explicitly authorized Worker under an Orchestrator
task. Git history remains the archive.

## Accepted Kronika Transition (Not Yet Deployed)

[ADR-0082](adr/0082-kronika-one-product-and-private-records.md) accepts one
product here, with the existing FrameNest application and a separate parked
capture runtime.
[ADR-0083](adr/0083-modular-research-providers-and-administrator-curated-timeline.md)
adds the research supervisor to that same application. This runbook change
does not install either capability. The existing operational commands below
remain unchanged and do not establish that capture services or a research
credential are installed.

The sole release route remains `deploy/ubuntu/framenest-release`. Do not add
a second deployment system. The S3 host remainder is parked. Planned capture
uses account `kronika-capture`, private state under `/var/lib/kronika-capture`,
one persistent Chromium on permanent Xvfb and a dedicated profile. A normal web
release must not restart that browser. A capture runtime update receives one
planned restart after draining work and enforcing the five-minute start brake.
Bridge outages reconnect; browser crashes pause instead of restarting in a
loop. These are target requirements, not observed host facts.

Before host mutation, a separate read-only preflight must verify exact release
provenance, active services/writers, installed Node/Chromium/display tooling,
sandbox/AppArmor readiness, paths, permissions and loopback port availability.
Historical host notes are not proof of current readiness. Login and challenge
intervention use a temporary loopback-only VNC/noVNC view over SSH operated by
the Cooperator. Only he backs up/restores the opaque profile, with Chromium
stopped. Agents do not inspect credentials or profile contents.

S9 requires independent integrated acceptance before joint deployment, followed
by a separately authorized exact-object reset. Preflight identifies both old
application databases and their WAL/SHM files; all writers stop before deletion.
No whole state directory, media, profile, identity configuration, secret or
archive is removed. No old database is imported. Normal migrations create the
empty catalog; migration history and the helper's explicit
`migration-required` continuation remain. Rollback uses previous code and a
compatible empty database, not deleted test data.

Cooperator rendered acceptance follows publication to exact public `main` and
refresh of that candidate on NUC. S10 is complete: the public repository is
`cisarik/kronika`, and the former capture repository remains
`cisarik/cli_chatgpt` without an archive. Local host paths and deployment
identifiers stay unchanged. See
[ROADMAP.md](../ROADMAP.md) for the active order and the parked capture rows.
None of this authorizes host work now.

## Future Research Credential and Native Provider (Not Provisioned)

ADR-0083 selects the OpenAI Responses API, model `gpt-5.5-2026-04-23`, with
native provider-managed research supervised by this application. Nothing in
this runbook provisions that provider, stores a key, sets a billing limit or
places a live call. Research stays disabled until a later repository slice
and a separate activation grant. A disabled or unconfigured research provider
must not prevent ordinary `framenest.service` startup. `framenest-release`
continues to move only the web release, as it does today.

Future provisioning, not authorized here, uses one dedicated project-scoped
credential identified as `KRONIKA_RESEARCH_OPENAI_API_KEY`. The non-secret
configuration stores that identifier only. The intended production shape is a
root-owned mode `0600` file
`/etc/framenest/credentials/research-openai`, mapped with:

```text
LoadCredential=KRONIKA_RESEARCH_OPENAI_API_KEY:/etc/framenest/credentials/research-openai
```

The web service would read that named systemd credential through the existing
credential boundary. It must not prefer an ambient API key. No key belongs in
Git, unit arguments, logs or the frontend. The repository source for that
optional mapping is `deploy/systemd/framenest-research-credential.conf`;
install it only during an explicitly authorized provisioned deployment.

Before any native provider call, a separate grant must show all of the
following:

- the repository research slice is accepted and still disabled by default;
- the Cooperator has provisioned the dedicated credential;
- the provider account has a monthly hard limit of USD 30;
- application thresholds remain Search USD 0.50, Research USD 5, daily USD 10
  and monthly USD 30, with the accepted possibility of delayed enforcement;
- the call itself is an explicit bounded live test, not a side effect of
  deploy, rollback or ordinary startup.

Capture remains parked. The next section records its repository sources and
does not record a completed capture deployment.

## Capture Runtime Sources (Not Deployed)

The capture units and the release-helper extension below are repository
sources. They do not record a completed capture deployment, a created account,
or a passed host preflight. A later read-only preflight and a separate host
grant remain required before any of these units are installed.

Planned paths, owned by the dedicated `kronika-capture` account:

```text
Private state:           /var/lib/kronika-capture
Profile:                 /var/lib/kronika-capture/profile
Journal:                 /var/lib/kronika-capture/capture-journal.sqlite3
Staging:                 /var/lib/kronika-capture/staging
Runtime directory:       /run/kronika-capture
Nonsecret configuration: /etc/kronika-capture/capture.env
Capture release pointer: /opt/framenest/capture-current
Web release pointer:     /opt/framenest/current
```

Source units:

```text
deploy/systemd/kronika-capture-xvfb.service
deploy/systemd/kronika-capture-bridge.service
deploy/systemd/kronika-capture-runner.service
deploy/systemd/kronika-capture-vnc.service
deploy/systemd/kronika-capture-view.service
deploy/systemd/kronika-capture.env.example
```

Xvfb and the runner set `Restart=no`. The bridge may restart on failure; the
runner reconnects and is not stopped by a bridge restart. No capture unit is
`PartOf=framenest.service`. Xvfb and the runner share display `:99`, the socket
under `/tmp/.X11-unix`, and `/run/kronika-capture/Xauthority`. They do not use
a private `/tmp`, and Xvfb does not disable access control. Xvfb writes its
display lock under `/tmp`, so the Xvfb unit keeps `/tmp` writable while the
rest of the filesystem stays read-only. Chromium's Linux process singleton
creates its socket directory under the temporary directory, so the runner sets
`TMPDIR=/run/kronika-capture/tmp` inside the runtime directory it can already
write. `ExecStartPre` creates that directory at mode `0700`. General `/tmp`
write access for the runner and `PrivateTmp` remain prohibited. The committed
environment template does not set `TMPDIR`, so a host `capture.env` that
matches it does not override the unit. The cookie is generated when Xvfb starts.
Browser debugging stays on loopback inside the runner. The launcher reads `KRONIKA_CHROMIUM_PATH` from the non-secret env
file and requires an absolute executable. It does not search `PATH`, enable
stealth, or weaken the sandbox.

The runner logs one `capture_startup` JSON outcome per startup attempt. Its
public failure code remains `E_BROWSER_UNAVAILABLE`. The internal `stage`
identifies `executable_preflight`, `profile_preflight`, `launch_lock`,
`brake_metadata`, `spawn`, `endpoint`, `cdp_connection`, `page_opening`, or
`navigation`; successful startup reports `complete`. The `reason` distinguishes
an occupied lock (`locked`), an unexpired five-minute interval
(`interval_unexpired`), and unverifiable brake metadata
(`metadata_unverifiable`). Other reasons describe the failed operation rather
than guessing its cause.

Diagnostics contain only fixed enums, booleans, a bounded spawn errno, and an
exit code from 0 through 255 when available. They preserve the child's exit
code and allowlisted signal before cleanup, plus `endpoint_seen` and
`endpoint_budget_exhausted`. The existing stderr budget is 65,536 bytes and
4,096 characters per line. Recognized failure wording produces only one of
`sandbox_namespace`, `display_authentication`, `temporary_storage_read_only`,
or `profile_in_use`; unknown messages and unrelated warnings remain
`unclassified`. Input text is discarded. Raw exceptions, stderr, URLs, argv,
environment values, profile paths, DOM and credentials are never included in
the startup record. For example:

```text
capture_startup {"outcome":"failed","code":"E_BROWSER_UNAVAILABLE","stage":"endpoint","reason":"process_exited","spawn_errno":null,"exit_code":1,"signal":null,"endpoint_seen":false,"endpoint_budget_exhausted":false,"stderr_classification":"unclassified","cleanup_failed":false}
```

A cleanup failure emits a separate `capture_cleanup` record and preserves the
first startup failure. Unconfirmed termination retains the launch lock. A
failed startup remains in the unavailable service loop without another launch
attempt; confirmed termination releases the lock but does not reset the brake.
These classifications narrow a later host diagnostic; synthetic tests alone
do not establish whether Chromium can start on the host.

The per-install bridge token is not written in `capture.env`, unit arguments,
or logs. Bridge and runner units load systemd credential `token` from the
root-owned file `/etc/kronika-capture/credentials/kronika-bridge-token`. When
`CREDENTIALS_DIRECTORY` contains that regular file, the bridge and the runner
launcher use it. Otherwise they keep the existing state-directory `token`
file. The web service does not receive this credential from
`deploy/systemd/framenest.service` in this repository slice. A later host
grant can install this drop-in for `framenest.service` without adding the web
account to the capture account or opening the browser profile:

```text
[Service]
LoadCredential=token:/etc/kronika-capture/credentials/kronika-bridge-token
```

State directories are mode `0700`. VNC and noVNC have no install section, so
they stay stopped until an operator starts them. They listen only on
`127.0.0.1` ports `5900` and `6080` and stop after `RuntimeMaxSec=1800` (30
minutes). If either port is already bound, setup stops; the units do not pick
another port. The Cooperator opens the view through an SSH tunnel to
`127.0.0.1:6080`. Agents do not open the view and do not enter credentials.

`framenest-release deploy` and `framenest-release rollback` still move only
`/opt/framenest/current` and `framenest.service`. They report both pointer
SHAs and do not restart capture. `activate-capture` and `rollback-capture`
are the capture operations. Each one checks the installed release SHA, the
capture identity in its manifest, and bridge protocol `1`; drains queued
capture work; refuses a live or paused job; enforces the five-minute browser
start brake; snapshots the previous runner and browser-session identities from
journal service metadata; switches `capture-current`; restarts
`kronika-capture-runner.service` exactly once; and checks readiness. Readiness
is accepted only when both valid identities have changed and the runner unit
is active. Stale `ready`, `needs_admin`, and `browser_unavailable` records all
remain `starting`. A failed unit is immediately terminal, as is fresh
`needs_admin` or `browser_unavailable` readiness (exit 16). The bounded
180-second deadline accommodates the existing 90-second connection fence and
reconnect backoff; no fresh readiness by that deadline returns exit 17. Failure
retains the new capture pointer and does not restart the runner again. An
unverifiable identity snapshot refuses activation before the switch (exit 15).
Capture failure does not restart the web service. A release
referenced by either pointer is retained. Web schema handling is unchanged:
`migration-required` still stops `deploy --yes` at exit 13, and this helper
does not migrate or delete a database.

A reconstructed journal with zero jobs and persisted `browser_unavailable`
can require `needs_admin` / `E_AMBIGUOUS_SEND`. Recovery uses the existing
explicit resume with a null job identity and the current intervention identity.
It remains pending until a ready browser acknowledges the matching resume
identity. Wrong job, intervention, or acknowledgement identities cannot clear
the pause; fresh readiness alone is insufficient. No journal reset, schema
change, or state-directory recreation is part of this recovery.

## Current Target

```text
Intel NUC6i5SYH
Ubuntu Server 24.04 LTS
x86_64
development-and-testing machine
```

The future Ubuntu VPS target is portability scope only. It is not the immediate
deployment target.

## Repository Artifacts

```text
deploy/systemd/framenest.service
deploy/systemd/framenest.env.example
deploy/systemd/framenest-ai-credential-nvidia-nim.conf
deploy/systemd/framenest-ai-credential-vercel-ai-gateway.conf
deploy/ubuntu/fn-production-env-deploy
deploy/ubuntu/framenest-release
deploy/ubuntu/framenest_release.py
deploy/ubuntu/README.md
docs/adr/0032-ubuntu-nuc-deployment-foundation.md
docs/adr/0060-repeatable-immutable-nuc-release-update-contract.md
docs/adr/0033-catalog-backup-and-recovery-foundation.md
docs/adr/0036-production-ai-credentials-via-systemd.md
docs/NUC_HOST_BASELINE.md
docs/BACKUP_AND_RECOVERY.md
```

The service artifacts are source material. Committing them does not install,
enable, start, stop, reload, or inspect a real service.

[NUC_HOST_BASELINE.md](NUC_HOST_BASELINE.md) records accepted sanitized
host hardening and media-storage baseline facts. It does not grant mutation
authority and is historical host evidence, not a substitute for current release
acceptance.

## Stable Service Contract

```text
service user: framenest
service group: framenest
release root: /opt/framenest/current
production executable: /opt/framenest/current/.venv/bin/framenest-production
operator environment: /etc/framenest/framenest.env
database: /var/lib/framenest/catalog.sqlite3
non-secret AI configuration: /var/lib/framenest/ai/config.json
durable cover storage: /var/lib/framenest/covers
cover thumbnails: /var/cache/framenest/cover-thumbnails
YouTube acquisition staging: /var/lib/framenest/youtube-acquisition
Gallery preview cache: /var/cache/framenest/gallery-previews
runtime root: /run/framenest
original media root: /srv/media
```

Production operations:

```text
framenest-production check-database-ready
framenest-production serve
```

Release-local operator console entry points:

```text
/opt/framenest/current/.venv/bin/framenest-db
/opt/framenest/current/.venv/bin/framenest-youtube
/opt/framenest/current/.venv/bin/framenest-ai
/opt/framenest/current/.venv/bin/framenest-backup
/opt/framenest/current/.venv/bin/framenest-previews
```

Automated catalog backup assets (ADR-0052):

```text
deploy/systemd/framenest-catalog-backup.service
deploy/systemd/framenest-catalog-backup.timer
```

Optional off-device catalog copy assets (ADR-0056):

```text
deploy/systemd/framenest-catalog-offdevice.service
deploy/systemd/framenest-catalog-offdevice.timer
```

These off-device units remain repository source material until a separately
authorized host task provisions `/mnt/framenest-catalog-offdevice`, sets the
non-secret destination ID, and accepts the physical failure domain. Installing
or enabling them is not part of ordinary repository implementation.

Operator-workstation pull assets (ADR-0057):

```text
deploy/ubuntu/framenest-catalog-export-v1
```

Console surfaces after the feature release is deployed:

```text
/opt/framenest/current/.venv/bin/framenest-backup export-latest
/opt/framenest/current/.venv/bin/framenest-recovery
```

The export launcher and exact no-argument sudoers bridge are later host
provisioning only. Current production may remain on an older SHA until an
authorized immutable deployment publishes this capability. Repository presence
alone does not enable real workstation pulls.

Install and enable the local backup timer only under an authorized deployment
task after the feature release is active:

```text
# [NUC / bash]
sudo install -m 0644 deploy/systemd/framenest-catalog-backup.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/framenest-catalog-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now framenest-catalog-backup.timer
systemctl list-timers framenest-catalog-backup.timer
sudo -u framenest --chdir=/opt/framenest/current \
  env FRAMENEST_ENV_FILE=/etc/framenest/framenest.env \
  /opt/framenest/current/.venv/bin/framenest-backup status
#------------------------------------------------------
```

Manual oneshot trigger:

```text
# [NUC / bash]
sudo systemctl start framenest-catalog-backup.service
sudo systemctl status framenest-catalog-backup.service --no-pager
#------------------------------------------------------
```

Rollback to a release that lacks `run-scheduled` must disable and stop the timer
before switching `current`, then remove or leave the units disabled:

```text
# [NUC / bash]
sudo systemctl disable --now framenest-catalog-backup.timer
sudo systemctl disable --now framenest-catalog-backup.service
sudo systemctl daemon-reload
#------------------------------------------------------
```

The daily local pipeline creates an `auto-` catalog bundle, verifies it,
restores it to a disposable destination, records restore-readiness, and expires
only eligible automatic bundles. It does not back up original media bytes.
Defaults and operator commands are documented in
[BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md). The optional ADR-0056
off-device timer copies that verified recovery point to a distinct mount and
restore-verifies it; repository presence alone is not proof of host-loss
survival. The preferred current off-host layer is ADR-0057 operator-workstation
pull, which remains repository capability until later E3 launcher/sudoers/store
provisioning and the first accepted real pull/verify.

The service must remain loopback-first, foreground under systemd, journal
captured, explicit-migration only, and protected by the read-only database
readiness gate.

## Operator Command Execution Contract

Every FrameNest service-account operator command on the NUC must run under an
explicit identity transition that also establishes the immutable release root
as the working directory:

```text
sudo -u framenest --chdir=/opt/framenest/current \
  env FRAMENEST_ENV_FILE=/etc/framenest/framenest.env \
  /opt/framenest/current/.venv/bin/<entry-point> <arguments>
```

- Ubuntu Server 24.04 provides `sudo` with `--chdir` support. The
  service-account process always starts inside the immutable release root,
  which that account can traverse. It never inherits the caller's working
  directory.
- Configuration authority is explicit. FrameNest administrative and
  production commands never read a `.env` file from any working directory.
  An environment file is applied only when explicitly requested through
  `FRAMENEST_ENV_FILE`; a missing or unreadable explicit file fails closed
  with a sanitized error, and process environment variables keep the highest
  precedence.
- Never run service-account commands from a user home directory. Never solve
  a working-directory or permission failure by broadening access to a user
  home or any other unrelated directory, changing its ownership, or adding
  the service account to a personal group.
- The repository-root `./framenest` launcher is CachyOS Fish development
  tooling. It is not installed on the NUC and must not be used there. The
  release-local console entry points above are the only NUC operator
  interface; Fish is not a production prerequisite.
- `framenest-production` is an exception by design: it reads only the
  process environment (supplied by the systemd unit's `EnvironmentFile`)
  and runs readiness and serving through the unit's own
  `WorkingDirectory=/opt/framenest/current`.

### YouTube Operator Ingestion

YouTube manual ingestion uses the release-local `framenest-youtube` console
entry point under the same contract, never the development launcher:

```text
sudo -u framenest --chdir=/opt/framenest/current \
  env FRAMENEST_ENV_FILE=/etc/framenest/framenest.env \
  /opt/framenest/current/.venv/bin/framenest-youtube ingest URL --yes
sudo -u framenest --chdir=/opt/framenest/current \
  env FRAMENEST_ENV_FILE=/etc/framenest/framenest.env \
  /opt/framenest/current/.venv/bin/framenest-youtube status CLAIM_ID
sudo -u framenest --chdir=/opt/framenest/current \
  env FRAMENEST_ENV_FILE=/etc/framenest/framenest.env \
  /opt/framenest/current/.venv/bin/framenest-youtube retry CLAIM_ID --yes
```

The CLI reaches only the loopback server. Without `--yes` it asks for
interactive confirmation on stdin, which requires an interactive operator
session.

### Gallery Preview Operator Generation

Persistent Gallery preview derivatives for GIF and video cards are generated
explicitly through the release-local `framenest-previews` console entry point
under the same contract, never the development launcher and never on demand
from the Gallery preview HTTP endpoint:

```text
sudo -u framenest --chdir=/opt/framenest/current \
  env FRAMENEST_ENV_FILE=/etc/framenest/framenest.env \
  /opt/framenest/current/.venv/bin/framenest-previews status
sudo -u framenest --chdir=/opt/framenest/current \
  env FRAMENEST_ENV_FILE=/etc/framenest/framenest.env \
  /opt/framenest/current/.venv/bin/framenest-previews generate --all --yes
```

`status` is read-only. `generate` prints a plan and requires `--yes` or an
interactive confirmation before writing JPEG derivatives under
`/var/cache/framenest/gallery-previews`.

## Routine Immutable Release Update

The canonical, discoverable, tested routine immutable release-update entry
point is:

```text
deploy/ubuntu/framenest-release
```

It invokes `deploy/ubuntu/framenest_release.py` (standard library only; Ubuntu
system Python 3.12 compatible for its private transferred remote mode). The
architecture decision is [ADR-0060](adr/0060-repeatable-immutable-nuc-release-update-contract.md).

Public commands:

```text
framenest-release status [transport arguments]
framenest-release check --release <40-hex-SHA> [transport arguments]
framenest-release deploy --release <40-hex-SHA> --yes [transport arguments]
framenest-release rollback --release <40-hex-SHA> --yes [transport arguments]
framenest-release activate-capture --release <40-hex-SHA> --yes [transport arguments]
framenest-release rollback-capture --release <40-hex-SHA> --yes [transport arguments]
```

Transport arguments are `--target`, `--user`, and `--identity`, with public-safe
fallbacks `FRAMENEST_NUC_SSH_TARGET`, `FRAMENEST_NUC_SSH_USER`, and
`FRAMENEST_NUC_SSH_IDENTITY`.

### Initial bootstrap versus routine update

Initial host bootstrap provisions the pinned standalone CPython through `uv`,
installs Poetry tooling, creates the service identity, and performs the first
release installation. Those are separate, explicitly authorized maintenance
tasks.

Routine immutable release updates reuse the already accepted tooling exactly:

```text
Poetry:  /opt/framenest/tooling/poetry/2.4.1/.venv/bin/poetry
CPython: /opt/framenest/tooling/python/cpython-3.13.14-linux-x86_64-gnu/bin/python3.13
```

A routine update never invokes `uv`, never requires `uv` on `PATH`, never
installs or downloads tooling automatically, and fails closed with a sanitized
result when the exact tooling is missing or mismatched.

### Modes

- `status` and `check` are read-only with respect to the repository, database,
  service, and host state. They use only fixed, tested commands and create no
  remote state. They must not transfer a helper, refresh sudo, or transition
  into deployment automatically.
- `deploy` re-runs every check gate, then builds and hashes two exact archives
  (superproject and pinned AP), transfers exact bytes, verifies hashes
  remotely, prepares a release-local `.venv` from the committed `poetry.lock`,
  atomically publishes the release, runs a fresh verified catalog checkpoint,
  and performs the atomic cutover and single restart. Pre-cutover
  `framenest-production` readiness uses a oneshot `systemd-run` with the unit
  `EnvironmentFile` because that binary reads only the process environment.
  After restart, deploy and automatic rollback wait up to 30 seconds
  (one-second polling) for active state, database readiness, and health;
  transient `activating` / socket-not-ready / health-not-ready states retry,
  terminal systemd states fail immediately, and deadline expiry is
  `EXIT_READINESS_TIMEOUT`. `--yes` prevents accidental execution but is not
  AP or Cooperator authority.
- `rollback` switches to an already complete release under
  `/opt/framenest/releases/<SHA>`. It never references a
  `/opt/framenest/rollback` path.

### Same-schema boundary and privilege release

This first implementation supports same-schema routine updates only. The
production database revision must equal the packaged target head; any schema
difference stops before cutover with a sanitized `migration-required` result.
The helper never runs `framenest-db migrate` and never hides migration
authority.

Privileged remote phases use `sudo -n` only after the Cooperator has
established the sudo timestamp outside the helper. At terminal handling the
Cooperator invalidates the sudo timestamp through the exact supported route
when the session remains available; if the session is lost first, privilege
release is reported unknown rather than fabricated.

Interrupted, ambiguous, or failed state retains bounded recovery evidence under
`/run/framenest-release-deploy` and provides an exact operator recovery
instruction. A partial target is never deployable; final release publication is
atomic; no wildcard deletion occurs.

## 0. Preconditions And Authority

Read-only checks:

- Verify the operator has a specific authorized deployment task.
- Verify the exact repository commit or release SHA to deploy.
- Verify the catalog backup and restore-to-new-destination foundation in
  [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md) has been exercised for the
  catalog database before important production state is created.
- Verify the service user, release root, environment path, state path, cache
  path, runtime path, and media root are still the accepted paths.
- Verify the NUC is not the only important copy of any media or catalog data.

Stop conditions:

- No exact commit or release is named.
- Backup and restore are not understood.
- The operator is asked to expose FrameNest publicly.
- The operator is asked to edit live code on the NUC.
- The operator is asked to place credentials in committed files or command
  arguments.
- The operator discovers host-specific facts that conflict with ADR-0032.

Evidence:

- Exact commit or release identifier.
- Confirmation that repository `main`, tag, or release evidence is public and
  verifiable.
- Catalog backup bundle verification evidence and restore-drill evidence.

## 1. Check

Read-only checks:

- Confirm the host reports Ubuntu Server 24.04 LTS and `x86_64`.
- Confirm AppArmor status when there is a concrete reason to inspect it.
- Confirm UFW remains enabled when host hardening prerequisites claim it is
  enabled.
- Confirm no public FrameNest listener exists.
- Confirm no router forwarding or public SSH exposure is part of the plan.
- Confirm `/srv/media`, `/srv/media/memes`, `/srv/media/youtube`, and
  `/srv/media/movies` are treated as source-media locations and are not
  service-writable by default.
- Confirm the repository service artifact still binds to `127.0.0.1`.
- If YouTube ingestion is configured, confirm its pre-existing `0700` staging
  root is under `/var/lib/framenest`, is not a symlink, and is disjoint from
  the database, quarantine, preview cache, and every registered media root.
- Confirm any production AI credential plan uses optional systemd
  `LoadCredential=` drop-ins and root-controlled files under
  `/etc/framenest/credentials`, not `framenest.env` or command-line arguments.

Security control: loopback binding.

- Threat: accidental LAN or public exposure of a pre-authentication service.
- Benefit: local-only listener until a later Tailscale and authentication slice.
- Limitation: loopback does not provide remote access by itself.
- Rollback: restore `FRAMENEST_HOST=127.0.0.1` and restart only after
  readiness succeeds.
- Verification: environment file contains `FRAMENEST_HOST=127.0.0.1`; health
  checks use loopback.

Stop conditions:

- The target is not Ubuntu Server 24.04 LTS on x86_64.
- The service would bind to `0.0.0.0`.
- `/srv/media` would be made broadly writable to the service.
- YouTube acquisition would require write access to `/srv/media` or any
  source-media library.

Evidence:

- Sanitized OS and architecture output.
- Sanitized UFW/AppArmor status when checked.
- Sanitized service environment diff.

## 2. Plan

Planned mutations:

- Select exact FrameNest commit or release.
- Select exact `uv` release version and platform artifact.
- Select exact CPython 3.13 patch version, initially `3.13.14`.
- Select exact Poetry version policy already present on the host or prepared by
  the operator.
- Decide whether activation is a first install, restart, or rollback.

Security control: pinned runtime acquisition.

- Threat: supply-chain substitution, unreviewed installer code, or mutable
  runtime drift.
- Benefit: reproducible tool and Python version with checksum and attestation
  evidence.
- Limitation: Astral `python-build-standalone` is the managed Python
  distribution source because Python does not publish official Linux
  distributable binaries.
- Rollback: keep the previous release tree and previous verified runtime until
  the new release passes readiness and health checks.
- Verification: recorded `uv --version`, `python --version`, archive checksum,
  and attestation result when available.

Stop conditions:

- The plan includes `curl | sh`, `wget | sh`, an unreviewed PPA, system Python
  replacement, or global FrameNest package installation.
- The `uv` artifact hash or attestation cannot be verified.

Evidence:

- Planned commit or release SHA.
- Planned `uv` version and artifact name.
- Planned CPython patch version.
- Planned rollback target.

## 3. Prepare Release

Planned reversible mutations:

- Fetch the exact verified commit or release into a new release tree under the
  release root policy.
- Install verified `uv` outside Ubuntu system package ownership.
- Use `uv` to provide CPython 3.13.14 without replacing Ubuntu Python.
- Point Poetry to that interpreter.
- Install the committed lock into the release-local `.venv`.
- Copy the non-secret environment template to `/etc/framenest/framenest.env`
  only if the operator environment does not already exist or the planned change
  explicitly updates it.

Security control: release-local `.venv`.

- Threat: dependency drift, global package contamination, or conflict with
  Ubuntu-managed Python packages.
- Benefit: the active service executes a release-local environment tied to the
  verified commit.
- Limitation: the operator must still maintain `uv`, Poetry, and dependencies.
- Rollback: restore `/opt/framenest/current` to the previous release and use
  its previous `.venv`.
- Verification: `/opt/framenest/current/.venv/bin/framenest-production` exists
  and reports the expected package command behavior.

Stop conditions:

- Poetry wants to update `poetry.lock`.
- `pyproject.toml` and `poetry.lock` are inconsistent.
- The release-local interpreter is not CPython 3.13.
- Any provider key is requested for `framenest.env`.

Evidence:

- Exact release tree path.
- `uv` version.
- Python version from the release-local environment.
- Poetry install result from the committed lock.

## 4. Apply One Bounded Change

Service-affecting mutations must be one bounded change at a time. Examples:

- Install or update the service unit.
- Update the non-secret environment file.
- Switch `/opt/framenest/current` to a prepared release.
- Restart the service after readiness succeeds.

Do not combine unrelated firewall, SSH, storage, Tailscale, authentication,
provider-secret, or backup implementation work with a FrameNest service switch.

Security control: least privilege service identity.

- Threat: application compromise gaining root or broad filesystem authority.
- Benefit: `framenest` service user and group limit routine service authority.
- Limitation: Unix permissions do not replace backups, AppArmor policy, or
  application authentication.
- Rollback: restore previous unit/environment/release and restart after
  readiness succeeds.
- Verification: unit contains `User=framenest` and `Group=framenest`.

Stop conditions:

- A change requires weakening SSH, UFW, AppArmor, or source-media permissions.
- A change requires entering a secret on the command line.
- A change would format, repartition, or remount storage.

Evidence:

- Sanitized before/after diff for the exact changed host artifact.
- Confirmation that no unrelated host control changed.

### Production AI Credential Helper

`deploy/ubuntu/fn-production-env-deploy` is repository-owned source
material for a later explicitly authorized production AI credential task. Its
documented entry point is:

```text
fn-production-env-deploy
```

The helper manages only one selected AI provider credential plus non-secret
provider/model selection. It supports a non-mutating `--check` mode, accepts an
explicit SSH target or non-secret operator environment default, transfers the
credential over SSH stdin rather than argv, uses only `sudo -n` remotely,
atomically acquires `/run/framenest-ai-credential-deploy` before production
mutation, installs deployment-controlled files atomically, and waits up to 30
seconds for bounded readiness. Existing recovery material causes a fail-closed
stop before credential transmission, configuration, restart, health polling, or
rollback. Check mode validates the selected private credential source and the
selected tracked provider-specific drop-in template locally before any SSH
activity.

The helper supports three provider credential identities: `NVIDIA_API_KEY` for
NVIDIA NIM, `AI_GATEWAY_API_KEY` for Vercel AI Gateway, and `OPENCODE_API_KEY`
for operator-declared OpenCode Go records. Each identity uses its exact tracked
two-line `LoadCredential=` drop-in template under `deploy/systemd/`
(`framenest-ai-credential-nvidia-nim.conf`,
`framenest-ai-credential-vercel-ai-gateway.conf`, and
`framenest-ai-credential-opencode-go.conf`). An OpenCode Go provider record is
declared and activated through the authenticated administrator AI providers
surface or the `framenest-ai provider add` CLI before deployment; the helper
installs only the selected credential and the non-secret provider/model
selection.

The systemd credential drop-in source is always the exact tracked template
under `deploy/systemd/` for the selected provider. The helper validates the
template's strict two-line `LoadCredential=` contract locally, transfers the
template bytes as a non-secret stdin payload separate from the credential
payload, installs them to a `.next` path, proves byte equivalence before and
after atomic rename, and never reconstructs line breaks with shell escaping.
After non-secret provider/model configuration is written, the helper verifies
systemd acceptance before restart: `systemd-analyze verify`, daemon reload,
enabled state, loaded drop-in path, exact on-disk drop-in
`LoadCredential=IDENTITY:PATH` mapping via trusted drop-in bytes and
`systemctl cat` (not redacted `systemctl show LoadCredential`), and unchanged
base service unit. Only after those gates pass may it restart
`framenest.service`. After readiness succeeds, it calls only the loopback
`/api/ai/media-suggestion-capability` endpoint and requires the selected
provider/model to be configured, available, and credential-available. A
historical connection-test record must not fail deployment and is not proof
that the newly installed credential is valid; live proof remains an explicit
later `framenest-ai test`.

The helper starts rollback only after a complete backup marker has been written.
That backup records present/absent state for the selected credential, the
systemd credential drop-in, and `/var/lib/framenest/ai/config.json`. Rollback
restores those states, removes pending `.next` artifacts, daemon-reloads,
restarts, and uses the same bounded readiness contract for `framenest.service`.
Deployment terminal service failure and readiness timeout both trigger rollback.
Rollback terminal service failure and rollback readiness timeout are reported as
distinct sanitized outcomes, and recovery material remains under
`/run/framenest-ai-credential-deploy` for operator recovery.

The later operator may create a Fish wrapper or function that invokes the
repository script, but this repository task does not install anything into
`~/.config/fish`.

## 5. Migrate

Service-affecting mutation:

Same-schema routine updates skip this phase: `framenest-release deploy --yes`
continues through checkpoint and cutover. When the packaged Alembic head
differs from the live catalog revision, `deploy --yes` stops with exit 13
(`migration-required`) after atomically publishing
`/opt/framenest/releases/<T>` and before checkpoint or cutover. Do not migrate
from `/opt/framenest/current` for that continuation. Use the annex below.

Security control: explicit migration.

- Threat: surprise schema mutation during service startup or partial startup.
- Benefit: the operator controls backup, timing, and the documented
  schema-jump continuation around schema changes.
- Limitation: migration success does not prove application health.
- Rollback: a post-migration cutover failure requires explicit triage. Never
  improvise a downgrade or catalog restore.
- Verification: the annex's target-tree status, cutover `status`, and
  restore-readiness evidence.

Stop conditions:

- No fresh verified catalog backup exists (`check` requires
  `restore_readiness=ready`).
- The database path is not `/var/lib/framenest/catalog.sqlite3` or another
  explicitly accepted absolute production path.
- Migration reports failure or an unexpected revision.
- Target-tree probes or lock contents do not match the annex.

Evidence:

- Catalog backup verification evidence from `check` / `status`.
- Exit 13 (`migration-required`) from `deploy --yes`.
- Target-tree migration command result and post-migration status.
- Final `framenest-release status` after cutover.

### Shared Release Lock Recovery

Every routine mutating operation (`deploy` and `rollback`) holds one shared
remote release lock before it transfers or mutates anything. The lock is the
remote directory `/run/framenest-release-deploy`; its owner record is the
sibling file `/run/framenest-release-deploy.owner`. The owner record carries
only this run's identity - a nonce, its process id, its start time and the
workstation host name - and no secret.

The lock directory is created with a non-recursive command; there is no `-p`:

```text
sudo -n mkdir -m 0700 /run/framenest-release-deploy
```

The deploy phase can create these exact objects in that directory:
`/run/framenest-release-deploy/ap.tar`,
`/run/framenest-release-deploy/framenest_release.py`, and
`/run/framenest-release-deploy/superproject.tar` before the schema gate, and
`/run/framenest-release-deploy/previous-release` after a successful
checkpoint. `rollback` records
`/run/framenest-release-deploy/rollback-previous-release` in the same
directory. After a completed operation, the engine removes each exact
transferred file, removes the empty lock directory, and then removes the
owner record. An interrupted cleanup can
leave the directory, its contents, or the owner record behind, and a stale
lock can recur; no run may assume the lock is absent.

When the lock directory already exists, the engine reads the owner record and
reclaims the lock only in these cases. A reclaim moves the abandoned directory
aside with one atomic rename to the quarantine path named by the reason,
creates a fresh lock directory, and removes the quarantine path after the
operation:

| Existing lock | Engine decision | Quarantine |
|---|---|---|
| Owner record names this exact run | reclaim, reason `own-identity` | `/run/framenest-release-deploy.reclaimed-own-identity` |
| Record written on this workstation, at least 60 seconds old, recorded process no longer alive | reclaim, reason `abandoned-owner` | `/run/framenest-release-deploy.reclaimed-abandoned-owner` |
| Live owner, foreign host, unreadable or absent owner record, or a failed reclaim rename | refuse; stop the run | none |

Three recovery cases:

1. **Live lock owner.** Precondition: the owner record parses, names this
   workstation, and its recorded process is alive, or the record is younger
   than the reclaim bound. Another run owns the lock. Stop; do not remove,
   move, reclaim or inspect-delete the lock directory, its contents or its
   owner record.
2. **Proven stale or ownerless residue.** Precondition: no live owner is
   proven, and the residue is either a record whose age exceeds the reclaim
   bound with no live process, or an ownerless directory with no readable
   record. Inspect the exact phase, ownership and contents before any
   authorized recovery. An ownerless lock is never reclaimed automatically,
   so it always stops for explicit inspection. Recover only exact named
   objects from the inventory above; never use a wildcard, a recursive delete
   or a parent-directory delete.
3. **`migration-required` (exit 13).** Precondition: a fresh `deploy --yes`
   stopped exactly at the schema gate and left the pre-schema-gate residue of
   `ap.tar`, `framenest_release.py` and `superproject.tar` (no
   `previous-release` yet). Continue with the annex below.

### Annex: Schema-jump continuation after `migration-required` (exit 13)

This annex is the documented continuation when `deploy --yes` stops because
the packaged Alembic head differs from the live catalog revision. It does not
add a fifth public command. The helper remains migration-free. `<T>` is the
exact public `main` SHA already accepted by
`framenest-release check --release <T>`.

The continuation is schema-generic; no revision pair in this document is a
current expectation. Read the pair from the fresh `status` and `check` you ran
before the stop and from the target tree in step 2:

- `<C>` is the observed live catalog revision the running release reports
  (`database_revision` in `status`, and `current_revision` of the deployed
  tree).
- `<H>` is the observed packaged target head the target release reports
  (`head_revision` in step 2).
- Exit 13 guarantees `<C>` and `<H>` differ at the stop.
- Step 4 must end at `current_revision=head_revision=<H>` for that same
  observed `<H>`. Any other pair stops the run.

1. **Stop at exit 13.** `deploy --yes` exits exactly 13 (`migration-required`)
   AFTER atomically publishing `/opt/framenest/releases/<T>` and BEFORE
   checkpoint, cutover, restart, or cleanup. The running service remains on
   the previous release. Exit 0 means the helper completed a same-schema
   update; skip this annex and verify with `status`. Any other nonzero exit
   stops the run.

2. **Verify the published target before cleanup.** Confirm all of the
   following; any mismatch stops the run:

```text
# [NUC / bash]
sudo readlink -n /opt/framenest/current
sudo systemctl is-active framenest.service
sudo cat /opt/framenest/releases/<T>/.framenest-release-sha
sudo test -x /opt/framenest/releases/<T>/.venv/bin/framenest-db && echo executable
sudo ls -1 /run/framenest-release-deploy
sudo -u framenest --chdir=/opt/framenest/releases/<T> \
  env FRAMENEST_ENV_FILE=/etc/framenest/framenest.env \
  /opt/framenest/releases/<T>/.venv/bin/framenest-db status
#------------------------------------------------------
```

Required evidence:

- `/opt/framenest/current` still names the previous release, not
  `/opt/framenest/releases/<T>`; `framenest.service` remains active on that
  old release.
- Target `.framenest-release-sha` equals `<T>`.
- Target `.venv/bin/framenest-db` is executable.
- Target-tree `framenest-db status` shows the recorded live revision
  `current_revision=<C>` and the packaged target head `head_revision=<H>`.
- `/run/framenest-release-deploy` contains only the known pre-schema-gate
  artifacts `ap.tar`, `framenest_release.py`, and `superproject.tar`. A
  failure after the checkpoint can instead leave a fourth artifact,
  `previous-release`, and the owner record; inspect the exact contents before
  recovery. Unexpected names, extra files, or a missing expected file stop the
  run.

3. **Remove only the exact residual lock artifacts, then the empty lock
   directory, then the owner record.** Do not use wildcard, recursive, or
   parent-directory deletion. Remove each named object separately, directory
   before owner record, so an interruption never leaves an ownerless directory
   that automatic reclamation refuses. Unexpected contents stop the run.

```text
# [NUC / bash]
sudo rm -f /run/framenest-release-deploy/ap.tar
sudo rm -f /run/framenest-release-deploy/framenest_release.py
sudo rm -f /run/framenest-release-deploy/superproject.tar
sudo rmdir /run/framenest-release-deploy
sudo rm -f /run/framenest-release-deploy.owner
#------------------------------------------------------
```

`rmdir` must succeed on an empty directory. If it fails, stop: the engine's
own lock release could not complete either, and it preserves the primary
`migration-required` failure while suppressing that cleanup error.

4. **Migrate from the new release tree**, never from `/opt/framenest/current`,
   under the operator command execution contract:

```text
# [NUC / bash]
sudo -u framenest --chdir=/opt/framenest/releases/<T> \
  env FRAMENEST_ENV_FILE=/etc/framenest/framenest.env \
  /opt/framenest/releases/<T>/.venv/bin/framenest-db migrate
sudo -u framenest --chdir=/opt/framenest/releases/<T> \
  env FRAMENEST_ENV_FILE=/etc/framenest/framenest.env \
  /opt/framenest/releases/<T>/.venv/bin/framenest-db status
#------------------------------------------------------
```

Post-migration status must show `current_revision=head_revision=<H>` for the
observed target head `<H>`. Any other revision stops the run.

5. **Complete cutover** through the documented switch to an already-complete
   target tree:

```text
framenest-release rollback --release <T> --yes
```

Here `rollback` is the supported cutover onto `/opt/framenest/releases/<T>`
after the target tree is schema-complete. It is not an improvised downgrade.

6. **Final `status`.** Require exact SHA `<T>`, active `framenest.service`,
   catalog schema equal to the recorded target head `<H>`, and backup
   restore-readiness `ready`.

7. **Terminal privilege release.** After final `status`, the Cooperator
   invalidates the sudo timestamp:

```text
# [NUC / bash]
sudo -K
#------------------------------------------------------
```

If the session is lost first, privilege release is unknown, not assumed.

A post-migration cutover failure requires explicit triage. Never improvise a
downgrade or catalog restore.

### Identity Migration (Non-Routine; Not This Continuation)

The continuation above migrates the catalog schema only. The separate host
identity migration subcommand (`migrate-identity`) is a non-routine
operation. No routine `deploy`, `rollback`, `check`, `status` or capture
command invokes it, and this annex never invokes it. It shares the routine
release lock above and uses its own prepared remote scratch area and root-only
control state:

```text
Remote engine helper scratch: /run/kronika-identity-migration
Root-only control state:      /var/lib/kronika-identity-migration
Journal:                      /var/lib/kronika-identity-migration/journal.json
Recovery manifest:            /var/lib/kronika-identity-migration/recovery-manifest.json
```

Neither path belongs to the routine lock directory, and routine cleanup never
touches either.

If the identity migration fails before the new service starts, its automatic
pre-write recovery restores the observed account, group, home and
unit/scheduler state, but it deliberately leaves the copied canonical state,
the installed canonical unit files and the installed ancillary files in
place. A retry is refused until a separately authorized explicit recovery.
That residue is by design; an operator must not expect a clean slate after a
failed pre-write attempt. After the recorded writes boundary, forward
recovery is a separate authorized decision and never restores stale copied
state.

## 6. Readiness Verification

Read-only checks:

The readiness gate itself runs inside the service unit through
`ExecStartPre` with the unit's own `WorkingDirectory` and `EnvironmentFile`:

```text
/opt/framenest/current/.venv/bin/framenest-production check-database-ready
```

Manual operator verification of the same migration contract uses the
read-only status form under the operator command execution contract:

```text
sudo -u framenest --chdir=/opt/framenest/current \
  env FRAMENEST_ENV_FILE=/etc/framenest/framenest.env \
  /opt/framenest/current/.venv/bin/framenest-db status
```

AI status preflight uses the network-free read-only form with the explicit
non-secret AI configuration path:

```text
sudo -u framenest --chdir=/opt/framenest/current \
  env FRAMENEST_ENV_FILE=/etc/framenest/framenest.env \
  /opt/framenest/current/.venv/bin/framenest-ai \
  --config-path /var/lib/framenest/ai/config.json status --no-write
```

The ordinary `framenest-ai status` command may record a safe local status
snapshot. Use `--no-write` when deployment preflight must avoid creating or
modifying AI status files.

Security control: read-only readiness gate.

- Threat: starting against a missing, empty, behind, ahead, or unreadable
  database.
- Benefit: startup fails before binding the service when the database is not at
  packaged Alembic head.
- Limitation: readiness does not test networking, media availability, or remote
  client behavior.
- Rollback: restore previous release/database state and re-run readiness.
- Verification: command exits success and emits sanitized ready output.

Stop conditions:

- Readiness fails.
- Readiness creates or mutates the database.
- Output discloses private paths, SQL, tracebacks, or environment values.

Evidence:

- Sanitized readiness output.
- Confirmation that no migration ran during readiness.

## 7. Controlled Activation

Service-affecting mutation:

- Start or restart only the FrameNest service after readiness passes.
- Do not enable public listeners.
- Do not configure Tailscale in this phase.

Security control: systemd foreground supervision.

- Threat: orphaned daemons, unmanaged logs, or development launcher behavior in
  production.
- Benefit: systemd supervises one foreground `framenest-production serve`
  process and captures stdout/stderr in journald.
- Limitation: systemd supervision is not application authentication or backup.
- Rollback: stop the service, restore previous release reference, run
  readiness, and start the previous service.
- Verification: unit uses `ExecStartPre` readiness and `ExecStart` serve from
  `/opt/framenest/current/.venv/bin/framenest-production`.

Stop conditions:

- The service would use `./framenest`, Poetry as supervisor, reload mode,
  shell wrappers, or browser-opening behavior.
- The service would write to `/srv/media`.

Evidence:

- Sanitized `systemctl` status for the FrameNest unit.
- Sanitized unit content or verification output.

## 8. Health And Log Verification

Read-only checks:

- Query `GET /health` through `127.0.0.1`, or run the release-local
  `framenest-production check-health` command, which uses the Unix socket
  automatically when the Tailscale ingress mode from section 11 is active.
- Inspect recent journald entries for the FrameNest unit.
- Verify logs contain no credentials, private media filenames, raw provider
  responses, database paths, or tracebacks.
- Verify the service did not call a provider during startup.

Security control: sanitized journald capture.

- Threat: leaking secrets or private paths through operator logs.
- Benefit: application-owned logs use structured sanitized stderr captured by
  journald.
- Limitation: journald retention and host log access remain host policy.
- Rollback: stop the service if logs reveal sensitive data and perform a
  security incident review before continuing.
- Verification: sanitized log sample and health response.

Stop conditions:

- Health fails.
- Logs show tracebacks, raw paths, provider keys, authorization headers, or
  private media names.
- The service binds outside loopback.

Evidence:

- Sanitized health response.
- Sanitized recent log sample.
- Listener verification showing loopback binding only.

## 9. Rollback

Rollback commands and mutations must be planned before activation.

Rollback sequence:

1. Stop only the FrameNest service if the new release is running.
2. Restore the previous `/opt/framenest/current` reference.
3. Restore a verified database backup to a new path and perform the separately
   authorized controlled replacement when migration compatibility requires it.
4. Run `check-database-ready` from the restored release.
5. Start the service.
6. Verify health and logs.

Stop conditions:

- The previous release or database backup is missing.
- The previous release readiness fails.
- Rollback requires destructive storage actions not already authorized.

Evidence:

- Previous release SHA.
- Restored database backup identifier.
- Readiness result.
- Health and log verification.

## 10. Evidence Capture

Capture only sanitized evidence:

- Exact deployed commit or release SHA.
- `uv` version and artifact verification result.
- CPython version.
- Poetry install result.
- Database backup identifier.
- Migration result.
- Readiness result.
- Service activation result.
- Loopback health result.
- Sanitized logs.
- Final rollback target retained.

Do not capture or share:

- passwords;
- API keys;
- authorization headers;
- cookies;
- private keys;
- full environment dumps;
- private network values;
- disk UUIDs or serial numbers;
- SSH fingerprints;
- private media filenames;
- paths below the approved generic roots.

## 11. Tailscale Remote Access Ingress

This phase is a separately authorized slice. It is not part of the base
activation above and requires its own bounded task authority.

Architecture:

```text
authenticated tailnet browser
  -> Tailscale HTTPS Serve (root-owned tailscaled)
  -> /run/framenest/framenest.sock (service-account Unix socket)
  -> FrameNest tailscale_uds ingress mode
```

Security properties:

- The application stops listening on TCP entirely; Tailscale Serve is the
  only remote application ingress today. A second public listener is a new
  operational object per
  [ADR-0074](adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md)
  and is not part of this phase or of routine `framenest-release` updates.
- Serve strips and reinjects `Tailscale-User-*` identity headers; the
  application trusts them only in this ingress mode, bound to the protected
  Unix socket, and never trusts same-named headers from any other channel.
- `RuntimeDirectory=framenest` (mode `0750`, service account only) and
  `UMask=0077` keep the socket closed to normal login users; the root-owned
  `tailscaled` can always reach it.
- An explicit configuration identity map assigns roles; unknown verified
  identities are denied, privileged actions are capability-checked and
  recorded in the durable `security_audit_events` table.
- Browser mutations require the exact external `Origin` plus the
  `X-FrameNest-Request: 1` header; no CORS middleware is enabled.

Configuration (placeholders; see `deploy/systemd/framenest.env.example`):

```text
FRAMENEST_INGRESS_MODE=tailscale_uds
FRAMENEST_UDS_PATH=/run/framenest/framenest.sock
FRAMENEST_EXTERNAL_ORIGIN=https://<node>.<tailnet>.ts.net
FRAMENEST_IDENTITY_MAP={"<verified-login>":"<admin|user>"}
# Optional; empty remains fail-closed. See docs/X_COMPANION.md.
# FRAMENEST_COMPANION_EXTENSION_ORIGINS=["chrome-extension://<32-char-id>"]
```

Rules:

- The exact verified Serve login must be observed through
  `GET /api/identity/me` from an authenticated tailnet client before the
  admin mapping is written.
- The database must be backed up before migration `0020` runs, and the
  backup readability must be verified.
- Health verification in this mode uses the release-local
  `framenest-production check-health` command, which speaks to the Unix
  socket; there is no separate TCP health listener.
- Funnel stays disabled. No LAN binding, no tailnet-wide ACL, DNS, user, or
  tag changes, and no stale node cleanup belong to this slice.

Serve activation (root, after the application is healthy on the socket):

```bash
tailscale serve --bg unix:/run/framenest/framenest.sock
tailscale serve status --json
tailscale funnel status
```

Verification:

- No FrameNest TCP listener remains (`ss -tlnp` shows no port 8000).
- `tailscale serve status --json` shows exactly one HTTPS handler to the
  Unix socket, and Funnel reports no configuration.
- `framenest-production check-health` reports ready.
- `GET /api/identity/me` through the tailnet HTTPS URL returns the expected
  login, role, and capability list.

Rollback:

1. Capture `tailscale serve status --json` before any change; remove only
   the FrameNest Serve handler (`tailscale serve reset` is acceptable only
   when the captured state was empty).
2. Remove the four ingress environment keys and restore the previous
   `/opt/framenest/current` reference.
3. Restore the pre-migration database backup when the previous release
   predates migration `0020`, per `docs/BACKUP_AND_RECOVERY.md`.
4. Restart the service and verify loopback health.

Stop conditions:

- Tailscale is below the accepted minimum version, Serve Unix-socket proxying
  is unsupported, or MagicDNS/HTTPS would require a tailnet-wide setting that
  is not already enabled.
- The observed Serve login differs from the intended admin identity.
- A normal login user can open the application socket.

## Catalog Identity-Label Maintenance (Non-Routine; Stopped Writers)

This section is a bounded maintenance procedure for correcting operator-typed
catalog display labels. It is not part of routine `kronika-release` updates and
is not a general device or library editing surface. Complete it only under its
own authorization and only while every catalog writer is stopped.

The installed command is `kronika-catalog identity-labels` with exactly three
operations:

```text
kronika-catalog identity-labels check
kronika-catalog identity-labels apply --yes [--include-libraries]
kronika-catalog identity-labels rollback --yes [--include-libraries]
```

`check` is read-only for the catalog. It resolves the device whose stored
display name is exactly `FrameNest NUC` and reports only sanitized values:
whether exactly one expected device candidate exists, how many devices carry
the retired label, and how many library display names contain the retired
brand. It never prints identifiers, library names, media paths or SQL. It
writes one private selection receipt beside the catalog at
`/var/lib/framenest/catalog.sqlite3.identity-labels-receipt.json`, mode `0600`,
holding the exact selected rows and the inverse values needed for rollback, and
prints only that receipt's digest. An existing receipt is never overwritten:
a second `check` stops until the operator removes an obsolete receipt.

Run every operation as the catalog owner: the private-catalog check compares
the catalog's ownership with the invoking account and fails closed for any
other account.

Procedure:

1. Stop and drain every catalog writer, including the web service and every
   catalog timer or oneshot. Confirm that no writer remains.
2. Run `check` and review only the sanitized result. If more than one device
   carries the retired label, stop and resolve the ambiguity first.
3. Create and verify a consistent catalog checkpoint through the installed
   backup implementation.
4. Run `apply --yes`. Add `--include-libraries` only when the authorization
   names library rows; without it, libraries are never changed. The command
   refuses a stale receipt, an ambiguous candidate set, a missing confirmation
   and a busy catalog, and it changes nothing unless every compare-and-set
   condition holds inside one transaction.
5. Read back the command's sanitized assertions: the label transition, valid
   foreign keys and the unchanged schema revision. `device list` is not
   sufficient evidence and prints identifiers.
6. Retain the private receipt until rollback is no longer required. Do not
   delete it while a rollback remains possible.
7. Run `rollback --yes [--include-libraries]` to restore the exact prior labels
   by inverse compare-and-set. It refuses a receipt whose recorded state no
   longer matches the catalog.

Never restore an old whole-catalog backup merely to undo a display label. The
command only changes the selected `display_name` values: no row is deleted or
re-registered, no identifier, root or relationship changes, and no schema
revision is applied.

## Not Implemented By This Runbook

- Real deployment acceptance of the automated catalog-backup timer on a host.
- Off-device copies, media-byte backup, and in-place production catalog overwrite.
- Live production provider-secret deployment or provider testing.
- Tailscale Funnel or any ingress beyond the authenticated tailnet. A second
  public listener or public TLS termination is a new operational object per
  [ADR-0074](adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md)
  and is not part of this runbook.
- Live Mullvad exit-node assignment; see [OPERATOR_NETWORK.md](OPERATOR_NETWORK.md)
  and [ADR-0058](adr/0058-independent-mullvad-egress-and-operator-network-recovery.md).
- Multi-user administration UI, invitations, or per-user personal metadata.
- AppArmor profile.
- UFW policy changes.
- SSH changes.
- Upload or synchronization.
- Managed ingest area.
- System-disk encryption.
- High availability.
