#!/bin/bash
# Build "Inbox Link Handler.app" from link_handler.applescript and register it
# for inboxclone:// (Things backlinks, banner clicks) and mailto: (so Inbox can be
# the default email app). The launcher (/Applications/Inbox.app) is a shell
# script and cannot receive URL Apple Events, so this hidden applet takes them.
#
# The bundle id is derived from the installed launcher, never hardcoded: a
# machine-specific id can embed a real name, which must not land in this repo.
#
#   ./setup_link_handler.sh             build + register
#   ./setup_link_handler.sh --default   also make Inbox the default email app
#
# Idempotent. macOS can show a dialog to confirm the default-app change.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DEST_DIR="$HOME/Library/Application Support/Inbox"
DEST="$DEST_DIR/Inbox Link Handler.app"
LAUNCHER="/Applications/Inbox.app"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

BASE_ID="$(defaults read "$LAUNCHER/Contents/Info" CFBundleIdentifier 2>/dev/null || true)"
HANDLER_ID="${BASE_ID:+${BASE_ID}link}"
HANDLER_ID="${HANDLER_ID:-local.inbox-clone.link}"

mkdir -p "$DEST_DIR"
rm -rf "$DEST"
osacompile -o "$DEST" "$HERE/link_handler.applescript"

PLIST="$DEST/Contents/Info.plist"
PB=/usr/libexec/PlistBuddy
$PB -c "Add :CFBundleIdentifier string $HANDLER_ID" "$PLIST" 2>/dev/null \
  || $PB -c "Set :CFBundleIdentifier $HANDLER_ID" "$PLIST"
$PB -c "Add :CFBundleName string 'Inbox Link Handler'" "$PLIST" 2>/dev/null \
  || $PB -c "Set :CFBundleName 'Inbox Link Handler'" "$PLIST"
$PB -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null || $PB -c "Set :LSUIElement true" "$PLIST"
$PB -c "Delete :CFBundleURLTypes" "$PLIST" 2>/dev/null || true
$PB -c "Add :CFBundleURLTypes array" "$PLIST"
$PB -c "Add :CFBundleURLTypes:0 dict" "$PLIST"
$PB -c "Add :CFBundleURLTypes:0:CFBundleURLName string $HANDLER_ID" "$PLIST"
$PB -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes array" "$PLIST"
$PB -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string inboxclone" "$PLIST"
$PB -c "Add :CFBundleURLTypes:1 dict" "$PLIST"
$PB -c "Add :CFBundleURLTypes:1:CFBundleURLName string 'Email Address URL'" "$PLIST"
$PB -c "Add :CFBundleURLTypes:1:CFBundleURLSchemes array" "$PLIST"
$PB -c "Add :CFBundleURLTypes:1:CFBundleURLSchemes:0 string mailto" "$PLIST"

# The launcher's icon, so the default-mail picker shows the Inbox glyph.
if [ -f "$LAUNCHER/Contents/Resources/icon.icns" ]; then
  cp "$LAUNCHER/Contents/Resources/icon.icns" "$DEST/Contents/Resources/applet.icns"
fi

codesign --force --deep --sign - "$DEST" 2>/dev/null
"$LSREGISTER" -f "$DEST"
echo "Built: $DEST ($HANDLER_ID), handles inboxclone:// and mailto:"

if [ "${1:-}" = "--default" ]; then
  swift - "$DEST" <<'SWIFT'
import AppKit
let app = URL(fileURLWithPath: CommandLine.arguments[1])
let done = DispatchSemaphore(value: 0)
NSWorkspace.shared.setDefaultApplication(at: app, toOpenURLsWithScheme: "mailto") { err in
    if let err = err { print("default mail app NOT set: \(err.localizedDescription)") }
    else { print("default mail app set: \(app.path)") }
    done.signal()
}
while done.wait(timeout: .now() + 0.1) == .timedOut { RunLoop.main.run(until: Date() + 0.1) }
SWIFT
fi
