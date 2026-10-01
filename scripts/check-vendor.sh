#!/bin/sh
# Fails if any vendored upstream project (VDO.Ninja, handshake server) has been
# modified, or is not at the commit pinned by this repository.
set -eu
cd "$(dirname "$0")/.."
fail=0
for sm in vendor/vdo.ninja vendor/websocket_server; do
  if [ ! -e "$sm/.git" ]; then
    echo "check-vendor: $sm is not initialised (run: git submodule update --init)"; fail=1; continue
  fi
  pinned=$(git ls-files -s "$sm" | awk '{print $2}')
  actual=$(git -C "$sm" rev-parse HEAD)
  if [ -n "$pinned" ] && [ "$pinned" != "$actual" ]; then
    echo "check-vendor: $sm is at $actual but the pinned commit is $pinned"; fail=1
  fi
  dirty=$(git -C "$sm" status --porcelain --untracked-files=all)
  if [ -n "$dirty" ]; then
    echo "check-vendor: $sm has local changes (vendored code must never be edited):"; echo "$dirty" | head -20; fail=1
  fi
  echo "check-vendor: $sm $(git -C "$sm" describe --tags --always 2>/dev/null) $actual"
done
[ "$fail" -eq 0 ] && echo "check-vendor: OK"
exit "$fail"
