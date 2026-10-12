#!/bin/sh
# herdr バイナリの build 時取得（設計: docs/design/org-docker-distribution.md §9）。
# - バージョンと sha256 を本リポジトリ側に pin する（update manifest
#   https://herdr.dev/latest.json は URL 文字列のみで checksum を提供しないため、
#   pin 済み GitHub release URL + 実測 sha256 で検証する。実測 2026-10-12）。
# - self-update（herdr update）は image 不変性に反するため使わない。更新は
#   pin を上げて image rebuild で行う（runtime pin と同じ運用、設計 §7.7）。
set -eu

TARGETARCH="${1:?usage: install-herdr.sh <amd64|arm64>}"

HERDR_VERSION="${HERDR_VERSION:-0.9.3}"
# バージョンを上げるときは両アーキの sha256 を実測して更新すること:
#   curl -fsSL -o h https://github.com/ogulcancelik/herdr/releases/download/v<V>/herdr-linux-<arch> && sha256sum h
HERDR_SHA256_X86_64="${HERDR_SHA256_X86_64:-18a8dc65f1c2fa485884344356dea1cfd911c6f06cf46fa78e193f4087f4dba7}"
HERDR_SHA256_AARCH64="${HERDR_SHA256_AARCH64:-4de7aa3e25678812e92960de64f7c2aaa1bca1f0f80a3c5e559837e231e1f5c0}"

case "${TARGETARCH}" in
    amd64) HERDR_ARCH=x86_64;  sha="${HERDR_SHA256_X86_64}" ;;
    arm64) HERDR_ARCH=aarch64; sha="${HERDR_SHA256_AARCH64}" ;;
    *) echo "install-herdr: unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;;
esac

url="https://github.com/ogulcancelik/herdr/releases/download/v${HERDR_VERSION}/herdr-linux-${HERDR_ARCH}"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

echo "install-herdr: downloading ${url}"
curl -fsSL "${url}" -o "${tmp}/herdr"
echo "${sha}  ${tmp}/herdr" | sha256sum -c - || {
    echo "install-herdr: FAIL — sha256 mismatch for herdr ${HERDR_VERSION} linux/${HERDR_ARCH}" >&2
    echo "  (pin と release の不一致。herdr 同梱を諦める場合は --build-arg INSTALL_HERDR=0)" >&2
    exit 1
}

install -m 0755 "${tmp}/herdr" /usr/local/bin/herdr
/usr/local/bin/herdr --version
echo "install-herdr: done"
