# Kronika Ubuntu NUC Clean Install and Recovery

## 1. Purpose and installation boundary

This is a runbook for a future installation on a fresh or replacement Ubuntu
NUC. It is not a receipt that an installation, restore, login, or deployment
was performed. The Cooperator decided that the live host retains its existing
host and tailnet identity; no live reinstall or host rename is planned.

Classification: clean-install and recovery operator runbook. Consumers:
Cooperator, Orchestrator, and explicitly authorized host operators. Retention:
while Ubuntu NUC is the supported development-and-testing target. Update owner:
a future bounded documentation task. No installer is supplied by this document.

Use the [deployment runbook](UBUNTU_NUC_DEPLOYMENT.md) for operational phases,
release-lock recovery, and schema continuation; use the
[backup runbook](BACKUP_AND_RECOVERY.md) for catalog recovery. The accepted
[sole identity](adr/0085-kronika-sole-identity.md) and
[development/test role](adr/0075-nuc-development-test-target-and-routine-release-refresh.md)
govern. Historical spelling in frozen ADRs does not select current commands.

Before starting, obtain an exact bounded host task covering the intended OS,
account, storage, toolchain, unit, SSH, and ingress changes, with rollback or
recovery checkpoints. Routine release authority does not cover bootstrap.
Select the exact published source SHA and its pinned AP gitlink. Keep private
configuration and recovery evidence outside the repository. Never record real
addresses, logins, SSH filenames, device identifiers, media names, or secrets.

**Checkpoint:** the target is identified privately, the authorized phases and
recovery route are explicit, and any existing state has a preservation decision.
An empty-host plan grants no deletion on an existing installation.

## 2. Canonical naming and frozen exceptions

On a fresh setup choose canonical product naming: `kronika-nuc` for the host
and tailnet node name, and `Kronika NUC` for its application device display
name. These are naming conventions, not an observation of the live host. Use
the resulting full MagicDNS name privately for operator SSH. Do not rename an
existing node merely to follow this fresh-install convention.

Preserve these exact ADR-0085 exceptions:

| Object | Required spelling | Meaning |
|---|---|---|
| Poetry | `/opt/framenest/tooling/poetry/2.4.1/.venv/bin/poetry` | Pinned bootstrap output and routine toolchain |
| CPython | `/opt/framenest/tooling/python/cpython-3.13.14-linux-x86_64-gnu/bin/python3.13` | Pinned standalone interpreter |
| Encrypted snapshot protocol | `FNCBE01` | Durable protocol magic; do not rewrite historical artifacts |
| Off-device mount | `/mnt/framenest-catalog-offdevice` | Fixed optional catalog-copy destination |
| Capture default state name | `framenest-chatgpt-page` | Frozen state-directory name; no executable alias of that name |

The capture default state name is used by the XDG state resolver. The installed
capture units instead pass an explicit service state directory; do not confuse
that override with a rename of the frozen default. The only capture executable
is `kronika-capture`. Frozen document and applied migration bytes stay intact.

**Checkpoint:** fresh names are canonical; each exception matches its source;
the existing live host identity remains unchanged.

## 3. Prepare Ubuntu, access, and bootstrap tooling

Prepare Ubuntu Server 24.04 LTS on x86_64. Follow the Check, Plan, and Prepare
Release phases of the deployment runbook for manual OS/toolchain preparation.
Keep Ubuntu's system Python intact. Establish the separately authorized SSH,
UFW, AppArmor, storage, and Tailscale prerequisites without weakening them to
make an application check pass. No router forwarding, public SSH, Funnel, or
public application listener is part of this setup.

SSH must remain reachable over the tailnet, including when the NUC uses Wi-Fi.
The Cooperator's recorded Wi-Fi decision rules out a wired-only access boundary.
Check operator SSH over the actual intended connection before leaving the host
headless. Tailnet membership alone grants no application administrator role.

