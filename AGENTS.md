# FrameNest Agent Instructions

FrameNest is a local-first, privacy-conscious, cross-platform library for video
and animated media. It is in foundation-stage pre-alpha development with a
working loopback FastAPI server, SQLite/Alembic persistence, packaged local web
shell, catalog and metadata foundations, server-side AI suggestion review, a
catalog backup foundation, and repository-native systemd source material for an
Ubuntu NUC deployment workflow.

## Accepted Kronika Direction

[ADR-0082](docs/adr/0082-kronika-one-product-and-private-records.md) records
the accepted transition to one Kronika in this existing repository.
[ADR-0083](docs/adr/0083-modular-research-providers-and-administrator-curated-timeline.md)
records the current Search and Research architecture and the
administrator-curated Timeline. FrameNest supplies the application, catalog,
media preparation, identity and deployment. Search and Research are added
through a provider-neutral application boundary. The chatgpt.com capture
module, package `kronika_capture` and command `kronika-capture`, remains one
parked module on the loopback bridge. Do not port a second manager, account
system, family library or Git history. ADR-0085 is the sole-identity authority;
it is implemented as an ordered sequence of bounded cuts, not as a mass
branding replacement.

ADR-0085 is the sole-identity authority; the ordered identity cuts implement it.
The S4-A through S10 sequence in
[ROADMAP.md](ROADMAP.md) is complete. The public repository is
`cisarik/kronika`. The former capture repository remains `cisarik/cli_chatgpt`
and stays active.
The S3 host remainder, capture-mode Search and Research, S5 ZIP activation and
S7-C capture integration stay parked. One implementation grant covers one row;
the roadmap itself grants no execution authority.

<!-- BEGIN MANAGED AP INTEGRATION -->
## Analytic Programming

This project uses Analytic Programming through the pinned Git submodule at `.ap/`.
The exact AP version is the commit recorded by this repository's `.ap` gitlink.

Required reading:
- All participants read `.ap/AP.md`.
- Orchestrators also read `.ap/AP_ORCHESTRATOR.md`.
- Workers also read `.ap/AP_WORKER.md`.
- Prompt structures are in `.ap/PROMPT_CONTRACTS.md`.

Project-specific rules outside this managed block remain authoritative within
their scope. Task authority comes only from the current authoritative
Orchestrator prompt.

Treat `.ap/` as read-only during ordinary project work. Protocol updates require
a separate explicit AP update task.
<!-- END MANAGED AP INTEGRATION -->

## Cursor Worker Execution Boundary

Cursor/AppImage ambient execution is untrusted. Cursor Workers must not
directly invoke `.venv/bin/python`, `python`, `python3`, or `poetry run` for
Python evidence.

- Python and tests go through `./.ap/ap project check` and `./.ap/ap exec`
  with an exact authorized `--baseline`.
- NUC SSH goes through `scripts/operator/network/kronika_nuc_worker_gate.fish`
  (`--probe` for agent capability; BatchMode SSH only when a later task grants
  NUC access). Do not reconstruct `gpgconf` or print agent sockets. The retained
  `scripts/operator/network/framenest_nuc_worker_gate.fish` wrapper and the
  `FRAMENEST_NUC_SSH_*` variable names keep working, with the canonical
  `KRONIKA_NUC_SSH_*` names taking precedence and a conflicting pair failing
  closed with exit 2; both spellings are removed only in the C7-B window.
- Remote sudo lifecycle is Cooperator timestamp (`sudo -v`, then `sudo -n true`)
  outside the Worker, plus Worker terminal `sudo -K`. Workers must not run
  `sudo -v` or handle a password. Password-required after predecessor `sudo -K`
  is expected lifecycle state, not a broken NUC.

Details and classification live in
[docs/WORKER_EXECUTION_CONTRACT.md](docs/WORKER_EXECUTION_CONTRACT.md). Do not
duplicate universal AP protocol here.

## NUC Routine Release Update

Per [ADR-0075](docs/adr/0075-nuc-development-test-target-and-routine-release-refresh.md)
the NUC is FrameNest's development-and-testing machine: routinely refreshing it
to the exact public `main` SHA — including schema jumps through the documented
`migration-required` continuation — is normal operation through the sole entry
point below. Non-routine host work still requires its own explicit bounded task.

