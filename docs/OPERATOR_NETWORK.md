# FrameNest Operator Network Contract

## Artifact classification

Classification: durable operator contract for independent Mullvad public
egress and network recovery. It is not application ingress configuration and
does not grant host, account, or live-network authority.

Consumers: Cooperator, Orchestrator, Workers, and the operator on `ahw` and
`framenest-nuc`.

Retention: remains while ADR-0058 is accepted.

Inbound links: [ADR-0058](adr/0058-independent-mullvad-egress-and-operator-network-recovery.md),
[ADR-0048](adr/0048-tailscale-remote-access-and-identity-foundation.md),
[SERVER.md](../SERVER.md), [SECURITY.md](../SECURITY.md),
[UBUNTU_NUC_DEPLOYMENT.md](UBUNTU_NUC_DEPLOYMENT.md), and
[scripts/operator/network/README.md](../scripts/operator/network/README.md).

Repository presence of this document or the companion scripts grants no
Tailscale account action, Mullvad assignment, SSH access, or host mutation.
Those remain separately authorized.

## Accepted topology

- `ahw` and `framenest-nuc` each select an explicit Mullvad exit node.
- Direct tailnet and MagicDNS packets remain overlay traffic.
- Tailscale Serve ingress stays tailnet-only on the protected Unix socket.
- FrameNest does not gain a public listener or inbound exposure.
- `--exit-node-allow-lan-access=false` is the enable default.
- Tailscale preference persistence is the reboot mechanism.
- Public diagnostics reduce to `Mullvad egress`, `non-Mullvad egress`, or
  `unknown`.

## Rejected topology

- Advertising `ahw` as an exit node.
- Exit-node chaining or routing one device through the other.
- `auto:any` (it may select a non-Mullvad node).
- Mandatory exit-node or MDM policy.
- Custom boot units for this contract.
- Firewall, NetworkManager, Wi-Fi, router, manual-route, forwarding, or
  sysctl changes.
- Competing standalone Mullvad tunnels beside Tailscale Mullvad egress.
- Funnel, router port forwarding, or any public inbound path.

## Privacy limitations

The goal is Mullvad public egress, not anonymity. Tailscale remains
identity-aware. Overlay member identity, Serve login mapping, and MagicDNS
names continue to exist. Public verification must not print IP addresses,
tailnet identity, account data, node keys, or raw `tailscale status --json`.

## Installed-command feature detection

Clients do not expose identical CLI surfaces. A newer client may provide
`tailscale get`; an older client may not. Scripts treat that preference
surface as usable only when a read-only `tailscale get` probe for the required
preference actually exits zero. Command presence alone is not enough.
Readable `get` remains the preference and LAN-access surface. A non-DNS or
opaque selected preference is not itself enough to classify provider type;
sanitized `tailscale status --json` identifies the selected peer as Mullvad or
non-Mullvad, and raw opaque preference values are not emitted. Unavailable or
unreadable preference access falls back to that same sanitized JSON for
selected-exit-node classification. LAN-access is reported as unavailable
without a usable `tailscale get`; it is not treated as proof that LAN-access
state is `false`. Mutation still uses `tailscale set`, which both observed
client generations support. Scripts never assume the two machines share one
CLI.

## Subcommands

Canonical shared Bash implementation:

```text
scripts/operator/network/kronika_mullvad_egress.sh status
scripts/operator/network/kronika_mullvad_egress.sh enable --node <mullvad-node>.mullvad.ts.net
scripts/operator/network/kronika_mullvad_egress.sh disable
scripts/operator/network/kronika_mullvad_egress.sh verify
scripts/operator/network/kronika_mullvad_egress.sh recover
```

`ahw` invokes the adjacent canonical Fish wrapper
`scripts/operator/network/kronika_mullvad_egress.fish`. The NUC uses the Bash
script through the strict SSH gate. Host execution is separately authorized.

