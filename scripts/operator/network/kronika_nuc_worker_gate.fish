#!/usr/bin/env fish
# Strict noninteractive SSH gate for bounded NUC operator commands.
#
# This file is the canonical gate (ADR-0085). The retained
# framenest_nuc_worker_gate.fish is a wrapper that forwards to this same file.

set -e APPIMAGE APPDIR ARGV0 LD_LIBRARY_PATH LD_PRELOAD

set -g trusted_path /usr/sbin:/usr/bin:/sbin:/bin

# Accepted identity environment prefixes for every operator variable this gate
# reads. The rule is identical to the Python mirror in
# kronika.identity_env.lookup_env and in the release engine: one spelling wins,
# an empty value counts as unset, both unset means unset, and both set to
# different values exits 2 naming the two variable names and never a value.
set -g identity_env_prefix KRONIKA_
set -g compatible_env_prefix FRAMENEST_

set -g gate_name (basename (status filename))

function _gate_usage
    echo "Usage: $gate_name --probe" >&2
    echo "Usage: $gate_name --target <name> --user <user> --identity <file> --command <bounded-command>" >&2
end

function _resolve_env
    set -l suffix $argv[1]
    set -l primary_name "$identity_env_prefix$suffix"
    set -l compatible_name "$compatible_env_prefix$suffix"
    set -l primary ""
    set -l compatible ""
    if set -q $primary_name
        set primary $$primary_name
    end
    if set -q $compatible_name
        set compatible $$compatible_name
    end
    if test -z "$primary"
        set primary ""
    end
    if test -z "$compatible"
        set compatible ""
    end
    if test -z "$primary"
        printf '%s' "$compatible"
        return 0
    end
    if test -z "$compatible"; or test "$primary" = "$compatible"
        printf '%s' "$primary"
        return 0
    end
    echo "Conflicting environment variables $primary_name and $compatible_name are set to different values" >&2
    return 2
end

# Resolve every test hook once, under the same two prefixes, before any logic
# reads one. A conflicting pair exits 2 through the same message as an operator
# variable, because a hook value is still an operator value.
set -l _hook_names NETWORK_TEST_HOOKS NETWORK_TEST_SSH NETWORK_TEST_UNAME \
    NETWORK_TEST_GPGCONF NETWORK_TEST_SSH_AUTH_SOCK NETWORK_TEST_AGENT_IS_SOCKET \
    NETWORK_TEST_AGENT_OWNER_MATCH NETWORK_TEST_AGENT_RESOLVED NETWORK_TEST_SSH_ADD_STATUS
for _hook_name in $_hook_names
    set -l _hook_value (_resolve_env $_hook_name)
    set -l _hook_status $status
    if test $_hook_status -ne 0
        exit $_hook_status
    end
    set -g _hook_$_hook_name "$_hook_value"
end

function _hooks_enabled
    test "$_hook_NETWORK_TEST_HOOKS" = 1
end

function _is_absolute_executable
    set -l candidate $argv[1]
    if not string match -q '/*' -- $candidate
        return 1
    end
    if string match -q '*..*' -- $candidate
        return 1
    end
    if not test -f $candidate -a -x $candidate
        return 1
    end
    return 0
end

function _trusted_lookup
    set -l name $argv[1]
    begin
        set -l PATH /usr/sbin /usr/bin /sbin /bin
        command -v $name
    end
end

function _resolve_tool
    set -l name $argv[1]
    set -l override ""
    if test (count $argv) -ge 2
        set override $argv[2]
    end
    if test "$_hook_NETWORK_TEST_HOOKS" = 1
        if test -n "$override"
            if _is_absolute_executable $override
                echo $override
                return 0
            end
            echo "Test hook tool path is not a trusted absolute executable." >&2
            return 1
        end
        echo "Required tool '$name' is not provided through the test hook." >&2
        return 1
    end
    set -l found (_trusted_lookup $name)
    or true
    if test -z "$found"
        echo "Required tool '$name' was not found in the trusted executable search path." >&2
        return 1
    end
    if not _is_absolute_executable $found
        echo "Required tool '$name' was not found in the trusted executable search path." >&2
        return 1
    end
    echo $found
end

function _optional_gpgconf
    if test "$_hook_NETWORK_TEST_HOOKS" = 1
        if test -n "$_hook_NETWORK_TEST_GPGCONF"
            if _is_absolute_executable $_hook_NETWORK_TEST_GPGCONF
                echo $_hook_NETWORK_TEST_GPGCONF
            end
        end
        return 0
    end
    set -l found (_trusted_lookup gpgconf)
    or true
    if test -n "$found"; and _is_absolute_executable $found
        echo $found
    end
end

function _trusted_executable
    set -l found (_trusted_lookup $argv[1])
    if test -z "$found"
        return 1
    end
    if not _is_absolute_executable $found
        return 1
    end
    echo $found
end

function _is_safe_absolute_path
    set -l candidate $argv[1]
    if test -z "$candidate"
        return 1
    end
    if not string match -q -- '/*' $candidate
        return 1
    end
    for segment in (string split / -- $candidate)
        if test "$segment" = ..
            return 1
        end
    end
    return 0
