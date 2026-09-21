# Inbox for Android

Native Kotlin/Compose port of the clone. It talks straight to the Gmail REST
API, so the phone needs no Mac and no server in the middle.
`gmail/Bundling.kt`, `gmail/Mime.kt` and `gmail/Unsub.kt` are line by line ports
of `app.py`'s classifiers and parsers (by way of the SwiftUI app in `ios/`);
keep the three in sync when the triage model changes. The visual system tracks
`static/style.css`, not stock Material: Material Icons rendered by codepoint
from the bundled font, Roboto in three weights, separated rounded cards with the
same borders and shadows, read mail receding to gray, the 4px unread edge and
dot, yellow pin dots, gray chips with blue icons, the red active pill in the
drawer, and the yellow mailing-list banner. `ui/Theme.kt` holds the tokens;
`MIcon` is the `<i class="material-icons">` equivalent.

## The setup wizard

The point of this build: hand someone the APK and they connect their own Gmail
without touching a terminal. First launch runs `ui/SetupWizard.kt`, which offers
two routes.

1. **The built-in connection.** If the APK was built with `client.properties`
   present, it carries an OAuth client and sign-in is one tap. Everyone who uses
   such a build has to be allowed on that one Google Cloud project (a test user,
   or the app published), and the project owner can see the usage.
2. **Your own Google Cloud project.** The wizard walks through creating a
   project, enabling the Gmail API, filling in the consent screen, and creating a
   **Desktop app** OAuth client, with a button per step that opens the exact
   console page. Then paste the downloaded `client_secret.json` (or the ID and
   secret on two lines) and sign in. Nothing then depends on anyone else.

Either way Google's consent page opens in a Chrome Custom Tab, and it shows
"Google hasn't verified this app" because this app is not going through Google's
review. Advanced, then "Go to Inbox (unsafe)". The wizard says so up front.

### Why the OAuth client must be "Desktop app"

The app catches the OAuth redirect on a one-shot `127.0.0.1` listener with PKCE,
exactly like the Mac server does. Google has deprecated the loopback redirect
for clients registered as **Android**, and disabled custom URI schemes for them,
steering those toward Play services and the Google Identity Services SDK. A
**Desktop app** client still accepts a loopback redirect, which keeps the flow
working from a sideloaded APK with no Play-services dependency, no hosted
redirect page, and no signing-fingerprint registration. A "Web application"
client is rejected by the wizard, because its redirect rules do not fit.

### The 7-day sign-out

A Cloud project whose consent screen is **External** and whose publishing status
is still **Testing** gets refresh tokens that expire after 7 days ([Google's
docs](https://developers.google.com/identity/protocols/oauth2#expiration)). The
app notices (`invalid_grant`) and drops back into the wizard to sign in again.
Press **Publish app** on the console's Audience page and it stops happening.

## In v1

- Inbox with Bundles (the same keyword classifier plus `Bundle/<name>` label
  overrides), pinned shortlist merged into page one, IMPORTANT+unread float,
  date separators
- Four configurable swipe zones (short and full, each edge), set in Settings.
  Defaults: right = Done, full right = Share, left = Snooze, full left = Pin.
  The reveal color and icon swap at the zone boundary with a haptic. Snoozed and
  Done keep swipe right as Move to Inbox. Long-press for the rest.
- Snooze presets ported from `resolve_snooze`, plus pick date and time. Each
  snooze is a WorkManager job that does the label flip and posts the banner;
  foreground and the periodic worker both re-check, so a dropped job is not a
  lost snooze.
- Reader: one WebView renders the thread as message cards, embedded `cid:`
  images are served from the attachment endpoint through
  `shouldInterceptRequest`, quoted history sits behind a toggle, attachments
  open in whatever app handles the type
- Reply and reply-all (never addressed to yourself), compose, threaded with
  In-Reply-To/References, sent as your display name
- Snoozed / Done / Sent views, Gmail-syntax search, pull to refresh, infinite
  scroll, the pinned-only app-bar switch, undo snackbar
- Bundle sweep two ways: the `done_all` button in the bundle head, or swipe the
  bundle card right
- **Unsubscribe** (RFC 2369 / RFC 8058): an inline link on the card, a banner in
  the reader, and a long-press action. Senders that bury the opt-out in the body
  get a one-time background body scan whose verdict is cached, same as the Mac's
  `unsub_scan` table. Both surfaces confirm first, then `UnsubResolver` walks
  the same ladder as the Mac's api_unsubscribe, stopping at the first rung that
  works: the RFC 8058 one-click POST; the sender's confirmation page driven to
  completion in-app (redirects including meta-refresh and window.location, the
  confirm form with its hidden tokens, the opt-out radio, your address in the
  "which address?" box, the reason dropdown, a second confirm screen, up to four
  rounds); a one-click POST the sender never advertised, accepted only when the
  response says in words that you are off the list; `UnsubWebDriver`, which
  loads the page again in an offscreen WebView for opt-outs that only exist once
  the page's scripts have run; then the mailto route sent from your account. The
  browser opens only when all five have failed. Every hop and every subresource
  goes through the same resolve-to-public-only SSRF guard as the Mac, login
  forms are never submitted, and a page reading "sorry to see you go" above a
  confirm button is not mistaken for success. `UnsubResolverTest` runs the same
  fixtures the Mac and iOS ports are checked against.
- New-mail banners from a 15-minute WorkManager poll (Android's floor for
  periodic work), toggleable in Settings
- `inboxclone://thread/<id>` opens that exact thread, so a shared link or a
  notification lands on the right mail

## Not ported

Block sender, move-to-bundle filters, drafts, aliases, multi-select, undo send,
live SSE. **Send to Things 3** is Apple-only, so the same slot is a share
intent: it hands any task app the subject, the `inboxclone://` backlink and the
Gmail permalink.

## Build

Needs a JDK 17 and the Android SDK (`brew install --cask temurin@17` or
`brew install openjdk@17`, plus `brew install android-commandlinetools`). Point
`android/local.properties` at the SDK, then:

    ./scripts/build-apk.sh

It creates `keystore/inbox-release.jks` on first run (gitignored, and worth
backing up: without it a later APK cannot upgrade an installed one), builds the
release variant, and prints the path, the signing fingerprints and the sha256 of
`build/Inbox.apk`.

To bake an OAuth client into the build, copy `client.properties.example` to
`client.properties` first. Leave it out and every user goes through the
bring-your-own-project route.

## Send it to someone

`build/Inbox.apk` is the whole thing, about 2.4 MB. Mail it, AirDrop it, drop it
in a chat. On the phone: open the file, allow the app store prompt about
installing unknown apps, install, open, follow the wizard. If they are on the
built-in connection, add their Gmail address as a test user on the Cloud project
first, or Google refuses the sign-in.

## Layout

    app/src/main/java/com/baestheorem/inbox/
      auth/     AuthStore (encrypted client + refresh token), OAuthFlow
                (loopback PKCE), ClientCredentials (paste parser)
      gmail/    GmailClient (REST, retry, metadata cache, history cursor),
                Bundling / Mime / Unsub / UnsubResolver (app.py ports),
                Models, Net
      data/     MailStore (the view model), SnoozeStore, UnsubScanCache,
                UnsubWebDriver (the JS-page rung), SwipeConfig, Prefs
      ui/       Theme + MIcon, SetupWizard, RootScreen, MailList,
                SwipeableRow, ThreadScreen (+ ThreadHtml), ComposeScreen,
                SnoozeSheet, SettingsScreen
      work/     SnoozeWorker (wakes one thread), NewMailWorker (poll +
                banners), Notifications
