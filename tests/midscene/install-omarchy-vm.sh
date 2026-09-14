#!/bin/bash
set -euo pipefail

# This is deliberately a real Omarchy installation, not an Arch container
# dressed up with a few Omarchy files. The official ISO acceptance harness
# drives the installer through QEMU screenshots, OCR and virtual keystrokes.
readonly ISO_VERSION="4.0.3"
readonly ISO_SHA256="03d60bc74306dca51f96e1a84b690871d8d606826b260edd0208962da8507d14"
readonly ISO_HARNESS_SHA="a23f8d464dcb0616a61bfaa8026e23d0533da209"
readonly WORK_DIR="$PWD/.midscene-omarchy"
readonly ISO_PATH="$WORK_DIR/omarchy-${ISO_VERSION}.iso"
readonly HARNESS_DIR="$WORK_DIR/omarchy-iso"

if [[ ! -c /dev/kvm ]]; then
  echo "::error::This GitHub runner does not expose /dev/kvm; a real Omarchy VM cannot be started."
  exit 1
fi

sudo chmod 0666 /dev/kvm

# The ISO is about 6 GB and the installed qcow2 image is sparse but sizeable.
# GitHub's image includes large SDKs that this isolated job does not need.
sudo rm -rf /usr/local/lib/android /usr/share/dotnet /opt/ghc
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  curl git imagemagick ovmf qemu-system-x86 qemu-utils socat \
  tesseract-ocr tesseract-ocr-eng

mkdir -p "$WORK_DIR"
df -h "$WORK_DIR"

curl --fail --location --retry 5 --retry-all-errors \
  "https://iso.omarchy.org/omarchy-${ISO_VERSION}.iso" \
  --output "$ISO_PATH"
printf '%s  %s\n' "$ISO_SHA256" "$ISO_PATH" | sha256sum --check --strict

git init --quiet "$HARNESS_DIR"
git -C "$HARNESS_DIR" remote add origin https://github.com/omacom/omarchy-iso.git
git -C "$HARNESS_DIR" fetch --quiet --depth 1 origin "$ISO_HARNESS_SHA"
git -C "$HARNESS_DIR" checkout --quiet --detach FETCH_HEAD

# The official harness names Arch paths and its package helper. Adapt only
# those host-side dependencies; the guest still boots and installs the exact
# verified official ISO.
sudo mkdir -p /usr/share/edk2/x64
sudo ln -sf /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/edk2/x64/OVMF_CODE.4m.fd
sudo ln -sf /usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/edk2/x64/OVMF_VARS.4m.fd

readonly SHIM_DIR="$(mktemp -d)"
trap 'rm -rf "$SHIM_DIR"' EXIT
printf '#!/bin/sh\nexit 0\n' >"$SHIM_DIR/omarchy-pkg-add"
printf '#!/bin/sh\nexec convert "$@"\n' >"$SHIM_DIR/magick"
chmod 0755 "$SHIM_DIR/omarchy-pkg-add" "$SHIM_DIR/magick"

PATH="$SHIM_DIR:$PATH" "$HARNESS_DIR/bin/omarchy-iso-test" \
  "$ISO_PATH" \
  --install-only \
  --memory 4096 \
  --timeout 3000 \
  --no-preview

test -s "$HARNESS_DIR/test-runs/omarchy-${ISO_VERSION}/base.qcow2"
echo "PASS: a complete Omarchy ${ISO_VERSION} base VM was installed on the GitHub runner"