end

function _is_launchd_physical_path
    set -l resolved $argv[1]
    if not _is_safe_absolute_path "$resolved"
        return 1
    end
    set -l prefix /private/var/run/com.apple.launchd.
    if not string match -q -- "$prefix*" $resolved
        return 1
    end
    set -l rest (string replace -- $prefix "" $resolved)
    test -n "$rest"
end

function _gate_uname_s
    if test "$_hook_NETWORK_TEST_HOOKS" = 1
        echo "$_hook_NETWORK_TEST_UNAME"
        return 0
    end
    set -l uname_bin (_trusted_executable uname)
    if test $status -ne 0; or test -z "$uname_bin"
        return 1
    end
    env -u APPIMAGE -u APPDIR -u ARGV0 -u LD_LIBRARY_PATH -u LD_PRELOAD \
        PATH=$trusted_path \
        $uname_bin -s 2>/dev/null
end

function _agent_liveness_ok
    set -l live $argv[1]
    if not string match -qr '^[0-9]+$' -- "$live"
        return 1
    end
    test "$live" -eq 0; or test "$live" -eq 1
end

function _attach_darwin_ambient_from_hooks
    set -l sock $_hook_NETWORK_TEST_SSH_AUTH_SOCK
    if not _is_safe_absolute_path "$sock"
        return 1
    end
    if test "$_hook_NETWORK_TEST_AGENT_IS_SOCKET" != 1
        return 1
    end
    if test "$_hook_NETWORK_TEST_AGENT_OWNER_MATCH" != 1
        return 1
    end
    if not _is_launchd_physical_path "$_hook_NETWORK_TEST_AGENT_RESOLVED"
        return 1
    end
    if not _agent_liveness_ok "$_hook_NETWORK_TEST_SSH_ADD_STATUS"
        return 1
    end
    set -gx SSH_AUTH_SOCK "$sock"
    return 0
end

function _attach_darwin_ambient_from_system
    set -l sock $SSH_AUTH_SOCK
    if not _is_safe_absolute_path "$sock"
        return 1
    end
    if not test -S "$sock"
        return 1
    end
    set -l stat_bin (_trusted_executable stat)
    if test $status -ne 0; or test -z "$stat_bin"
        return 1
    end
    set -l id_bin (_trusted_executable id)
    if test $status -ne 0; or test -z "$id_bin"
        return 1
    end
    set -l realpath_bin (_trusted_executable realpath)
    if test $status -ne 0; or test -z "$realpath_bin"
        return 1
    end
    set -l ssh_add_bin (_trusted_executable ssh-add)
    if test $status -ne 0; or test -z "$ssh_add_bin"
        return 1
    end
    set -l owner (
        env -u APPIMAGE -u APPDIR -u ARGV0 -u LD_LIBRARY_PATH -u LD_PRELOAD \
            PATH=$trusted_path \
            $stat_bin -L -f %u "$sock" 2>/dev/null
    )
    if test $status -ne 0; or test -z "$owner"
        return 1
    end
    set -l euid (
        env -u APPIMAGE -u APPDIR -u ARGV0 -u LD_LIBRARY_PATH -u LD_PRELOAD \
            PATH=$trusted_path \
            $id_bin -u 2>/dev/null
    )
    if test $status -ne 0; or test "$owner" != "$euid"
        return 1
    end
    set -l resolved (
        env -u APPIMAGE -u APPDIR -u ARGV0 -u LD_LIBRARY_PATH -u LD_PRELOAD \
            PATH=$trusted_path \
            $realpath_bin "$sock" 2>/dev/null
    )
    if test $status -ne 0; or not _is_launchd_physical_path "$resolved"
        return 1
    end
    env -u APPIMAGE -u APPDIR -u ARGV0 -u LD_LIBRARY_PATH -u LD_PRELOAD \
        PATH=$trusted_path \
        SSH_AUTH_SOCK="$sock" \
        $ssh_add_bin -l >/dev/null 2>&1
    set -l add_status $status
    if not _agent_liveness_ok "$add_status"
        return 1
    end
    set -gx SSH_AUTH_SOCK "$sock"
    return 0
end

function _attach_darwin_ambient
    set -l platform (_gate_uname_s)
    if test "$platform" != Darwin
        return 1
    end
    if test "$_hook_NETWORK_TEST_HOOKS" = 1
        _attach_darwin_ambient_from_hooks
        return $status
    end
    _attach_darwin_ambient_from_system
    return $status
end

function _attach_agent
    set -l gpgconf_bin
    set gpgconf_bin (_optional_gpgconf)
    if test -n "$gpgconf_bin"
        set -l agent_sock
        set agent_sock (env -u APPIMAGE -u APPDIR -u ARGV0 -u LD_LIBRARY_PATH -u LD_PRELOAD PATH=$trusted_path $gpgconf_bin --list-dirs agent-ssh-socket 2>/dev/null)
        if test $status -eq 0; and test -n "$agent_sock"; and test -S "$agent_sock"
            set -gx SSH_AUTH_SOCK $agent_sock
            return 0
        end
        return 1
    end
    _attach_darwin_ambient
    return $status