Bootstrap may use an explicitly selected and verified `uv` artifact to acquire
the standalone CPython 3.13.14 interpreter. Verify the artifact checksum and
attestation when available. Install Poetry 2.4.1 and preserve the exact tooling
paths in section 2. Poetry owns dependencies, the committed `poetry.lock`, and
each release-local `.venv`; `uv` is only bootstrap/interpreter acquisition or
explicit tooling-maintenance tooling. Do not replace Ubuntu Python, use a
download-and-execute installer pipeline, or resolve a new lock on the NUC.

Routine updates reuse that exact Poetry and CPython pair. A routine update
never invokes `uv`, requires it on PATH, or downloads missing tooling. Missing
or mismatched tooling stops the helper; repair is a separate maintenance task.
The workstation Fish release entry point requires the repository's prepared
local environment. The NUC runs release-local consoles under systemd and does
not require Fish or the development launcher.

**Checkpoint:** verify OS/architecture, private tailnet SSH reachability,
tool versions and artifact provenance, and the absence of public ingress.
Record sanitized results, not raw network or environment dumps.

## 4. Accounts, directories, and original media

Create the system account and group `kronika` for the web service. Keep the
operator login separate. Prepare the following canonical layout with least
privilege, following the [web unit](../deploy/systemd/kronika.service) and
[non-secret environment template](../deploy/systemd/kronika.env.example):

| Root | Purpose and ownership boundary |
|---|---|
| `/opt/kronika` | Root-controlled immutable `releases/<SHA>` trees and `current` pointer; service can read and traverse |
| `/etc/kronika` | Operator-controlled `kronika.env`; optional root-controlled `credentials/` kept separate |
| `/var/lib/kronika` | Service-owned private catalog and durable state; state directory mode `0700` |
| `/var/cache/kronika` | Service-owned regenerable previews and cover thumbnails; `CacheDirectoryMode=0700` |
| `/run/kronika` | systemd-managed runtime `0750` and protected `kronika.sock` `0600`; `UMask=0077` |
| `/srv/media` | Original media; read-only to the service by default |

The catalog is `/var/lib/kronika/catalog.sqlite3`. Non-secret AI configuration
is `/var/lib/kronika/ai/config.json`; the administrator runtime overlay is
`/var/lib/kronika/runtime-settings.json`. Cover storage is
`/var/lib/kronika/covers`. Preview and thumbnail caches follow the environment
template. Do not grant broad service write access to source media.

Uploads require a separately selected private quarantine root
(`KRONIKA_UPLOAD_QUARANTINE_ROOT`) and a registered server-owned publication
library (`KRONIKA_UPLOAD_PUBLICATION_LIBRARY_ID`). Quarantine holds untrusted
bytes; storage publication follows validation and uses only the explicitly
selected destination. It does not grant household Timeline approval. Never
use source-media write access as an ingest shortcut. Leave optional ingest
unconfigured until its distinct storage and authorization gates pass.

YouTube staging, when separately configured, uses
`/var/lib/kronika/youtube-acquisition`; X staging is selected through
`KRONIKA_X_ACQUISITION_ROOT`. Keep staging disjoint from quarantine, catalog,
caches, covers, and registered media roots. Staging directories must be private,
pre-existing, and not symlinks. The centralized
[settings contract](../src/kronika/configuration.py) validates private-root
separation; it does not provision storage. Neither this runbook nor a restore
drill acquires external media or contacts a provider.

**Checkpoint:** verify account ownership, traversal, private state modes,
read-only originals, and non-overlapping storage roots without listing media.
Do not broaden home-directory access to repair a service-account command.

## 5. Install configuration and units in dependency order

Use exact source artifacts from the selected release. Review the effective
unit and any drop-ins, not just the base file.

The [hardening contract](NUC_SECURITY_HARDENING.md) owns the selected web and
local-backup restrictions and host proof matrix. Source review does not prove
the sandbox works on the new host. Verify systemd 255.x syntax, effective
settings, synthetic SQLite/media/preview workloads and local outbound stubs
before acceptance. The base web unit grants no publication write path; any
existing host exception remains a separate reviewed artifact. The recovery
helper is installed only under a bounded host task at
`/usr/local/libexec/kronika/nsh-boundary-rollback`, with root-only state
`/var/lib/kronika-host-hardening`. Before any UFW/SSH cut, prove console access,
snapshot/verify and an independent rollback timer that needs no SSH, DNS,
Tailscale or sudo timestamp. Require fresh authentication and denial gates
before cancelling it. No routine release update supplies that installation.

