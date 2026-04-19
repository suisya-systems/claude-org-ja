#!/bin/bash
# wezterm CLI wrapper — resolves path from environment or PATH
if [ -n "$WEZTERM_EXECUTABLE_DIR" ]; then
  WEZTERM="${WEZTERM_EXECUTABLE_DIR}/wezterm"
  # Windows: try .exe suffix
  [ -f "${WEZTERM}.exe" ] && WEZTERM="${WEZTERM}.exe"
else
  WEZTERM="wezterm"
fi

if ! command -v "$WEZTERM" &>/dev/null && [ ! -f "$WEZTERM" ]; then
  echo "ERROR: wezterm not found (WEZTERM_EXECUTABLE_DIR=${WEZTERM_EXECUTABLE_DIR:-unset})" >&2
  exit 1
fi
exec "$WEZTERM" "$@"