The retained `framenest_mullvad_egress.sh` and `framenest_mullvad_egress.fish`
entry points keep working unchanged until the compatibility-removal window;
the canonical pair is authoritative. The canonical implementation resolves
every operator variable from the `KRONIKA_*` spelling first and still accepts
the retained `FRAMENEST_*` spelling; a pair set to different values refuses
with exit 2 naming only the two variable names.

| Subcommand | Mutation | Network contact |
|---|---|---|
| `status` | none | none |
| `enable --node` | `tailscale set` exit node and LAN-access false | none |
| `disable` | clears only the selected exit node | none |
| `verify` | none | one call to the documented Mullvad diagnostic endpoint |
| `recover` | clears only the selected exit node | none |

Scripts never run `tailscale up`, `tailscale down`, `tailscale login`, or
`tailscale logout`. They never advertise an exit node, never change accepted
routes, DNS, Serve, Funnel, SSH, firewall, Wi-Fi, NetworkManager, forwarding,
or sysctl state, never configure a Tailscale operator identity, and never
escalate privileges.

## `ahw` sequence

```text
# [ahw / fish]
scripts/operator/network/kronika_mullvad_egress.fish status
scripts/operator/network/kronika_mullvad_egress.fish enable --node <mullvad-node>.mullvad.ts.net
scripts/operator/network/kronika_mullvad_egress.fish verify
scripts/operator/network/kronika_mullvad_egress.fish disable
scripts/operator/network/kronika_mullvad_egress.fish recover
#------------------------------------------------------
```

Confirm standalone Mullvad is not an active tunnel before enable. Confirm
Serve and overlay connectivity after enable. Do not reboot both devices in
the same window.

## NUC sequence

Use MagicDNS, not an IP address:

```text
# [ahw / fish]
set -gx KRONIKA_NUC_SSH_TARGET <nuc-magicdns-name>
set -gx KRONIKA_NUC_SSH_USER <operator-user>
set -gx KRONIKA_NUC_SSH_IDENTITY <identity-file>
scripts/operator/network/kronika_nuc_worker_gate.fish \
  --command 'kronika_mullvad_egress.sh status'
#------------------------------------------------------
```

The retained `FRAMENEST_NUC_SSH_*` names and the retained
`framenest_nuc_worker_gate.fish` wrapper remain accepted until the
compatibility-removal window.

Before `enable` on the headless NUC, arm the separately authorized transient
rollback described below. Then transmit only a bounded remote command that
runs `enable`, `verify`, `disable`, or `recover`. Cancel the rollback after a
successful `verify`. Do not combine this with Serve, firewall, or deployment
work.

## Cursor Worker SSH gate

Cursor Workers use `scripts/operator/network/kronika_nuc_worker_gate.fish`
as the sole project-owned NUC SSH route. They must not reconstruct
`gpgconf --list-dirs agent-ssh-socket` or print `SSH_AUTH_SOCK`.

```text
scripts/operator/network/kronika_nuc_worker_gate.fish --probe
```

`--probe` prints only `ssh-agent: ready` or `ssh-agent: absent`. It does not
print the socket path and does not open SSH. Discovery tries trusted
`gpgconf` on `/usr/sbin:/usr/bin:/sbin:/bin` first. Only when `gpgconf` is
absent from that path, and only when trusted `uname -s` is exactly `Darwin`,
the gate accepts the ambient `SSH_AUTH_SOCK` if every check holds: non-empty,
absolute, no `..` path segment, a Unix socket owned by the invoking effective
user, and a `realpath` under `/private/var/run/com.apple.launchd.`. Liveness
is `ssh-add -l` with output discarded; exit 0 or 1 is a working agent and any
other status is refusal. The native launchd socket mode is not required to be
restrictive. On success the gate sets that socket for its own process and for
the SSH child. A found `gpgconf` that yields no socket, or a missing
`gpgconf` on any other platform, stays `ssh-agent: absent`.
A Cursor parent that lacks `SSH_AUTH_SOCK` is expected, not a host defect.
The BatchMode SSH form above remains the transport when a later task grants
NUC access; the gate attaches the agent for its own process without printing
it.

