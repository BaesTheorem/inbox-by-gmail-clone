#!/bin/bash
# Build the Inbox-branded notifiers, so macOS banners are attributed to "Inbox"
# (name, real app icon, its own row in System Settings → Notifications):
#
#  1. /Applications/Inbox Notifier.app: a rebranded copy of a UN notifier app
#     (MIST Notifier from the exobrain-harness repo, or $INBOX_UN_SOURCE). It
#     posts through UNUserNotificationCenter, the only API whose banner clicks
#     still run on macOS 26, so a click opens the thread. It must live in
#     /Applications: usernoted refuses UN callers from other paths.
#  2. ~/Library/Application Support/Inbox/Inbox Notifier.app: a rebranded
#     terminal-notifier, the fallback. Its banners show, but clicks do nothing
#     on macOS 26.
#
# app.py prefers (1), then the unbranded UN notifier, then (2).
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

# 5. The UN notifier: copy, rebrand, sign with a real identity when one exists.
UN_SRC="${INBOX_UN_SOURCE:-/Applications/MIST Notifier.app}"
UN_DEST="/Applications/Inbox Notifier.app"
UN_ID="${BASE_ID:+$BASE_ID.un-notifier}"
UN_ID="${UN_ID:-local.inbox-clone.un-notifier}"
if [ -d "$UN_SRC" ]; then
  rm -rf "$UN_DEST"
  cp -R "$UN_SRC" "$UN_DEST"
  UPLIST="$UN_DEST/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $UN_ID" "$UPLIST"
  /usr/libexec/PlistBuddy -c "Set :CFBundleName Inbox" "$UPLIST"
  /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName Inbox" "$UPLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string Inbox" "$UPLIST"
  UICON="$(/usr/libexec/PlistBuddy -c "Print :CFBundleIconFile" "$UPLIST" 2>/dev/null || echo AppIcon)"
  cp "$ICNS" "$UN_DEST/Contents/Resources/${UICON%.icns}.icns"
  IDENTITY=$(security find-identity -v -p codesigning 2>/dev/null \
    | awk -F'"' '/Apple Development/{print $2; exit}')
  codesign --force --sign "${IDENTITY:--}" "$UN_DEST"
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$UN_DEST"
  echo "Built: $UN_DEST ($UN_ID)"
  if [ "${1:-}" != "--no-test" ]; then
    # One-time permission prompt. Click Allow on the banner it raises.
    open -a "$UN_DEST" --args auth
    echo "Click Allow on the Inbox permission banner (clickable new-mail banners need it)."
  fi
else
  echo "No UN notifier at $UN_SRC: banner clicks will not work on macOS 26." >&2
  echo "Install MIST Notifier (exobrain-harness/mist-notifier) or set INBOX_UN_SOURCE." >&2
fi

# 6. Test banner (also triggers the one-time notification-permission grant)
if [ "${1:-}" != "--no-test" ]; then
  "$DEST/Contents/MacOS/terminal-notifier" \
    -title "Inbox" -message "Notifications are now Inbox-branded" -group setup-test
  echo "If no banner appeared, allow Inbox under System Settings → Notifications."
fi
