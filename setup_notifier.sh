#!/bin/bash
# Build /Applications/Inbox Notifier.app, so macOS banners are attributed to
# "Inbox" (name, real app icon, its own row in System Settings → Notifications)
# and a click opens the thread.
#
# It is a rebranded copy of a UN notifier app: MIST Notifier from the
# exobrain-harness repo (mist-notifier/), or $INBOX_UN_SOURCE, any app that takes
# the same JSON spec and `post <path>` CLI. It must post through
# UNUserNotificationCenter, because on macOS 26 a click on a legacy
# NSUserNotification banner (terminal-notifier) never runs its action. It must
# live in /Applications, because usernoted refuses UN callers from other paths.
#
# The bundle id is <launcher id>.notifier, derived from /Applications/Inbox.app
# and never hardcoded: a machine-specific id can embed a real name, which must
# never land in this public repo. Notification permission is stored per bundle
# id, so a rebuild keeps the grant. Older versions of this script put a
# terminal-notifier copy with the same id in ~/Library/Application Support/Inbox;
# it is removed so the two bundles do not compete for the id.
#
# Idempotent. Pass --no-test to skip the test banner at the end.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LAUNCHER="/Applications/Inbox.app"
DEST="/Applications/Inbox Notifier.app"
LEGACY="$HOME/Library/Application Support/Inbox/Inbox Notifier.app"
SRC="${INBOX_UN_SOURCE:-/Applications/MIST Notifier.app}"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

[ -d "$SRC" ] || {
  echo "No UN notifier at $SRC. Install MIST Notifier (exobrain-harness/mist-notifier)" >&2
  echo "or set INBOX_UN_SOURCE. Without one, banners cannot be clicked on macOS 26." >&2
  exit 1; }
if pgrep -f "$DEST/Contents/MacOS" >/dev/null 2>&1; then
  echo "Inbox Notifier is running. Not rebuilding under it; re-run in a minute." >&2
  exit 1
fi

# 1. Icon: the launcher's real icon, else one built from the repo favicon
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

# 2. Bundle id
BASE_ID="$(defaults read "$LAUNCHER/Contents/Info" CFBundleIdentifier 2>/dev/null || true)"
NOTIFIER_ID="${BASE_ID:+$BASE_ID.notifier}"
NOTIFIER_ID="${NOTIFIER_ID:-local.inbox-clone.notifier}"

# 3. Retire the legacy terminal-notifier copy that held the same id
if [ -d "$LEGACY" ]; then
  "$LSREGISTER" -u "$LEGACY" 2>/dev/null || true
  rm -rf "$LEGACY"
  echo "Removed legacy terminal-notifier copy: $LEGACY"
fi

# 4. Copy, rebrand, sign with a real identity when one exists, register
rm -rf "$DEST"
cp -R "$SRC" "$DEST"
PLIST="$DEST/Contents/Info.plist"
plist_set() {
  /usr/libexec/PlistBuddy -c "Set :$1 $2" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :$1 string $2" "$PLIST"
}
plist_set CFBundleIdentifier "$NOTIFIER_ID"
plist_set CFBundleName Inbox
plist_set CFBundleDisplayName Inbox
ICON_FILE="$(/usr/libexec/PlistBuddy -c "Print :CFBundleIconFile" "$PLIST" 2>/dev/null || echo AppIcon)"
cp "$ICNS" "$DEST/Contents/Resources/${ICON_FILE%.icns}.icns"
IDENTITY=$(security find-identity -v -p codesigning 2>/dev/null \
  | awk -F'"' '/Apple Development/{print $2; exit}')
codesign --force --sign "${IDENTITY:--}" "$DEST"
"$LSREGISTER" -f "$DEST"
echo "Built: $DEST ($NOTIFIER_ID)"

# 5. Test banner. On a first build this raises the one-time permission prompt.
if [ "${1:-}" != "--no-test" ]; then
  SPEC="$(mktemp -t inbox-notify).json"
  echo '{"title":"Inbox","body":"Notifications are Inbox-branded. Click to open Inbox.","link":"inboxclone://","id":"inbox-setup-test"}' > "$SPEC"
  open -n -a "$DEST" --args post "$SPEC"
  for _ in $(seq 1 30); do [ -f "$SPEC.result" ] && break; sleep 0.2; done
  if grep -q '"ok":true' "$SPEC.result" 2>/dev/null; then
    echo "Test banner sent."
  else
    echo "Not delivered ($(cat "$SPEC.result" 2>/dev/null || echo no result))."
    echo "Allow the prompt, or turn on Inbox in System Settings → Notifications."
  fi
  rm -f "$SPEC" "$SPEC.result"
fi
