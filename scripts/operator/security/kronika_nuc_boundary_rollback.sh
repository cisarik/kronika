#!/bin/bash
# Fixed-surface recovery source. Installation and activation need host authority.
set -euo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
# Emit exactly one causal message; command diagnostics can disclose private data.
exec 3>&2 2>/dev/null
failure='operation failed'
trap 'rc=$?; if (( rc != 0 )); then printf "nsh-boundary-rollback: %s\n" "$failure" >&3; fi' EXIT
die() { failure=$1; exit 1; }

[[ $# == 2 ]] || die 'usage: snapshot|verify|restore ufw|sshd'
operation=$1 surface=$2
case $operation in snapshot|verify|restore) ;; *) die 'invalid operation' ;; esac
case $surface in
    ufw) paths=(etc/ufw/user.rules etc/ufw/user6.rules) ;;
    sshd) paths=(etc/ssh/sshd_config.d/00-kronika-hardening.conf etc/ssh/sshd_config.d/50-cloud-init.conf) ;;
    *) die 'invalid surface' ;;
esac

root=/ expected_uid=0 expected_gid=0
if [[ ${KRONIKA_NSH_TEST_ROOT+x} ]]; then
    root=$KRONIKA_NSH_TEST_ROOT
    [[ $root == /* && $root != / && $root != */ && $root != *//* ]] || die 'unsafe test root'
    [[ /$root/ != */../* && /$root/ != */./* ]] || die 'unsafe test root'
    [[ -d $root && ! -L $root ]] || die 'unsafe test root'
    [[ $(realpath -e -- "$root") == "$root" ]] || die 'unsafe test root'
    expected_uid=$EUID expected_gid=$(id -g)
    [[ $(stat -c '%u:%g:%a' -- "$root") == "$expected_uid:$expected_gid:700" ]] || die 'unsafe test root'
else
    (( EUID == 0 )) || die 'root required'
fi
prefix=${root%/}

# Directories within the selected boundary must be real and safely owned.
# Ancestors above an explicit private synthetic root are outside that test
# boundary (workstation temporary mounts need not be root-owned).
check_ancestors() {
    local path=$1 ancestor= component uid gid mode
    local -a components
    IFS=/ read -r -a components <<< "${path#/}"
    for component in "${components[@]}"; do
        ancestor+=/$component
        [[ -d $ancestor && ! -L $ancestor ]] || die 'unsafe or missing directory'
        if [[ $root != / && $root == "$ancestor/"* ]]; then continue; fi
        read -r uid gid mode < <(stat -c '%u %g %a' -- "$ancestor")
        [[ $uid == 0 || $uid == "$expected_uid" ]] || die 'unexpected directory owner'
        if (( (8#$mode & 07022) != 0 )); then
            die 'unsafe directory mode'
        fi
    done
}

check_private_dir() {
    [[ -d $1 && ! -L $1 ]] || die 'unsafe or missing snapshot directory'
    [[ $(stat -c '%u:%g:%a' -- "$1") == "$expected_uid:$expected_gid:700" ]] || die 'unexpected snapshot directory metadata'
}

check_private_file() {
    [[ -f $1 && ! -L $1 ]] || die 'unsafe or missing snapshot file'
    [[ $(stat -c '%u:%g:%a:%h' -- "$1") == "$expected_uid:$expected_gid:600:1" ]] || die 'unexpected snapshot file metadata'
}

safe_surface_mode() {
    case $1 in 600|640|644) return 0 ;; *) die 'unexpected surface mode' ;; esac
}

# Missing SSH drop-ins have explicit absent records; UFW inputs are mandatory.
# Restore can recreate a removed recorded file, but never a missing directory.
check_live() {
    local rel=$1 allow_missing=$2 path=$prefix/$1 uid gid mode links
    check_ancestors "${path%/*}"
    [[ ! -L $path ]] || die 'surface symlink refused'
    if [[ ! -e $path ]]; then
        [[ $allow_missing == yes ]] || die 'missing surface file'
        return
    fi
    [[ -f $path ]] || die 'surface is not a regular file'
    read -r uid gid mode links < <(stat -c '%u %g %a %h' -- "$path")
    [[ $uid == "$expected_uid" && $gid == "$expected_gid" ]] || die 'unexpected surface owner'
    [[ $links == 1 ]] || die 'surface hard link refused'
    safe_surface_mode "$mode"
}

failure='state preparation failed'
check_ancestors "$prefix/var/lib"
state=$prefix/var/lib/kronika-host-hardening
if [[ ! -e $state && ! -L $state ]]; then
    [[ $operation == snapshot ]] || die 'missing recovery state'
    mkdir -m 0700 -- "$state"
fi
check_private_dir "$state"
lock=$state/lock
if [[ ! -e $lock && ! -L $lock ]]; then
    (set -o noclobber; : > "$lock") || die 'lock creation conflict'
fi
check_private_file "$lock"
exec 9<> "$lock"
flock -n 9 || die 'operation lock conflict'

# Never silently adopt abandoned partial snapshots or unknown state entries.
shopt -s dotglob nullglob
for entry in "$state"/*; do
    case ${entry##*/} in
        lock) ;;
        ufw|sshd) check_private_dir "$entry" ;;
        .pending-ufw|.pending-sshd) die 'incomplete snapshot conflict' ;;
        *) die 'unexpected recovery state entry' ;;
    esac