## Human admin-console gate

Mullvad exit nodes appear only after the Tailscale admin console grants
Mullvad access to that device. This repository does not perform that grant.
A future console action may be required for `framenest-nuc`; describing that
step here is not authority to open the console or change the account.

## `NeedsLogin` stop behavior

If Tailscale reports `NeedsLogin`, scripts stop mutating and do not call
`tailscale login`. The operator completes login under a separately authorized
host session.

## Standalone Mullvad conflict handling

An installed or running standalone Mullvad daemon is not proof of a tunnel.
Scripts treat daemon presence and tunnel proof as separate facts. A positively
detected active standalone tunnel blocks `enable`. Ambiguous CLI output also
blocks mutation. Missing `mullvad` is not a positive competing-tunnel
detection.

## MagicDNS

Prefer full MagicDNS names for operator SSH targets. Use placeholders such as
`<nuc-magicdns-name>` rather than a raw IP. This contract does not record a
real tailnet suffix.

## Explicit-node validation

`enable --node` accepts one normalized hostname that ends exactly in
`.mullvad.ts.net`. Rejected values include empty strings, whitespace, leading
hyphens, non-DNS tokens, `auto:any`, and any name that is not a Mullvad exit
node DNS name. There is no default city or node.

## LAN-access and DNS posture

Enable sets `--exit-node-allow-lan-access=false`. Overlay tailnet and
MagicDNS communication does not require LAN access. This contract does not
change Tailscale DNS, MagicDNS, or `--accept-dns`.

## Optional future operator identity

A future separately authorized host decision may grant the login user
Tailscale operator rights so `tailscale set` succeeds without privilege
escalation. These scripts never configure that identity and never escalate
privileges. Permission denied yields a short explanation that a separately
authorized host grant is required.

## Transient NUC rollback design

The headless NUC requires an automatic transient rollback *before* changing
the NUC exit-node preference. The delay is exactly 10 minutes, and the
rollback remains capable of firing if SSH disconnects or the Worker
terminates. Required properties:

- armed under a separately authorized host task, not by these scripts;
- clears only the selected exit node;
- is transient (not a persistent boot unit);
- is cancelled only after the required SSH, Mullvad-egress, FrameNest-health,
  and Serve/Funnel verification gates pass;
- does not change Serve, DNS, firewall, or routes.

This repository slice does not install or start that timer. Repository
presence alone grants no timer, sudo, host, Tailscale, or account authority.

## One-device-at-a-time reboot acceptance

After preferences persist, reboot or otherwise reprove at most one device,
then confirm overlay connectivity and egress, then consider the other device.
Do not take both members down together.

## Disable and recovery

`disable` and `recover` clear only the selected exit node. They do not restore
ISP routing by changing firewall or default routes. `recover` preserves the
first causal failure when a later read-only status probe also fails. After
`NeedsLogin`, competing-tunnel, or permission failures, stop; do not improvise
additional network changes.

## Output sanitization

Public and log output may include sanitized labels and a verified
`*.mullvad.ts.net` hostname. It must not include exact public IPs, tailnet
names, account emails, node keys, fingerprints, or raw JSON.

`verify` contacts only `https://am.i.mullvad.net/json` and reduces the body to
`Mullvad egress`, `non-Mullvad egress`, or `unknown`. Transport, HTTP, or
parse failure is `unknown` and must not be reported as `non-Mullvad egress`.

## No public inbound exposure

This contract does not add a listener, Funnel handler, router forward, or
WAN SSH. Serve remains the only remote FrameNest ingress.

## No credential storage

Scripts do not store tokens, cookies, account passwords, or private keys.
The SSH gate requires an operator-supplied identity file path and does not
hardcode it. Agent sockets discovered via `gpgconf`, or on Darwin via the
validated launchd fallback, are used without printing.
`--probe` reports only `ssh-agent: ready` or `ssh-agent: absent`.