1. Prepare accounts, mount availability, directories, and the first release as
   described in section 6. Put the non-secret template at
   `/etc/kronika/kronika.env`, adjusting only approved settings. It contains
   no credentials. Keep all services stopped until their prerequisites pass.
2. Install [kronika.service](../deploy/systemd/kronika.service) in the system
   unit directory. Its working directory is `/opt/kronika/current`;
   `ExecStartPre` runs `kronika-production check-database-ready` and
   `ExecStart` runs `kronika-production serve` from that tree. Preserve
   `ProtectSystem=strict`, private state, empty capability sets, and journald
   output. Reload systemd after installing approved unit material.
3. Keep optional provider credential drop-ins uninstalled on a clean default
   setup. The source inventory is
   [NVIDIA](../deploy/systemd/kronika-ai-credential-nvidia-nim.conf),
   [Vercel](../deploy/systemd/kronika-ai-credential-vercel-ai-gateway.conf),
   [OpenCode](../deploy/systemd/kronika-ai-credential-opencode-go.conf), and
   [research](../deploy/systemd/kronika-research-credential.conf). They map
   named systemd credentials from root-controlled files, never values in the
   environment file. Provisioning or enabling any provider is a separate task.
   Research stays disabled by default, per
   [ADR-0084](adr/0084-administrator-managed-research-settings-and-versioned-pricing.md).
4. After explicit catalog migration and readiness pass, enable/start the web
   unit and verify health. Install the catalog service/timer pairs and accept
   their scheduling as specified in section 7. Install optional capture sources
   only under section 8's separate provisioning boundary; leave them stopped.

Default ingress is `tcp` with `KRONIKA_HOST=127.0.0.1`. For the separately
authorized remote-access phase, set `KRONIKA_INGRESS_MODE=tailscale_uds`,
`KRONIKA_UDS_PATH=/run/kronika/kronika.sock`, the exact private HTTPS origin
through `KRONIKA_EXTERNAL_ORIGIN`, and an explicit `KRONIKA_IDENTITY_MAP`
with at least one verified administrator. Follow deployment runbook section 11
to verify the Serve identity and mapping. Root-owned Tailscale HTTPS Serve
proxies only to the protected socket; the application then has no TCP listener.
Health checks must use that ingress, not an invented second health port.

The socket must be service-owned with mode `0600` before serving requests.
Keep `public_published_uds` off, internet publication disabled, and Funnel off.
Browser mutations require the exact Origin and `X-Kronika-Request: 1`.
Companion mutations remain fail-closed until the exact extension origin is
allowlisted through `KRONIKA_COMPANION_EXTENSION_ORIGINS`; this is independent
of household/admin role mapping. Optional `KRONIKA_LOCAL_OWNER_LOGIN` must name
an already mapped login; it does not create an administrator.

**Checkpoint:** verify effective user/group, environment-file path, execution
paths, database readiness, healthy service, actual listener/socket permissions,
and verified identity/capabilities through the intended ingress. Do not dump
configuration values or logs into a shared report.

## 6. First release, routine updates, and schema recovery

The sole release helper is
[deploy/ubuntu/kronika-release](../deploy/ubuntu/kronika-release), backed by
[kronika_release.py](../deploy/ubuntu/kronika_release.py). It is a routine
update helper, not an empty-host installer: `status` and `check` require a
canonical installed unit, an existing `current` release and catalog, and
`check` requires backup restore-readiness `ready`.

