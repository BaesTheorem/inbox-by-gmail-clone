# Roadmap

## ⏳ iPhone app — standalone PWA (deferred)

**Goal:** use this on iPhone as a real home-screen app, **independent of the Mac**
(Mac may be off). Decided direction: **client-side PWA** (not a native SwiftUI rewrite —
weeks of work, Xcode sideload, 7-day expiry on a free Apple ID).

### Plan (Option 2)
- **Host the frontend as static files on free always-on HTTPS** (e.g., GitHub Pages /
  Cloudflare Pages). No server to run.
- **Move the backend (`app.py`) logic into browser JavaScript** — talk to the Gmail REST
  API directly with `fetch`. OAuth via **Google Identity Services** (token client / PKCE).
- **New Google OAuth client of type "Web application"**, with the Pages URL as an
  authorized JS origin (the current one is a Desktop client).
- **Make the UI responsive** for phone (drawer becomes an overlay, full-width cards,
  app bar fits a narrow screen). Add a **web app manifest** + apple-touch-icon so
  "Add to Home Screen" gives an icon + fullscreen standalone mode.

### What carries over for free
Read / triage / bundles / pin / done / search / compose + attachments / drafts / sent /
live-sync (SSE) / **Send-to-Things** (Things has an iOS URL scheme).

### Known degradations to handle (browser sandbox, no server)
- **Snooze scheduler** — no background execution; snoozed mail can't re-file itself while
  the app is closed. Options: (a) re-file on app open + periodic foreground check, or
  (b) a tiny free serverless cron (Cloudflare Worker) that re-adds `INBOX` at wake time.
- **One-click unsubscribe** — the silent POST to the sender is blocked by browser CORS;
  degrades to "open the unsubscribe page." (mailto-unsubscribe still works via Gmail send.)
- **Notifications** — iOS web push (16.4+) is possible but finicky; likely skip initially.

### Rough effort
Days (reuses the existing HTML/CSS/JS UI; the work is OAuth-in-browser + porting Gmail
calls to client-side `fetch` + responsive CSS + manifest).

### Alternative considered (rejected for now)
- **Native SwiftUI app** — most polished + full background/notifications, but a from-scratch
  Swift rewrite (weeks), needs a Mac to build/re-sign, and free-Apple-ID builds expire
  every 7 days. Revisit only if background snooze/notifications become must-haves.
