#!/usr/bin/env fish
# Thin ahw wrapper around the shared canonical Bash Mullvad egress
# implementation.

set -e APPIMAGE APPDIR ARGV0 LD_LIBRARY_PATH LD_PRELOAD

set -l script_dir (dirname (status filename))
set -l bash_impl "$script_dir/kronika_mullvad_egress.sh"

if not test -f "$bash_impl"
    echo "Shared Bash implementation is missing." >&2
    exit 127
end

/bin/bash "$bash_impl" $argv
exit $status