For a genuinely empty host, first complete the separately authorized manual
bootstrap in deployment runbook phases 2–8: prepare an exact published source
tree with its pinned AP content, install the committed lock with the pinned
Poetry/CPython pair into its release-local `.venv`, establish the canonical
unit/environment layout and first `current` pointer, explicitly migrate the
new catalog, and verify readiness and health. Release trees contain no `.git`;
provenance must use `.kronika-release-sha` and
`.kronika-release-manifest.json` consistent with the engine's marker/manifest
contract. Do not fabricate markers for an unverified tree. Complete the first
verified scheduled backup pipeline before requesting routine `check`.

This manual prerequisite does not supply another installer or deployment
system. Do not invoke the helper's private remote modes to bypass its gates.
A replacement-host restore instead follows the backup runbook's restore-to-new-
path and separately authorized controlled replacement boundary; it is not an
import of the unwanted old test databases.

Once bootstrap is accepted, use these public forms from the workstation
repository. Substitute one exact 40-hex published `main` SHA for the release
placeholder, after verifying clean local source and the exact AP pin.

| Operation | Command form |
|---|---|
| Read current state | `deploy/ubuntu/kronika-release status` |
| Read-only candidate check | `deploy/ubuntu/kronika-release check --release <40-hex-SHA>` |
| Separately authorized web update | `deploy/ubuntu/kronika-release deploy --release <40-hex-SHA> --yes` |
| Switch to an already complete compatible tree | `deploy/ubuntu/kronika-release rollback --release <40-hex-SHA> --yes` |
| Separately authorized capture code update only | `deploy/ubuntu/kronika-release activate-capture --release <40-hex-SHA> --yes` |

Always run `status` and `check --release` before deployment. A successful check
never deploys automatically. Web deploy/rollback move only the web pointer and
web unit; they do not restart capture. Capture activation is not part of a
normal web update or the clean-install default.

For same-schema updates the helper prepares and hash-verifies immutable trees,
runs a fresh verified catalog checkpoint, switches `current` atomically, and
restarts the web unit with bounded readiness. It never migrates the database.
When deploy exits exactly 13, `migration-required`, the target tree has been
published under `/opt/kronika/releases/<T>` before checkpoint and cutover;
the previous release still runs. Follow the complete section 5 schema-jump
annex in the deployment runbook. Verify target provenance and observed live
revision `<C>` versus target head `<H>`, inspect the exact lock residue, and
recover only the named artifacts after ruling out a live lock owner. Unexpected
contents stop recovery; never use wildcard or recursive cleanup.

The explicit migration runs from the new target tree, never from `current`.
Require target status `current_revision=head_revision=<H>`. Complete the switch
with the helper's `rollback --release <T> --yes`, then require final `status`
at `<T>`, active service, the observed target schema, and restore-readiness
`ready`. Any other nonzero exit or post-migration cutover failure needs triage;
do not improvise a downgrade or catalog restore.

The Cooperator establishes the sudo timestamp outside a Worker; privileged
helper operations use noninteractive sudo. Terminal handling releases the
timestamp with `sudo -K`; a lost session makes release unknown. Workers never
handle a password or refresh that timestamp.

**Checkpoint:** record exact source/AP/release identities, helper exit status,
schema pair, health, backup readiness, and the retained compatible rollback
tree. `status` is the mutable runtime readback, not a committed SHA snapshot.

## 7. Schedule backups and prove restore readiness

Install the local pair after the release provides its backup console:
[kronika-catalog-backup.service](../deploy/systemd/kronika-catalog-backup.service)
and [kronika-catalog-backup.timer](../deploy/systemd/kronika-catalog-backup.timer).
The service runs `kronika-backup run-scheduled` as `kronika`, with the web
environment file and release working directory. The timer schedules
`03:17:00 UTC` daily, `Persistent=yes`, `RandomizedDelaySec=900`.
Reload systemd, enable/start the timer, and observe its actual service trigger.

The local backup sandbox permits `AF_UNIX` only, sets `IPAddressDeny=any`,
and denies access to `/srv/media`. Prove those restrictions with synthetic
negative tests and require a new actual scheduled success after activation;
the optional off-device unit has its own unchanged contract.