end

set -l target (_resolve_env NUC_SSH_TARGET)
set -l target_status $status
if test $target_status -ne 0
    exit $target_status
end
set -l remote_user (_resolve_env NUC_SSH_USER)
set -l user_status $status
if test $user_status -ne 0
    exit $user_status
end
set -l identity (_resolve_env NUC_SSH_IDENTITY)
set -l identity_status $status
if test $identity_status -ne 0
    exit $identity_status
end
set -l remote_command (_resolve_env NUC_SSH_COMMAND)
set -l command_status $status
if test $command_status -ne 0
    exit $command_status
end
set -l probe 0
set -l cli_ssh 0

while test (count $argv) -gt 0
    switch $argv[1]
        case -h --help
            _gate_usage
            exit 0
        case --probe
            set probe 1
            set argv $argv[2..]
        case --target
            if test (count $argv) -lt 2
                echo "Missing value for --target." >&2
                exit 2
            end
            set cli_ssh 1
            set target $argv[2]
            set argv $argv[3..]
        case --user
            if test (count $argv) -lt 2
                echo "Missing value for --user." >&2
                exit 2
            end
            set cli_ssh 1
            set remote_user $argv[2]
            set argv $argv[3..]
        case --identity
            if test (count $argv) -lt 2
                echo "Missing value for --identity." >&2
                exit 2
            end
            set cli_ssh 1
            set identity $argv[2]
            set argv $argv[3..]
        case --command
            if test (count $argv) -lt 2
                echo "Missing value for --command." >&2
                exit 2
            end
            set cli_ssh 1
            set remote_command $argv[2]
            set argv $argv[3..]
        case '*'
            echo "Unknown or extra operand: $argv[1]" >&2
            _gate_usage
            exit 2
    end
end

if test "$probe" = 1
    if test "$cli_ssh" = 1
        echo "Probe mode does not accept SSH target parameters." >&2
        _gate_usage
        exit 2
    end
    if _attach_agent
        echo "ssh-agent: ready"
        exit 0
    end
    echo "ssh-agent: absent"
    exit 1
end

if test -z "$target"
    echo "Missing remote target." >&2
    _gate_usage
    exit 2
end
if test -z "$remote_user"
    echo "Missing remote user." >&2
    _gate_usage
    exit 2
end
if test -z "$identity"
    echo "Missing identity file." >&2
    _gate_usage
    exit 2
end
if test -z "$remote_command"
    echo "Missing bounded remote command." >&2
    _gate_usage
    exit 2
end

if string match -qr '[[:space:]]' -- $target
    echo "Remote target must not contain whitespace." >&2
    exit 2
end
if string match -q -- '-*' $target
    echo "Remote target must not be option-like." >&2
    exit 2
end
if string match -q '*@*' -- $target
    echo "Remote target must not include a user prefix." >&2
    exit 2
end
if not string match -qr '^[A-Za-z0-9][A-Za-z0-9.-]*$' -- $target
    echo "Remote target is not a valid hostname." >&2
    exit 2
end

if string match -qr '[[:space:]]' -- $remote_user
    echo "Remote user must not contain whitespace." >&2
    exit 2
end
if string match -q -- '-*' $remote_user
    echo "Remote user must not be option-like." >&2
    exit 2
end
if not string match -qr '^[A-Za-z_][A-Za-z0-9_-]*$' -- $remote_user
    echo "Remote user is not a valid login name." >&2
    exit 2
end

if string match -qr '[[:space:]]' -- $identity
    echo "Identity file path must not contain whitespace." >&2
    exit 2
end
if string match -q -- '-*' $identity
    echo "Identity file path must not be option-like." >&2
    exit 2
end
if not test -f $identity
    echo "Identity file is missing or not a regular file." >&2
    exit 2
end

if string match -q -- '-*' $remote_command
    echo "Remote command must not be option-like." >&2
    exit 2
end
if string match -qr '[\n;|&$`<>(){}]' -- $remote_command
    echo "Remote command contains unsupported shell metacharacters." >&2
    exit 2
end

set -l ssh_bin
set ssh_bin (_resolve_tool ssh $_hook_NETWORK_TEST_SSH)
or exit $status

_attach_agent
or true

set -l ssh_args
set -a ssh_args -o BatchMode=yes
set -a ssh_args -o RequestTTY=no
set -a ssh_args -o StrictHostKeyChecking=yes
set -a ssh_args -o IdentitiesOnly=yes
set -a ssh_args -o ForwardAgent=no
set -a ssh_args -o ClearAllForwardings=yes
set -a ssh_args -o ConnectTimeout=10
set -a ssh_args -o ServerAliveInterval=15
set -a ssh_args -o ServerAliveCountMax=2
set -a ssh_args -i $identity
set -a ssh_args $remote_user@$target
set -a ssh_args $remote_command

env -u APPIMAGE -u APPDIR -u ARGV0 -u LD_LIBRARY_PATH -u LD_PRELOAD \
    PATH=$trusted_path \
    $ssh_bin $ssh_args
exit $status
