# Operator network scripts

Repository-native controls for independent Mullvad egress. The durable
operator contract is [docs/OPERATOR_NETWORK.md](../../../docs/OPERATOR_NETWORK.md)
and [ADR-0058](../../../docs/adr/0058-independent-mullvad-egress-and-operator-network-recovery.md).

These files are source material. They do not grant host, account, or live
network authority.

## Artifacts

| File | Role |
|---|---|
| `kronika_mullvad_egress.sh` | Shared Bash implementation for CachyOS and Ubuntu |
| `kronika_mullvad_egress.fish` | Thin `ahw` wrapper around the Bash script |
| `kronika_nuc_worker_gate.fish` | Strict noninteractive SSH transport to the NUC |

## Interface

```text
kronika_mullvad_egress.sh status
kronika_mullvad_egress.sh enable --node <mullvad-node>.mullvad.ts.net
kronika_mullvad_egress.sh disable
kronika_mullvad_egress.sh verify
kronika_mullvad_egress.sh recover
```

The Fish wrapper accepts the same arguments and returns the Bash exit status.

The SSH gate requires explicit `--target` / `--user` / `--identity` /
`--command` values, or the public-safe environment variables documented in
the operator contract. It never hardcodes those values. `--probe` validates
SSH-agent capability without printing the socket or opening SSH. Discovery
tries trusted `gpgconf` first, then on Darwin only a validated ambient
launchd socket when `gpgconf` is unavailable.

## Test-only command resolution

Production resolves `tailscale`, `curl`, and related tools from a bounded
trusted PATH and never executes a current-directory binary. Tests may set
`KRONIKA_NETWORK_TEST_HOOKS=1` plus absolute fake tool paths. Darwin agent
fallback tests may also supply simulated platform, socket, owner, prefix, and
liveness outcomes through that same hook namespace. Those hooks are not a
production interface.
