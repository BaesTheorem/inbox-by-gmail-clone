#!/bin/sh
# Click action of a verification-code banner: put the code on the clipboard and
# type it into whatever field has keyboard focus. The code is validated by app.py
# (digits and upper-case letters only) before it reaches this script.
#
# Typing goes through System Events, which needs the Accessibility grant for the
# process that runs this script (the notifier app, or the shell). If that grant
# is missing the keystroke silently does nothing and the clipboard copy still
# stands, so a paste always works.
code="$1"
case "$code" in
  *[!A-Z0-9]*|"") exit 1 ;;
esac
printf '%s' "$code" | pbcopy
osascript -e "tell application \"System Events\" to keystroke \"$code\"" >/dev/null 2>&1 || true