| Environment key | Canonical root or retention |
|---|---|
| `KRONIKA_CATALOG_BACKUP_ROOT` | `/var/lib/kronika/catalog-backups` |
| `KRONIKA_CATALOG_RESTORE_VERIFY_ROOT` | `/var/lib/kronika/catalog-restore-verify` |
| `KRONIKA_CATALOG_BACKUP_OPS_ROOT` | `/var/lib/kronika/catalog-backup-ops` |
| `KRONIKA_CATALOG_BACKUP_KEEP_AUTO` | Default `30`, minimum `3` |

The operator root contains the shared non-blocking operation lock and durable
attempt order. The pipeline creates, verifies, restores to a disposable new
destination, performs semantic readback, cleans owned disposable artifacts,
and only then applies eligible retention. Only verified ledger-recorded
`auto-` bundles expire; non-automatic bundles stay pinned. Failed pipelines
delete no older bundles. Readiness becomes stale after 48 hours without a
complete scheduled success. A later failure must remain visible.

Use the installed console with the unit's process environment for status;
the backup console does not load the environment file through its selector:

```text
# [NUC / bash]
sudo -n systemd-run --quiet --pipe --wait --collect --uid=kronika --gid=kronika --working-directory=/opt/kronika/current --property=EnvironmentFile=/etc/kronika/kronika.env /opt/kronika/current/.venv/bin/kronika-backup status
#------------------------------------------------------
```

Optional mounted copying uses
[kronika-catalog-offdevice.service](../deploy/systemd/kronika-catalog-offdevice.service)
and [kronika-catalog-offdevice.timer](../deploy/systemd/kronika-catalog-offdevice.timer),
scheduled `04:17:00 UTC` with the same persistent/random-delay settings. Before
enabling this timer, separately provision the frozen mount, its root-owned
`.kronika-catalog-offdevice.json` marker and private `bundles/` destination,
and privately set `KRONIKA_CATALOG_OFFDEVICE_DESTINATION_ID` to the marker's
identity. Unset means disabled. It runs `kronika-backup run-offdevice`, copies
the verified scheduled recovery point, and restore-verifies the destination.
Local retention does not delete off-device bundles; there is no off-device
retention automation. Prove its physical failure domain separately.

**Checkpoint:** scheduled success is the requirement. Correlate timer-triggered
unit activation with successful backup operator state, restore-readiness
`ready`, and no pending disposable cleanup. A manual checkpoint or manually
started oneshot alone is insufficient scheduling proof. If off-device copying
is enabled, require its own scheduled success and destination restore-readiness.

Catalog bundles contain only the manifest and SQLite catalog. They exclude
originals, covers, caches, AI configuration, runtime overlay, secrets, and
capture profiles. Plan those recovery classes separately. The preferred
off-host layer is operator-workstation pull; its provisioning, first real
pull/verify, and Level-2/3 restore drill remain distinct evidence in the backup
runbook. A catalog drill restores only into a new absent path. It neither
replaces the live database nor proves media or whole-host recovery.

## 8. Optional parked capture provisioning

Capture stays parked. A clean default install does not log in, start a browser,
enable these units, activate capture, or send capture work. Repository source
availability is not proof of host readiness. Provisioning needs a separate host
task and preflight for Node, Chromium's explicit absolute executable, Xvfb,
xauth, x11vnc, websockify/noVNC, sandbox/AppArmor, permissions, and free ports.

Use account/group `kronika-capture`, private state `/var/lib/kronika-capture`,
profile and staging children there, runtime `/run/kronika-capture`,
configuration `/etc/kronika-capture/capture.env`, and independent release
pointer `/opt/kronika/capture-current`. Review
[kronika-capture.env.example](../deploy/systemd/kronika-capture.env.example).
The credential source name is
`/etc/kronika-capture/credentials/kronika-bridge-token`; bridge and runner
load the systemd credential named `token`. This identifies the contract only;
no credential provisioning or value is supplied here. A later authorized web
drop-in may map that same named credential without granting profile access.