done
snapshot=$state/$surface
pending=$state/.pending-$surface
declare -a modes=() owners=() groups=() digests=() presence=()

digest() {
    local output
    output=$(sha256sum -- "$1")
    printf '%s' "${output%% *}"
}

verify_snapshot() {
    local base=$1 index rel present mode uid gid hash extra line path item component canonical_hash
    local -a lines components
    local -A inventory=([manifest]=file [files]=dir)
    check_private_dir "$base"
    check_private_file "$base/manifest"
    # Bounded schema, never source/eval the recorded metadata.
    [[ $(stat -c %s -- "$base/manifest") -le 2048 ]] || die 'invalid snapshot manifest'
    mapfile -t lines < "$base/manifest"
    [[ ${#lines[@]} == 3 && ${lines[0]} == "NSH1|$surface" ]] || die 'invalid snapshot manifest'
    # Bash cannot represent NUL bytes. Compare canonical bytes before trusting
    # parsed fields so discarded bytes and a missing final newline fail closed.
    canonical_hash=$(printf '%s\n' "${lines[@]}" | sha256sum)
    [[ ${canonical_hash%% *} == "$(digest "$base/manifest")" ]] || die 'noncanonical snapshot manifest'
    for index in 0 1; do
        line=${lines[index+1]}
        IFS='|' read -r rel present mode uid gid hash extra <<< "$line"
        [[ $rel == "${paths[index]}" && -z $extra ]] || die 'invalid snapshot path'
        [[ $line == "$rel|$present|$mode|$uid|$gid|$hash" ]] || die 'invalid snapshot record'
        case $present in
            present)
                safe_surface_mode "$mode"
                [[ $uid == "$expected_uid" && $gid == "$expected_gid" && $hash =~ ^[0-9a-f]{64}$ ]] || die 'invalid snapshot metadata'
                path=$base/files/$rel
                item=files
                check_private_dir "$base/$item"
                IFS=/ read -r -a components <<< "${rel%/*}"
                for component in "${components[@]}"; do
                    item+=/$component
                    check_private_dir "$base/$item"
                done
                check_private_file "$path"
                [[ $(digest "$path") == "$hash" ]] || die 'snapshot checksum mismatch'
                inventory[files/$rel]=file
                item=files/${rel%/*}
                while [[ $item != files ]]; do
                    inventory[$item]=dir
                    item=${item%/*}
                done
                ;;
            absent)
                [[ $surface == sshd && $mode == - && $uid == - && $gid == - && $hash == - ]] || die 'invalid absent record'
                ;;
            *) die 'invalid snapshot presence' ;;
        esac
        presence[index]=$present modes[index]=$mode owners[index]=$uid groups[index]=$gid digests[index]=$hash
    done
    # Expand only inside the validated private snapshot; no live-path expansion.
    local -a queue=("$base")
    while (( ${#queue[@]} )); do
        path=${queue[0]}
        queue=("${queue[@]:1}")
        for item in "$path"/*; do
            rel=${item#"$base/"}
            case ${inventory[$rel]-} in
                dir) check_private_dir "$item"; queue+=("$item") ;;
                file) check_private_file "$item" ;;
                *) die 'unexpected snapshot entry' ;;
            esac
        done
    done
    for rel in "${!inventory[@]}"; do
        if [[ ${inventory[$rel]} == dir ]]; then check_private_dir "$base/$rel"; fi
    done
}

case $operation in
    snapshot)
        [[ ! -e $snapshot && ! -L $snapshot ]] || die 'snapshot already exists; stale or conflicting state'
        failure='surface preflight failed'
        allow_missing=no
        [[ $surface != sshd ]] || allow_missing=yes
        for rel in "${paths[@]}"; do check_live "$rel" "$allow_missing"; done
        failure='snapshot creation failed'
        mkdir -m 0700 -- "$pending"
        mkdir -m 0700 -- "$pending/files"
        printf 'NSH1|%s\n' "$surface" > "$pending/manifest"
        for rel in "${paths[@]}"; do
            live=$prefix/$rel
            if [[ ! -e $live ]]; then
                printf '%s|absent|-|-|-|-\n' "$rel" >> "$pending/manifest"
                continue
            fi
            metadata=$(stat -c '%a|%u|%g' -- "$live")
            hash=$(digest "$live")
            mkdir -p -m 0700 -- "$pending/files/${rel%/*}"
            cat -- "$live" > "$pending/files/$rel"
            [[ $(stat -c '%a|%u|%g' -- "$live") == "$metadata" && $(digest "$live") == "$hash" ]] || die 'surface changed during snapshot'
            printf '%s|present|%s|%s\n' "$rel" "$metadata" "$hash" >> "$pending/manifest"
        done
        verify_snapshot "$pending"
        mv -T -- "$pending" "$snapshot"
        ;;
    verify)
        failure='snapshot verification failed'
        verify_snapshot "$snapshot"
        ;;
    restore)
        failure='snapshot verification failed'
        verify_snapshot "$snapshot"
        # Preflight every destination before changing any live file.
        failure='restore preflight failed'
        for rel in "${paths[@]}"; do
            check_live "$rel" yes
            next=$prefix/${rel%/*}/.nsh-boundary-rollback-${rel##*/}.next
            [[ ! -e $next && ! -L $next ]] || die 'restore staging conflict'
        done
        failure='restore staging failed'
        for index in 0 1; do
            [[ ${presence[index]} == present ]] || continue
            rel=${paths[index]}
            next=$prefix/${rel%/*}/.nsh-boundary-rollback-${rel##*/}.next
            (set -o noclobber; cat -- "$snapshot/files/$rel" > "$next")
            chown -- "${owners[index]}:${groups[index]}" "$next"
            chmod -- "${modes[index]}" "$next"
            [[ $(digest "$next") == "${digests[index]}" ]] || die 'restore staging checksum mismatch'
        done
        failure='restore replacement failed; retain snapshot and inspect exact staging files'
        for index in 0 1; do
            rel=${paths[index]}
            live=$prefix/$rel
            check_live "$rel" yes
            if [[ ${presence[index]} == present ]]; then
                next=$prefix/${rel%/*}/.nsh-boundary-rollback-${rel##*/}.next
                mv -T -- "$next" "$live"
                [[ $(stat -c '%a:%u:%g' -- "$live") == "${modes[index]}:${owners[index]}:${groups[index]}" && $(digest "$live") == "${digests[index]}" ]] || die 'restored file verification failed'
            elif [[ -e $live ]]; then
                rm -- "$live"
            fi
        done
        ;;
esac
printf 'nsh-boundary-rollback: %s %s complete\n' "$operation" "$surface"
