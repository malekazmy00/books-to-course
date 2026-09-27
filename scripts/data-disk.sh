#!/usr/bin/env bash
# b2c data disk (D-020): check / initialise the directories on the dedicated VHDX.
#   scripts/data-disk.sh check   -> exit 1 unless the dedicated disk is mounted with our marker
#   scripts/data-disk.sh init    -> (once) create marker + per-service dirs with the right owners
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=$(grep -E '^B2C_DATA_DIR=' .env | cut -d= -f2 | awk '{print $1}')
DIR=${DIR:-/mnt/wsl/b2cdata}
MARK="$DIR/.b2c-data-disk"

mounted_separately() {
  # must be its own mount (not a directory on the WSL root disk)
  mountpoint -q "$DIR" && [ "$(findmnt -no SOURCE "$DIR")" != "$(findmnt -no SOURCE /)" ]
}

case "${1:-check}" in
  check)
    if mounted_separately && [ -f "$MARK" ]; then
      echo "data disk OK: $DIR ($(findmnt -no SOURCE,FSTYPE "$DIR"))"
    else
      echo "ERROR: b2c data disk is not mounted at $DIR (or marker missing)." >&2
      echo "Windows (admin): wsl --mount --vhd F:\\wsl-b2c\\b2c-data.vhdx --name b2cdata" >&2
      exit 1
    fi ;;
  init)
    mounted_separately || { echo "refusing: $DIR is not a separate mount" >&2; exit 1; }
    # service  owner
    while read -r d owner; do
      mkdir -p "$DIR/$d"; chown "$owner" "$DIR/$d"
    done <<'EOF'
postgres 999:999
clickhouse/data 101:101
clickhouse/logs 101:101
minio 65532:65532
redis 999:999
prometheus 65534:65534
grafana 472:0
EOF
    chown 101:101 "$DIR/clickhouse"
    date -u +%FT%TZ > "$MARK"
    ls -ln "$DIR" ;;
  *) echo "usage: $0 check|init" >&2; exit 2 ;;
esac