The sole routine immutable NUC release-update entry point is:

```text
deploy/ubuntu/kronika-release
```

It invokes `deploy/ubuntu/kronika_release.py` (standard library only).
`deploy/ubuntu/framenest-release` and `deploy/ubuntu/framenest_release.py` are
retained wrappers that forward identical arguments to that same engine, and both
are removed only in the C7-B window; they are not a second deployment system.
Future Orchestrators and Workers must use the canonical entry point instead of
reconstructing deployment commands, probing generic PATH locations, or confusing
initial host bootstrap with a routine release update.

- Always run `kronika-release status` and `kronika-release check --release
  <40-hex-SHA>` before any deployment. Deployment never follows automatically
  from a check. The retained `framenest-release` spelling still runs the same
  two commands unchanged.
- Routine updates use exactly:

```text
Poetry:  /opt/framenest/tooling/poetry/2.4.1/.venv/bin/poetry
CPython: /opt/framenest/tooling/python/cpython-3.13.14-linux-x86_64-gnu/bin/python3.13
```

- `uv` is bootstrap and explicit tooling-maintenance tooling only. Routine
  release updates never invoke `uv` and never require `uv` on `PATH`.
- Deployed releases contain no `.git` metadata. Release provenance comes from
  `.framenest-release-sha` and `.framenest-release-manifest.json`, not from
  `git -C /opt/framenest/current`.
- Do not improvise routine deployment commands. See
  [docs/UBUNTU_NUC_DEPLOYMENT.md](docs/UBUNTU_NUC_DEPLOYMENT.md) and
  [ADR-0060](docs/adr/0060-repeatable-immutable-nuc-release-update-contract.md).

## UI/UX Acceptance And Companion Testing Require A Current NUC

Rendered UI/UX acceptance belongs to the Cooperator. Whenever the Cooperator is
asked to test rendered UI/UX, perform any visual acceptance, or exercise the
Brave companion (whose side panel and review flows call the NUC API), the code
under test must already be published on GitHub `main` and refreshed onto the
NUC through the routine release update above, so he always sees the actual
current version. Never request rendered acceptance against code the NUC cannot
serve. Testing over the Tailscale tailnet is normal operation
([ADR-0075](docs/adr/0075-nuc-development-test-target-and-routine-release-refresh.md)),
not production exposure; treating the NUC as a guarded production server is
retired framing.

## AP Upgrade Ledger

AP upgrade ledger declaration:
Upgrade ledger: upgrade https://github.com/cisarik/ap.git
Ledger storage version: 1
Ledger path: docs/AP_UPGRADE_OBSERVATIONS.md

## Project Truth

Repository files, tests, Git history, public commits, ADRs, and current product
documents are the source of truth. Worker reports are structured claims and must
be verified against repository and public evidence when commits are involved.

Current product, system, and operational truth is distributed across:

- [README.md](README.md) for repository status and user-facing overview.
- [PRODUCT.md](PRODUCT.md) for approved product direction.
- [SPEC.md](SPEC.md) for normative product and system requirements.
- [ROADMAP.md](ROADMAP.md) for staged development.
- [SECURITY.md](SECURITY.md) for security policy and privacy boundaries.
- [SERVER.md](SERVER.md) for authoritative server/client and NUC direction.
- [docs/UBUNTU_NUC_DEPLOYMENT.md](docs/UBUNTU_NUC_DEPLOYMENT.md) for the
  Ubuntu NUC deployment runbook.
- [docs/BACKUP_AND_RECOVERY.md](docs/BACKUP_AND_RECOVERY.md) for the catalog
  backup and recovery foundation.
- [docs/NUC_HOST_BASELINE.md](docs/NUC_HOST_BASELINE.md) for sanitized,
  command-observed NUC host baseline facts.
- [docs/adr/README.md](docs/adr/README.md) for accepted architecture decisions.
- [docs/WORKER_EXECUTION_CONTRACT.md](docs/WORKER_EXECUTION_CONTRACT.md) for
  Worker runtime, `.venv`, exact-source evidence, and test invocation rules.
