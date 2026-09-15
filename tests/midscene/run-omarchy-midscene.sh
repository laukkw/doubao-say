#!/bin/bash
set -euo pipefail

readonly ROOT_DIR="$PWD"
readonly WORK_DIR="$ROOT_DIR/.midscene-omarchy"
readonly HARNESS_DIR="$WORK_DIR/omarchy-iso"
readonly ISO_PATH="$WORK_DIR/omarchy-4.0.3.iso"
readonly BASE_DIR="$HARNESS_DIR/test-runs/omarchy-4.0.3"
readonly SSH_KEY="$BASE_DIR/id_ed25519"
readonly SSH_PORT=2222
readonly PLUGIN_DIR="/home/omarchy/.config/omarchy/plugins/md.lifeos.doubao-say"
readonly SHIM_DIR="$(mktemp -d)"

VM_PID=""

cleanup() {
  if [[ -n $VM_PID ]]; then
    kill "$VM_PID" 2>/dev/null || true
  fi
  rm -rf "$SHIM_DIR"
}
trap cleanup EXIT

ssh_guest() {
  ssh -i "$SSH_KEY" -p "$SSH_PORT" \
    -o BatchMode=yes \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o ConnectTimeout=10 \
    -o LogLevel=ERROR \
    omarchy@127.0.0.1 "$@"
}

ssh_session() {
  local command="$1"
  ssh_guest "export XDG_RUNTIME_DIR=/run/user/\$(id -u); \
    export DBUS_SESSION_BUS_ADDRESS=unix:path=\$XDG_RUNTIME_DIR/bus; \
    export HYPRLAND_INSTANCE_SIGNATURE=\$(ls -t \$XDG_RUNTIME_DIR/hypr | head -1); \
    export WAYLAND_DISPLAY=\$(find \$XDG_RUNTIME_DIR -maxdepth 1 -name 'wayland-*' ! -name '*.lock' -printf '%f\\n' | head -1); \
    export OMARCHY_PATH=/usr/share/omarchy; \
    export PATH=\$OMARCHY_PATH/bin:\$PATH; \
    $command" </dev/null
}

test -s "$ISO_PATH"
test -s "$BASE_DIR/base.qcow2"
test -s "$SSH_KEY"

# Reuse the pinned official harness's VM, login and session routines. Replace
# only its post-login acceptance body so this job can hand the live desktop to
# Midscene instead of running Omarchy's unrelated upstream acceptance suite.
readonly SESSION_HARNESS="$HARNESS_DIR/bin/omarchy-midscene-session"
cp "$HARNESS_DIR/bin/omarchy-iso-test" "$SESSION_HARNESS"
sed -i '/^acceptance_phase() {/,/^}/c\
acceptance_phase() {\
  log "Booting Omarchy session for Doubao Say Midscene E2E"\
  qemu-img create -f qcow2 -b "$BASE_DISK" -F qcow2 "$RUN_DIR/run.qcow2" >/dev/null\
  start_vm "$RUN_DIR/run.qcow2" "$RUN_DIR/serial.log"\
  establish_session\
  capture_console "success-session-ready-for-midscene"\
}' "$SESSION_HARNESS"

printf '#!/bin/sh\nexit 0\n' >"$SHIM_DIR/omarchy-pkg-add"
printf '#!/bin/sh\nexec convert "$@"\n' >"$SHIM_DIR/magick"
chmod 0755 "$SHIM_DIR/omarchy-pkg-add" "$SHIM_DIR/magick"

PATH="$SHIM_DIR:$PATH" "$SESSION_HARNESS" "$ISO_PATH" \
  --reuse-base \
  --keep-running \
  --memory 4096 \
  --no-preview

readonly RUN_DIR="$(find "$BASE_DIR/runs" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
VM_PID="$(cat "$RUN_DIR/qemu.pid")"
kill -0 "$VM_PID"

# Put this exact checkout at its real Omarchy plugin location, validate the
# manifest with Omarchy, then launch the deterministic GTK fixture in the
# guest's actual Hyprland session.
tar -C "$ROOT_DIR" --exclude='__pycache__' -cf - \
  LICENSE README.md manifest.json install.sh install-user.sh setup-omarchy.sh start.sh \
  omarchy src tests/midscene/gtk_fixture.py | \
  ssh_guest "rm -rf '$PLUGIN_DIR' && mkdir -p '$PLUGIN_DIR' && tar -C '$PLUGIN_DIR' -xf -"

ssh_session "omarchy plugin validate '$PLUGIN_DIR'"
ssh_session "rm -rf /tmp/doubao-midscene-config; \
  mkdir -p /tmp/doubao-midscene-config; \
  export PYTHONPATH='$PLUGIN_DIR/src'; \
  export XDG_CONFIG_HOME=/tmp/doubao-midscene-config; \
  export PYTHONDONTWRITEBYTECODE=1; \
  setsid -f python3 '$PLUGIN_DIR/tests/midscene/gtk_fixture.py' \
    >/tmp/doubao-midscene-fixture.log 2>&1"

for _attempt in $(seq 1 30); do
  if ssh_session "grep -q 'READY: synthetic Doubao Say GTK fixture' /tmp/doubao-midscene-fixture.log && \
      hyprctl -j clients | jq -e '[.[] | select(.title == \"Doubao Say\")] | length == 1'" \
      >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

ssh_session "grep -q 'READY: synthetic Doubao Say GTK fixture' /tmp/doubao-midscene-fixture.log"
ssh_session "hyprctl -j clients | jq -e '[.[] | select(.title == \"Doubao Say\")] | length == 1'"

OMARCHY_E2E=true npm --prefix tests/midscene test -- \
  e2e/omarchy-onboarding.test.ts

ssh_session "hyprctl -j clients | jq -e '[.[] | select(.title == \"Doubao Say\")] | length == 1'"
