# Kronika NUC Security Hardening

Classification: living host-boundary hardening and activation contract.
Consumers: Cooperator, Orchestrator, independent reviewers, and explicitly
authorized host operators. Retention: while the Ubuntu NUC boundary and its
recovery helper are supported. Update owner: a separately authorized Worker
task reconciled by the Orchestrator; host acceptance remains separate.

## Scope and current evidence

NSH-IMPL-05 supplies repository source for the web and local catalog-backup
unit restrictions, a fixed-surface recovery helper, synthetic tests, and these
operator gates. This is non-independent implementation evidence. It does not
claim host hardening is complete. Independent candidate review is row 05V;
installation and acceptance are row 08. UFW and SSH cuts remain rows 09–10,
with deferred decisions D1–D8 retained for rows 09–12. Those identifiers refer
to the approved plan; this document does not invent or resolve their contents.

This slice does not update dependencies or the lockfile, replace frozen
runtimes, activate capture, provision credentials, contact providers, or access
private media. Research stays disabled by default, internet publication stays
off, and remote ingress remains Tailscale Serve to the protected Unix socket.
No router forwarding is accepted. The existing host drop-in
`30-publication-write-path.conf` remains a deliberate deferred exception;
the repository base unit gains no publication write path.

The [FrameNest host baseline](NUC_HOST_BASELINE.md) is frozen historical
evidence, not current host acceptance. See the
[sole-identity decision](adr/0085-kronika-sole-identity.md#named-frozen-residues)
and [clean-install exceptions](UBUNTU_NUC_CLEAN_INSTALL.md#2-canonical-naming-and-frozen-exceptions)
for retained names and bytes. Do not duplicate or rewrite those inventories.

## Selected service restrictions

Both [web](../deploy/systemd/kronika.service) and
[local backup](../deploy/systemd/kronika-catalog-backup.service) sources add:

| Setting | Boundary |
|---|---|
| `PrivateDevices=true` | Private minimal device view |
| `ProtectHostname=true` | Protect hostname changes |
| `ProtectClock=true` | Protect host clock changes |
| `ProtectKernelLogs=true` | Deny kernel log access |
| `RestrictNamespaces=true` | Deny namespace creation |
| `RestrictRealtime=true` | Deny realtime scheduling |
| `SystemCallArchitectures=native` | Native syscall ABI only |
| `SystemCallFilter=~@clock @module @mount @raw-io @reboot @swap` | Deny the selected privileged syscall groups |
| `SystemCallErrorNumber=EPERM` | Return a bounded refusal for filtered calls |

The web source adds `CacheDirectoryMode=0700`,
`RuntimeDirectoryMode=0750`, and
`RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`. IPv4/IPv6 remain available
for approved outbound work and loopback stubs; this does not activate Research
or grant any provider call. Socket provenance still requires `0600` before
requests are served. The base unit keeps no publication write exception.

The local backup source adds `RestrictAddressFamilies=AF_UNIX`,
`IPAddressDeny=any`, and `InaccessiblePaths=/srv/media`. Its catalog-only
pipeline needs no IP network or original-media access. Capture and off-device
unit sources are unchanged. Existing users, groups, release paths, explicit
environment files, readiness/serve commands, restart/stop policy, journald,
private state, strict filesystem protection, and empty capabilities remain.
Web `MemoryDenyWriteExecute`, `PrivateUsers`, `RemoveIPC`, and `ProcSubset`
are deliberately absent from the selected bundle.

These settings target Ubuntu 24.04 systemd 255.x. Local parser verification
does not prove effective sandbox behavior on that host. Stage source review
separately from host activation so namespace, device, syscall, and outbound
compatibility failures can be attributed to one bounded change. Routine
`kronika-release` operations do not install units or this helper, reload the
manager, or activate firewall/SSH policy. Their behavior is unchanged.

## Recovery helper interface and state

Source: [kronika_nuc_boundary_rollback.sh](../scripts/operator/security/kronika_nuc_boundary_rollback.sh).
Installed target: `/usr/local/libexec/kronika/nsh-boundary-rollback`.
Installation belongs to row 08; repository presence installs nothing.
Invoke the installed helper with exactly two arguments:

| Operation | Form | Effect |
|---|---|---|
| Snapshot | `nsh-boundary-rollback snapshot ufw` or `snapshot sshd` | New private snapshot; never overwrite |
| Verify | `nsh-boundary-rollback verify ufw` or `verify sshd` | Check snapshot inventory, schema, metadata and SHA-256; no live-file writes |
| Restore | `nsh-boundary-rollback restore ufw` or `restore sshd` | Preflight every destination, then restore only the recorded files |

Only these exact surfaces are accepted:

| Surface | Exact persistent files |
|---|---|
| `ufw` | `/etc/ufw/user.rules`, `/etc/ufw/user6.rules` |
| `sshd` | `/etc/ssh/sshd_config.d/00-kronika-hardening.conf`, `/etc/ssh/sshd_config.d/50-cloud-init.conf` |

The UFW files must both exist. SSH drop-ins may be absent before their cut;
absence is an explicit record and restore removes only that exact newly
created regular file. Both surface directories must exist and pass checks.
Missing required surfaces stop snapshot; missing directories stop restore.
No arbitrary path argument, SSH host key, profile, credential, or unrelated
configuration can enter a snapshot.

Default execution requires root with surface files owned by root:root and
mode `0600`, `0640`, or `0644`. Other modes, special bits, non-regular files,
hard links, symlinks (including ancestors), unsafe directory owners, and
group/other-writable directories are refused. The state root is
`/var/lib/kronika-host-hardening`, root:root `0700`; snapshot directories
are `0700` and files `0600`. `umask 077` applies throughout.
Each surface has `manifest` and `files/<relative-surface-path>` children.
The bounded, versioned manifest records presence, original mode/UID/GID and
SHA-256; stored bytes remain private regardless of the original mode.
Metadata is parsed as data, never evaluated as shell code.

A non-blocking `flock` on the state root's `lock` spans every operation.
Held locks are conflicts, with no retry. Existing snapshots, unknown entries,
incomplete `.pending-ufw` or `.pending-sshd` state, corrupt metadata or bytes,
and pre-existing exact restore staging files fail closed with one first
causal message and nonzero status. A repeated snapshot is stale/conflicting;
there is no automatic expiry or replacement. Verification checks saved bytes
and metadata even if live configuration has changed or disappeared.
It may create a missing private lock file, but never alters live surfaces.

`KRONIKA_NSH_TEST_ROOT` is the sole test-only override: an existing canonical
absolute private `0700` directory owned by the invoking UID and primary GID.
Its descendants use that same expected ownership; ancestors above the
synthetic boundary make no host-safety claim. Paths, state and surfaces are
all prefixed by that root. Invalid, empty, relative, symlinked, or non-private
roots are rejected. Installed operation defaults to `/`, and the independent
rollback job must use a clean root environment with this override absent.

Restore stages exact bytes beside each destination, sets recorded ownership
and mode, checks hashes, and replaces each present file atomically. It is not
a transaction across both files, nor a power-loss durability guarantee.
An interruption can leave exact `.nsh-boundary-rollback-<basename>.next`
files. Preserve the primary failure and saved snapshot; inspect and recover
those exact objects under a separate task before retry. Never recursively
change permissions or delete with wildcards. The helper performs no network
access, sudo, service restart, reload, reboot, or package operation.

## Operator flow and lockout prevention

Execute only under a later exact host grant. This is the gate order, not an
instruction to run host commands during repository implementation:

1. Establish a working physical console/recovery route and retain the current
   authenticated operator session. Record the private baseline, intended cut,
   effective policy and installed helper identity. Confirm no concurrent
   operator or automated writer can alter the selected files.
2. Snapshot the selected surface and verify it. Exercise the installed helper
   against synthetic files first; reject stale or conflicting state. Keep
   recovery bytes private and separate from catalog backup bundles.
3. Arm an independent root-owned rollback timer before applying one cut.
   Prove that its exact job can verify and restore the recorded surface and
   apply the matching UFW reload or SSH validation/reload. These activations
   belong to the future host task, not the helper. A files-only restore does
   not change policy already loaded in the kernel or daemon.
4. Prove the timer and its service run without SSH, DNS, Tailscale, a surviving
   client process, or a sudo timestamp. Use absolute installed paths, clean
   root environment, validated job identity/deadline and sanitized result.
   If this proof cannot exist in the current session, do not apply the cut.
5. Apply exactly one UFW or SSH cut. Preserve the old session. Prove a fresh
   intended key-authenticated login over the actual tailnet connection,
   including Wi-Fi support, and prove the selected denied path/authentication
   fails for the intended reason. Confirm application health, Serve-only
   ingress and Funnel-off posture. An existing session is not fresh-login
   evidence; a transport failure alone is not authentication-denial proof.
6. Cancel the timer only after every positive and negative gate passes.
   Record cancellation and absence of pending rollback execution. Otherwise
   leave rollback armed, use the console route, and stop further cuts.

The helper intentionally has no timer-install or retire command. Retain
snapshots while an armed job or rollback may need them. Before the next cut,
an authorized operator must prove no pending consumer remains and retire only
the exact known snapshot objects. No automatic stale-state cleanup exists.
Timer deadline and effective UFW/SSH policy are selected by the future bounded
cut; the separate ten-minute exit-node rollback in the
[network runbook](OPERATOR_NETWORK.md) keeps its own contract.

## Proof required at host activation

Row 08 must collect fresh evidence against the exact reviewed source:

| Gate | Required proof |
|---|---|
| Unit syntax | `systemd-analyze verify` on Ubuntu 24.04 systemd 255.x, with every diagnostic classified |
| Effective settings | Installed base files and every drop-in, loaded settings, service identity and execution paths; a syntax pass alone is insufficient |
| Filesystem boundary | Socket `0600`, runtime `0750`, cache `0700`, state `0700`; root-owned Serve can reach the socket, ordinary login users cannot |
| Web compatibility | Synthetic SQLite read/write and WAL, media inspection and preview generation using synthetic media only, with private device/namespace/syscall restrictions active |
| Outbound compatibility | Local IPv4/IPv6 HTTP stubs and Unix-socket paths under the effective web sandbox; no real provider or external media contact |
| Backup isolation | Under the installed backup sandbox, IP socket/contact attempts fail and synthetic original-media reads at the denied root fail, while catalog create/verify/disposable-restore succeeds |
| Scheduling | An actual timer-triggered backup completes after activation; correlate unit trigger, operator success and restore-readiness `ready`; a manual oneshot is insufficient |
| Post-boot matrix | After an authorized reboot, repeat effective settings, modes, intended fresh login and selected denials, Serve/health, parked capture, and scheduled-backup/readiness checks |

Independent candidate review and synthetic repository tests cannot demonstrate
these installed-host facts. Workstation parser warnings about absent NUC
executables are a stated limitation; they never become a fabricated host PASS.

## Residual risks and deferred decisions

Root or another authorized privileged writer can race pathname checks or alter
the helper/state. Root-controlled directories and serialized operator changes
are preconditions; Bash path checks are not a hostile-root containment system.
An owner-private synthetic test root does not prove root deployment safety.
The rollback timer is independent of network/session reachability but not of
the host kernel, storage, power, or a reboot that discards a transient timer.
Never reboot during a network cut relying on that timer. A restart of SSH or
UFW is outside this source helper and must be proved separately.

The publication-media write exception remains deferred. Sandbox restrictions
do not replace application authorization, AppArmor design, catalog/media/secret
recovery, or physical boot/disk protection. Frozen runtime maintenance is not
silently included. D1–D8 remain deferred to rows 09–12 without an acceptance
or closure claim here; no effective firewall/SSH rule or other deferred policy
is selected by this repository bundle.

Continue through the [deployment runbook](UBUNTU_NUC_DEPLOYMENT.md),
[clean-install runbook](UBUNTU_NUC_CLEAN_INSTALL.md),
[backup runbook](BACKUP_AND_RECOVERY.md), and
[operator-network contract](OPERATOR_NETWORK.md) for their separate authority,
recovery and acceptance boundaries.