- [DEVELOPMENT.md](DEVELOPMENT.md) for the local browser-development launcher.

When sources conflict, identify the exact conflict, determine whether a source
is stale, incomplete, misunderstood, or intentionally superseded, and escalate
strategic conflicts to Michal through the Orchestrator.

## Communication

The COOPERATOR is Michal. Orchestrator communication with Michal is in Slovak,
addresses him with masculine grammatical forms, and uses feminine grammatical
forms for Orchestrator self-reference.

Repository documentation, code documentation, Worker prompts, and Worker reports
are written in professional English unless a task explicitly says otherwise. Do
not use Czech in repository documents, Worker prompts, or Worker reports.

Worker reports begin exactly:

```markdown
### Report for ORCHESTRATOR_CHAT
```

Human-facing command blocks for Michal's MacBook use Fish-compatible syntax and
begin with:

```text
# [MacBook / fish]
```

Human-facing command blocks for an already-open NUC session use Bash-compatible
syntax and begin with:

```text
# [NUC / bash]
```

Every human-facing command block for either environment ends with:

```text
#------------------------------------------------------
```

Do not mix MacBook and NUC commands in one unlabeled block.

## Cooperator Presentation Profile

Project-owned presentation for FrameNest AP work. This is not AP semantics and
not Worker authority; the copyable, structurally English Worker prompt remains
the sole authority grant.

Orchestrator chat updates to Michal open with a one-glance status block of at
most five lines (FrameNest HEAD SHA, AP pin SHA, whole/phase, open risk),
followed by exactly one status mark:

- 🟢 healthy / proceed / PASS
- 🟡 wait / exactly one open decision
- 🔴 stop / BLOCKED / catastrophe

One decision per message. Chat language follows the Communication section;
Worker prompts and repository artifacts remain professional English.

Delivery route: an Agent Orchestrator defaults to direct session dispatch of
one complete authoritative Worker prompt into one concrete Worker session. An
explicit Cooperator opt-out (P14 model rotation or manual messenger mode)
selects copy-paste delivery as the lawful selected route.

Delivery capsule emitted after the copyable, structurally English Worker
prompt:

- Route: Agent Orchestrator default dispatch, or copy-paste under explicit P14 opt-out
- Reasoning: lowest sufficient profile for the task
- Downloadable prompt filename: <trace-grammar prompt filename for the exchange>
- Activated-trace destination: the era trace directory designated by the current restoration handout (outside this repository, local-only)
- Archival: wait-for-report; the Orchestrator archives the prompt and its terminal report together after the report exists

## Security Boundaries

Private media access requires explicit task authority. Real provider calls
require explicit task authority. Credentials, secret values, private keys,
tokens, cookies, authorization headers, private media filenames, host-specific
identifiers, disk serials, UUIDs, SSH fingerprints, and private network values
must not be exposed in repository artifacts or reports.

NUC, SSH, sudo, firewall, storage, package-manager, deployment, systemd,
AppArmor, UFW, Tailscale, and mount mutations require explicit bounded
authority. Availability of a connection, credential, terminal, mounted disk, or
tool is capability context, not authority.

FrameNest backend services must remain loopback-first by default. No router port
forwarding is accepted for FrameNest. Remote access direction remains
Tailscale-only unless a later accepted decision supersedes it. Tailscale
membership is not application administrator authority.

Original server media under `/srv/media` is read-only to the service by default.
Do not grant broad service write access to source media to implement upload or
ingest. Ordinary clients must never receive provider secrets.

For the accepted Kronika record architecture, verified identity determines
ownership and every new record starts private to its owner. An authenticated
application administrator can read all product records, including private and
unfinished work. That privilege is application content only. It grants no
access to provider secrets, browser credentials or host administration.
Ordinary household members cannot read another owner's private or unfinished
records. Apply owner, administrator and household rules to lists, search,
detail, previews, playback, downloads and direct APIs. Household publication
of completed question and answer records, and of successfully analyzed media,
is an administrator approval. Owners do not publish directly to the shared
page. Internet publication stays disabled; the public composition stays off
and must not expose new records. These are requirements for later
implementation, not claims about the current code. The earlier administrator
denial is retained only as history in ADR-0082 and is superseded by ADR-0083.