Install source units in dependency order, leaving them stopped and disabled:

1. [kronika-capture-xvfb.service](../deploy/systemd/kronika-capture-xvfb.service)
   owns display `:99` and its Xauthority; it has no restart loop.
2. [kronika-capture-bridge.service](../deploy/systemd/kronika-capture-bridge.service)
   provides the token-authenticated loopback bridge on port `8765`.
3. [kronika-capture-runner.service](../deploy/systemd/kronika-capture-runner.service)
   requires Xvfb, follows the bridge, and uses one persistent Chromium/profile.
   It has `Restart=no`, private runtime temporary storage, and a five-minute
   launch brake. A bridge outage reconnects; it does not relaunch the browser.
4. [kronika-capture-vnc.service](../deploy/systemd/kronika-capture-vnc.service)
   and [kronika-capture-view.service](../deploy/systemd/kronika-capture-view.service)
   are temporary loopback-only views on ports `5900` and `6080`. They have no
   install section and stop after 1800 seconds. No default login step exists.

Only the Cooperator performs real login or opaque profile backup/restore,
with Chromium stopped for backup/restore. Agents never inspect browser state.
Future authorized capture code changes alone use `activate-capture` after
draining work and passing identity/protocol/brake gates. It restarts the bridge,
then the runner, once; failure is not an automatic retry. Explicit `needs_admin`
recovery never resends a possibly sent prompt. Capture is not the Search or
Research provider and has no page-to-API fallback.

**Checkpoint:** sources and dependency/credential paths reconcile; parked
units remain inactive; no login, activation, provider call, or profile access
is claimed. Synthetic contract tests cannot prove Chromium works on a host.

## 9. Private operator setup notes

The Cooperator's existing SSH identity filename still contains the retired
spelling. Preserve that private filename on the live workstation; do not print
it or treat its spelling as an unfinished product rename. On a fresh setup
choose an explicitly selected canonical SSH identity filename privately.

Transport variables currently come from one exported user-dotfile
configuration. Keep that as the single existing source; do not claim Fish
universal-variable storage or prescribe a competing source. The helper accepts
`KRONIKA_NUC_SSH_TARGET`, `KRONIKA_NUC_SSH_USER`, and
`KRONIKA_NUC_SSH_IDENTITY`, or its explicit `--target`, `--user`, and
`--identity` options. Workers use the canonical SSH gate in the
[execution contract](WORKER_EXECUTION_CONTRACT.md) when separately authorized;
connection capability grants no host authority. Do not inspect personal shell
configuration or credential stores to populate this document.

**Checkpoint:** verify presence and intended use privately without displaying
values. No private filename, address, login, key, or agent socket enters the
repository or report.

## 10. Recovery cautions and final verification

Use the deployment runbook's Shared Release Lock Recovery and schema-jump annex
for interrupted updates; use the backup runbook's Restore Drill and Production
Recovery sections for restore-to-new-path and controlled replacement. Preserve
the first causal failure. Do not replace a database, remove a profile, or alter
a route as an improvised response to an application readiness failure.

Historical operator finding: ADR-0058's `recover` did not clear a stale
Tailscale policy-table default route on the live host; the exact route was
cleared separately. This is a historical recovery caution, not a claim about
a fresh installation and not an instruction to delete routes. The
[network contract](OPERATOR_NETWORK.md) clears only the selected exit node.
If connectivity remains broken, stop for a separately authorized, exact
diagnostic/recovery task. Do not treat `recover` as a universal routing reset
or copy a historical route into a new host's configuration.

Final evidence must distinguish instructions from completed work: exact
published source and AP identities; installed/served web identity; effective
unit/configuration and ingress; schema/readiness/health; timer-triggered backup
and restore-readiness; any separately provisioned off-host recovery layer;
and capture's parked state. Keep unknown or unperformed checks explicit.
Rendered UI and companion acceptance belongs to the Cooperator only after
the tested code is on public `main` and refreshed onto the NUC. This document
records no such acceptance and no completed installation.
