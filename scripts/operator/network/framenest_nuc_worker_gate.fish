#!/usr/bin/env fish
# Retained gate spelling. The canonical NUC worker gate is
# scripts/operator/network/kronika_nuc_worker_gate.fish (ADR-0085); this file
# forwards every argument and the exit status to that same gate and is kept
# until the compatibility spellings are removed.

set -l script_dir (dirname (status filename))
set -l gate "$script_dir/kronika_nuc_worker_gate.fish"

if not test -x "$gate"
    echo "Kronika NUC worker gate is unavailable." >&2
    exit 1
end

exec "$gate" $argv