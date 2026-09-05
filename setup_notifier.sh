#!/bin/bash
# Build "Inbox Notifier.app" — a rebranded copy of terminal-notifier — so macOS
# banners are attributed to "Inbox" (name, real app icon, its own row in System
# Settings → Notifications) instead of terminal-notifier/Terminal. app.py prefers
# this bundle automatically when it exists; without it, banners fall back to plain
# terminal-notifier with the favicon pasted in.
#
# The bundle id is derived at build time from the installed launcher
# (/Applications/Inbox.app) and never hardcoded here: a machine-specific id can
# embed a real name, which must never land in this public repo.
#
# Idempotent. Rerun after `brew upgrade terminal-notifier` to pick up the new
# binary. Pass --no-test to skip the test banner at the end.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DEST_DIR="$HOME/Library/Application Support/Inbox"
DEST="$DEST_DIR/Inbox Notifier.app"
LAUNCHER="/Applications/Inbox.app"

# 1. Locate the stock terminal-notifier.app (brew keg or a direct .app install)
TN_BIN="$(command -v terminal-notifier || true)"
[ -n "$TN_BIN" ] || { echo "terminal-notifier not found — brew install terminal-notifier" >&2; exit 1; }
TN_REAL="$(readlink -f "$TN_BIN")"
if [[ "$TN_REAL" == *".app/Contents/MacOS/"* ]]; then
  TN_APP="${TN_REAL%/Contents/MacOS/*}"          # binary lives inside the bundle
else
  TN_APP="${TN_REAL%/bin/*}/terminal-notifier.app"  # brew: .app is a sibling of bin/
fi
[ -d "$TN_APP" ] || { echo "couldn't locate terminal-notifier.app near $TN_REAL" >&2; exit 1; }

# 2. Pick the icon: the launcher's real icon, else one built from the repo favicon
ICNS="$LAUNCHER/Contents/Resources/icon.icns"
if [ ! -f "$ICNS" ]; then
  TMP="$(mktemp -d)"
  mkdir -p "$TMP/icon.iconset"
  for sz in 16 32 64 128 256 512; do
    sips -z "$sz" "$sz" "$HERE/static/favicon.png" --out "$TMP/icon.iconset/icon_${sz}x${sz}.png" >/dev/null
  done
  iconutil -c icns "$TMP/icon.iconset" -o "$TMP/icon.icns"
  ICNS="$TMP/icon.icns"
fi

# 3. Pick the bundle id: <launcher id>.notifier, else a generic local id
BASE_ID="$(defaults read "$LAUNCHER/Contents/Info" CFBundleIdentifier 2>/dev/null || true)"
NOTIFIER_ID="${BASE_ID:+$BASE_ID.notifier}"
NOTIFIER_ID="${NOTIFIER_ID:-local.inbox-clone.notifier}"

# 4. Copy + rebrand + re-sign
mkdir -p "$DEST_DIR"
rm -rf "$DEST"
cp -R "$TN_APP" "$DEST"
PLIST="$DEST/Contents/Info.plist"
plist_set() {
  /usr/libexec/PlistBuddy -c "Set :$1 $2" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :$1 string $2" "$PLIST"
}
plist_set CFBundleIdentifier "$NOTIFIER_ID"
plist_set CFBundleName Inbox
cp "$ICNS" "$DEST/Contents/Resources/icon.icns"
plist_set CFBundleIconFile icon.icns
rm -f "$DEST/Contents/Resources/Terminal.icns"
codesign --force --deep --sign - "$DEST" 2>/dev/null
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$DEST"

echo "Built: $DEST ($NOTIFIER_ID)"
echo "Restart the Inbox app so it picks up the branded notifier."

# 5. Test banner (also triggers the one-time notification-permission grant)
if [ "${1:-}" != "--no-test" ]; then
  "$DEST/Contents/MacOS/terminal-notifier" \
    -title "Inbox" -message "Notifications are now Inbox-branded" -group setup-test
  echo "If no banner appeared, allow Inbox under System Settings → Notifications."
fi