The parked capture module must use one persistent browser, loopback-only token
authentication, bounded attachment staging and explicit `needs_admin` recovery
without an automatic resend. Never inspect browser credentials, cookies,
sessions, localStorage, profiles, unrelated tabs or history. Only the
Cooperator handles real login; opaque profile backup/restore also belongs only
to him, with the browser stopped for backup/restore. Parked capture uses the
page as configured, without model or reasoning inspection or selection, and
without falling back from that page to an external API. Search and Research
do not use that capture path. They use the ADR-0083 provider boundary with the
[ADR-0084](docs/adr/0084-administrator-managed-research-settings-and-versioned-pricing.md)
correction: the first provider is the OpenAI Responses API with native
provider-managed research, supervised locally, with no automatic fallback.
Verified administrators select one of four exact catalog models
(`gpt-5.5-2026-04-23` remains the default) through server settings only;
unknown and alias models fail before persistence, reservation or provider
contact, and there is no network model discovery. Only expressly submitted
question text may leave the host on that path. Generated output remains
untrusted. Research remains disabled by default. Live calls and credential
provisioning need their own authority.

The accepted empty-database transition requires its own exact-object reset
grant after writers stop. It grants no deletion of media, profiles, identity
configuration, secrets or archives and no import of the old test databases.

## Product Boundaries

FrameNest remains local-first. A FrameNest server process is authoritative for
catalog and server-owned state, but it may run locally and must not turn local
ownership into public-cloud dependence. The premium gallery remains a flagship
product invariant.

Under ADR-0083, the Timeline is the main page and contains only
administrator-approved records. Personal history is a separate view and stores
questions and their answers, including complete Research reports and the
caller's unfinished work. Gallery remains a separate working view using the
existing design and player. Media enters the shared Timeline only after
successful validated analysis and administrator approval. Search and Research
become personal history when their complete results are saved; that completion
does not by itself enter the Timeline. Preserve existing metadata-review
approval. Personal photos and their future local AI analysis are outside this
stage, as are internet publication and production hardening.

Rendered UX acceptance belongs to Michal. The accepted Gallery and Details MVP
visual behavior remains frozen unless a concrete defect is identified.

Do not hide new product scope inside infrastructure, deployment, backup,
protocol, or migration work. Do not claim desktop app, complete Cover Studio,
arbitrary collection management, persistent AI Drafts, multi-model draft
comparison, synchronization, media second-copy backup, or full production
hardening until repository evidence and owner acceptance prove it. Living status
for already-shipped foundations belongs in README, ROADMAP, PRODUCT, SPEC, and
SERVER; do not reopen closed logical wholes merely because prose elsewhere is
stale.

## Worker Execution

Workers must follow
[docs/WORKER_EXECUTION_CONTRACT.md](docs/WORKER_EXECUTION_CONTRACT.md) for
the Cursor/AppImage execution boundary, canonical `./.ap/ap exec` Python
route, NUC SSH gate, remote sudo lifecycle, CPython 3.13 / Poetry authority,
canonical `.venv` preservation, isolated worktree exact-source provenance,
Python and JavaScript test invocation, repository browser-evidence gates,
failure classification, and no-GUI shell rules. Implementation authority does
not implicitly grant push, publication, deployment, production mutation,
provider contact, or external X/YouTube acquisition.

## Git And Lifecycle

Do not perform Git write operations without task-specific authority. When Git
writes are authorized, stay within the exact branch, path, commit, push, and
verification authority in the task.

FrameNest uses the AP submodule at `.ap/` for universal protocol, role,
authority, Worker lifecycle, diagnostic closeout, Git, verification, prompt,
artifact, update, and exceptional handoff semantics. Do not duplicate universal
AP protocol text in project-local files.

Permanent `BOOT_*`, `NEXT_*`, `WORKERS.md`, `NEXT_AGENT.md`,
`ORCHESTRATOR_HANDOFF.md`, and `WORKER_HANDOFF.md` files are not part of the
current live repository model. Orchestrator rotation normally uses a generated
professional restoration prompt. A repository handoff is exceptional context
only and may be created or changed only by an explicitly authorized Worker task
that names its exact path, consumer, lifecycle, validation, and Git authority.
