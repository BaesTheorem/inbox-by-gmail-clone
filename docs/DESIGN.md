---
title: "Inbox by Gmail — Reverse-Engineered UX & Visual Design Specification"
type: research
created: 2026-05-31
subject: Google Inbox (Inbox by Gmail), 2014–2019
status: design reconstruction
---
# Inbox by Gmail — Reverse-Engineered UX & Visual Design Specification

> A reconstruction document intended to let a designer recreate the look, feel, and behavior of Google's deprecated **Inbox by Gmail** (Oct 22, 2014 – Apr 2, 2019). Google never published an official design spec for Inbox, so this document is assembled from primary sources (Google blog posts, developer docs, the official logo SVG), contemporaneous press reviews with screenshots, and faithful community recreations. **Every quantitative value carries a confidence tag** — `[documented]`, `[Material baseline]`, `[recreation]`, or `[estimated]` — so you know what is verbatim vs. inferred.

---

## 0. How to read this document

| Tag | Meaning |
|---|---|
| `[documented]` | Stated in a primary source or extracted directly from the official asset (e.g., the logo SVG). |
| `[Material baseline]` | Not Inbox-specific, but Inbox was built on Material Design 1 (2014) and inherited this value unless it overrode it. |
| `[recreation]` | Taken from a faithful third-party rebuild (chiefly the *Inbox Reborn* extension by ex-Inbox users). Close, but not an official value. |
| `[estimated]` | Best-effort inference from screenshots/branding. Treat as a starting point to eyedropper against the image plates in Appendix B. |

The single most useful action for a recreating designer: open the **image plates in Appendix B** and color-pick directly. The hex values below get you 90% there; the plates settle the last 10%.

---

## 1. Product overview & history

Inbox by Gmail was a **reimagining of email as a to-do list and personal assistant**, built by the Gmail team as a separate product (not a Gmail skin). It was one of the first flagship implementations of **Material Design**, designed *concurrently* with the Material spec itself — some of Inbox's web-density decisions fed back into Material rather than deriving from it.

### Timeline `[documented]`

| Date | Event |
|---|---|
| **Oct 22, 2014** | Launched, invite-only. Announcement post "An inbox that works for you" authored by **Sundar Pichai**. Simultaneous Web / Android / iOS. |
| Dec 2014 | Reminders + Assists; public AMA by the team. |
| **May 28, 2015** | Opened to everyone (no invite). Shipped **Trip Bundles**, custom signatures, **Undo Send** (10s). |
| Jun 2015 | Trip Bundles detailed; offline trip support. |
| **Nov 3, 2015** | **Smart Reply** launches on Inbox (Android/iOS) — *the first Google product to ship Smart Reply.* |
| Mar 15, 2016 | Smart Reply comes to Inbox **web**. |
| Apr 2016 | **Save to Inbox** (link saving), improved Calendar integration. |
| Apr 2017 | **High-priority notifications** (AI-filtered push). |
| Jun 2018 | Snooze options **"Someday"** and **"Pick Place"** (location snooze) **removed** for low usage. |
| Apr 25, 2018 | Gmail's big redesign absorbs Snooze, Nudges, Smart Reply, high-priority notifications, hover actions. |
| **Sep 12, 2018** | Shutdown **announced** (Workspace Updates blog said "end of March 2019"). |
| **Apr 2, 2019** | Inbox **shut down**. |

### Team `[documented]`
- **Product director:** Alex Gawley (Gmail & Calendar).
- **Lead designer:** Jason Cornwell.
- **Note / myth-bust:** *Jacob Bank* is often credited as an Inbox founder — he is not. He joined Google via the **Timeful acquisition (May 2015)**, seven months after launch, and his time-management tech was folded into Inbox/Calendar afterward.

### Design thesis (verbatim from the launch post) `[documented]`
> "it's not Gmail: it's a completely different type of inbox, designed to focus on what really matters… your inbox becomes a centralized place to keep track of the things you need to get back to… a better way to get back to what matters."

Cornwell's framing: *"kind of like an assistant putting your mail into piles to make it easier to deal with."*

![Inbox main list view, mobile — bundles and cards](https://upload.wikimedia.org/wikipedia/en/6/61/Google_Inbox.png)
*Main Android inbox showing the card list and bundle rows. (© Google, used for reference.)*

---

## 2. Design principles (reconstructed)

1. **Email is a to-do list.** Every item can be *done*, *snoozed*, *pinned*, or *reminded* — verbs of task management, not mail filing.
2. **Information without opening.** Highlights pull the answer (flight time, package status, photo) onto the card face.
3. **Machine does the sorting.** Bundles auto-categorize; the user sweeps, not files.
4. **One primary action, always reachable.** The red FAB anchors creation bottom-right on every surface.
5. **Calm, spacious, white.** Low density by deliberate choice (12–17 rows vs. Gmail's denser list), generous whitespace, a single confident blue.
6. **Motion as feedback.** Swipe reveals, FAB fan-out, the logo's radial fill, the sweep "whoosh," and a sunny empty state reward completion.

---

## 3. Visual design system

### 3.1 Color

**Primary brand / UI blue** — Inbox's app bar / primary accent was a Google Blue, near-universally cited as **`#4285F4`** (Google's canonical brand blue), *not* Material's palette blue (`#2196F3`). Note: no official Google spec pins the app bar to that exact value — it's the widely-accepted, inferred figure. Color-pick Plate B to confirm. `[estimated — plausible/inferred, not officially documented]`

**Status-bar / pressed-blue shade** — `#3367D6` (a darker Google Blue, present in the logo SVG; status-bar darkening follows Material convention). `[estimated for status bar; #3367D6 documented in the asset]`

**Logo palette** — extracted directly from the official SVG. Note this multi-blue gradient is the *logo*, broader and more saturated than the single UI blue: `[documented — parsed from official SVG]`

| Role | Hex |
|---|---|
| Envelope flap (medium blue) | `#2A56C6` |
| Envelope shadow (dark blue) | `#1C3AA9` |
| Envelope body (lighter blue) | `#3B78E7` |
| Body mid shade | `#3367D6` |
| Lower-left triangle (cyan-blue) | `#03A9F4` |
| Lower-right triangle (light blue) | `#4FC3F7` |
| Checkmark fill (off-white) | `#E1E1E1` |
| Deep shadow overlay (navy, low opacity) | `#1A237E` |

**Compose FAB red** — the FAB was unambiguously **red** in all coverage. A faithful recreation uses **`#d23f31`**; Google's brand red is `#DB4437`. **No official Inbox FAB hex is documented** — eyedropper Plate F to settle it. `[recreation / estimated]`

**Done / archive green** — the Done action and its checkmark read as green. `#0F9D58` is Google's standard green but is **inferred** for Inbox, not documented. `[estimated]`

**Reminders** — *Correction to common memory:* there is **no documented yellow reminder card background.** The reminder **icon was blue** (the asset was named `ic_reminder_blue_24dp`), and the reminder-creation mini-FAB used `#4285F4`. The "yellow reminders" association most likely conflates the **pin's yellow variant** or Google Keep's yellow. Treat any yellow reminder styling as **unverified** until you color-pick Plate H. `[documented: blue icon; yellow background: refuted/unverified]`

**Neutrals & dividers** `[recreation]`

| Role | Hex |
|---|---|
| Card / row background | `#FFFFFF` |
| Page background (behind cards) | `#F2F2F2` |
| Row divider | `#DDDDDD` |
| Primary text | ~`#212121` |
| Secondary text (snippet, time) | `#5F6368` / `#616161` |

**Bundle category accent colors** — Inbox distinguished bundles primarily by **bespoke icon art**, not text color. The per-category colors below come from the *Inbox Reborn* recreation and are the designer's interpretation, **not verified originals** — use as a plausible starting palette only. `[recreation, low confidence]`

| Bundle | Recreation color |
|---|---|
| Updates | orange `rgb(255,104,57)` |
| Promos | cyan `rgb(0,188,212)` |
| Forums | indigo `rgb(63,81,181)` |
| Social | red `rgb(219,68,55)` |
| Travel / Trips | purple `rgb(156,39,176)` |
| Finance | green `rgb(103,159,56)` |
| Purchases | brown `rgb(121,85,72)` |

### 3.2 Typography

**Typeface: Roboto** for the entire UI. `[documented]` Inbox **predated Google Sans / Product Sans** (those arrived with Google's Sept 2015 logo change), so the "Inbox" wordmark in the app bar was set in Roboto (white, ~20sp, Regular/Light).

Material Design 1 type scale, mapped to Inbox elements: `[Material baseline; mapping estimated]`

| Element | Size | Weight |
|---|---|---|
| App-bar wordmark "Inbox" | ~20sp | Roboto Regular/Light, white |
| Sender name (read) | 16sp | Roboto Regular |
| Sender name (unread) | 16sp | Roboto **Medium (500)** |
| Subject line | 16sp / 14sp | Roboto Regular |
| Snippet (secondary) | 14sp | Roboto Regular, `#5F6368` |
| Timestamp (right-aligned) | 12sp | Roboto Regular (Caption) |
| Bundle header | ~16sp | Roboto Medium |
| Buttons | 14sp | Roboto Medium, ALL CAPS |

Full MD1 scale for reference: Title 20sp Medium · Subheading 16/15sp · Body2 14sp Medium · Body1 14sp · Caption 12sp · Headline 24sp · Display1 34sp.

### 3.3 Iconography
- **Style:** Material line icons (outlined, thin stroke, **24dp** baseline). `[Material baseline]`
- **Bundle icons:** Travel = airplane · Purchases = shopping bag/tag · Finance = dollar · Social = people · Updates = bell · Forums = chat bubble · Promos = tag. `[documented]`
- **Action icons:** Done = circular **checkmark** · Pin = custom **thumbtack** (not in the stock Material set at launch — Google had to add it later) · Snooze = **clock** · Reminder = blue pointing-finger · Compose = **pencil**. `[documented]`
- **App icon:** opened envelope with a checkmark emerging; multi-blue composition; animates a **radial color fill** as the envelope opens and the check rises on launch. `[documented]`

![Inbox app icon](https://upload.wikimedia.org/wikipedia/commons/d/de/Google_Inbox_by_Gmail_logo.png)
*Official app icon (Wikimedia Commons, public domain).*

### 3.4 Layout, grid, density `[Material baseline unless noted]`
- **8dp baseline grid** throughout; touch targets ≥ **48×48dp**.
- **Screen margins:** 16dp mobile, 24dp desktop/tablet. Content keyline at **72dp** from the left (after avatar/icon).
- **App bar height:** 56dp portrait mobile, 64dp desktop. Status bar 24dp.
- **List rows:** MD three-line tile = 88dp standard / 76dp dense. **Inbox overrode these** to hit a deliberately spacious 12–17 rows on web; exact custom heights were never published. `[documented that it was customized; values not documented]`
- **Cards:** each message/bundle is a Material **card** at 2dp resting elevation.

### 3.5 Elevation & shadow `[Material baseline]`

| Component | Resting | Raised |
|---|---|---|
| FAB | 6dp | 12dp (pressed) |
| Card | 2dp | 8dp (picked up) |
| App bar | 4dp | — |
| Nav drawer | 16dp | — |
| Dialog | 24dp | — |

CSS shadow recipes:
- 2dp card: `0 2px 2px 0 rgba(0,0,0,.14), 0 3px 1px -2px rgba(0,0,0,.2), 0 1px 5px 0 rgba(0,0,0,.12)`
- 6dp FAB: `0 6px 10px 0 rgba(0,0,0,.14), 0 1px 18px 0 rgba(0,0,0,.12), 0 3px 5px -1px rgba(0,0,0,.2)`

### 3.6 Motion principles
- **Material easing & duration:** standard MD1 curves (`fast-out-slow-in`), ~200–300ms transitions. `[Material baseline]`
- **Signature motions:** logo radial fill on launch; FAB plus→pencil rotate-and-cut swap; FAB speed-dial fan-out; swipe-card slide revealing the action color beneath; pin "shoot out and back" toggle; bundle expand/collapse; the sweep "whoosh"; sunny empty state. `[documented]`

---

## 4. Core components & behaviors

### 4.1 App bar & navigation
- Top app bar in **blue `#4285F4`** with white "Inbox" wordmark and a **hamburger** opening a left nav drawer (16dp elevation). `[documented]`
- Drawer sections: **Inbox**, the bundles (Travel, Purchases, Finance, Social, Updates, Forums, Promos, Low Priority), **Trips**, **Snoozed**, **Done**, **Reminders**, plus settings. `[documented]`
- A **"Pinned only" toggle switch** sat in the app bar — flipping it filtered the list to pinned items only. `[documented]`

![Nav drawer / sidebar with bundle list](https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/02-Inbox-Inbox-And-Slide.png)
*Inbox list + slide-out navigation. (© MakeUseOf, reference use.)*

### 4.2 The email card / list item
A white card per message: sender (Medium if unread), subject, secondary snippet in gray, right-aligned timestamp, optional avatar/logo, and — crucially — **Highlights** rendered on the face (thumbnails, chips). Cards group under **time separators** ("Today", "Yesterday") and under **bundle headers**.

### 4.3 Bundles (auto-categorization) `[documented]`
The flagship organizing feature — "the next generation of Gmail's labels." Related mail auto-groups into a **single collapsible row** with a category header, icon, and message count; expand to see members.

- **Default set (9):** Travel, Purchases, Finance, Social, Updates, Forums, Promos, **Low Priority**, plus user **Custom bundles** (built on Gmail labels).
- **Per-bundle delivery schedule:** *Show in inbox* (real-time) · *Once a day* (7 AM) · *Once a week* (Mon 7 AM) — great for taming Promos.
- **Custom bundles:** create a Gmail label → enable "bundle messages in inbox" → optionally set frequency.
- **Sweep:** a checkmark on the bundle header marks **all unpinned** members **Done** in one tap (they go to the **Done** archive, not deleted).
- **Quirk:** some bundled mail could be auto-marked Done and **never surface in the main inbox** — a known source of "where did my email go?" friction.
- The category taxonomy outlived Inbox: Gmail search still honors `category:travel`, `category:purchases`, `category:finance`, `category:reservations`.

![Purchases bundle expanded](https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/04-Inbox-Purchases-Label.png)
*An expanded bundle with category header. (© MakeUseOf, reference use.)*

### 4.4 Highlights `[documented]`
Key info extracted to the card face so you needn't open the mail. Rendered as compact **chips / mini-cards** above or alongside the message, often with imagery and a one-tap **Action**.

**Ten documented highlight types:** Flight · Bus · Car rental · Train reservations (each with departure/arrival + a destination image; flights add **live status** + *Check In*); **Order** (price, est. delivery, product image); **Parcel delivery** (live tracking badge + *Track Package*); **Hotel** (+ property image); **Restaurant** (+ image); **Ticketed event** (venue/date + *View Tickets*); **Invoice**.

**Data sources:** schema.org structured markup embedded by senders (FlightReservation, ParcelDelivery, OrderStatus, etc., requiring Google whitelist approval) **and** NLP content analysis for known senders. The *same* markup simultaneously powered Inbox Highlights, Gmail Actions, Google Now cards, and Calendar event creation.

![Highlights and Assists on cards](https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEiAdmZgT10aBgfBVQza_2vTcLkEEvwEZt9qEIKeUF6LRTaoseUr6gnWArkfg29VUm5EwloB5g2t3dRrhFTerOwgctquGu8aAOyUCdmPwmnSTE-MOofvyLKzgtG5OHVSqj2heeKpVw/s1600/inbox5.png)
*Highlights / Assists surfaced inline. (© Google / Google System blog, reference use.)*

### 4.5 Trip bundles `[documented]`
Auto-assembled travel itineraries (launched May/Jun 2015). Inbox scanned confirmation emails and grouped flights, hotels, car rentals, restaurant and event reservations by date/destination into one card bearing a **destination photo**.

- **Collapsed:** destination + dates card; trips also listed in the left nav under **Trips** (upcoming + past).
- **Expanded:** an agenda prioritizing **times, dates, confirmation numbers** — flight (terminal/gate/duration), hotel (check-in/out), car rental, dinner — with source emails listed below. Canonical Google demo: Austin → London (Heathrow), Hertz rental, week at "The Blue Hotel."
- **Dynamic:** delays/gate changes update automatically. **Offline-saveable.** **Shareable** via email (Dec 2015). Third-party (HotelTonight, Eat24) integrated.

![Trip bundle itinerary](https://www.droid-life.com/wp-content/uploads/2015/06/inbox-trip-bundles1.jpg)
*Expanded trip bundle. (© Droid-Life, reference use.)*

### 4.6 Reminders & Assists `[documented]`
- **Reminders** are first-class inbox items (not emails) — created via the FAB, and pinnable/snoozable/done-able like any item. They sit inline among messages.
- **Assists:** Google auto-enriches a reminder — a "call [business]" reminder gains the phone number and hours; travel/package reminders gain itinerary/tracking detail.
- Later: follow-up reminders for sent mail awaiting a reply.
- *(Styling: the reminder icon was blue; a distinct yellow card background is not documented — see §3.1.)*

![Reminders](https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/09-Inbox-Reminders.png)
*Reminders with suggestions. (© MakeUseOf, reference use.)*

### 4.7 The FAB / Speed Dial / Compose `[documented]`
- Circular **red** FAB, bottom-right, 56dp (mini sub-FABs 40dp), 6dp elevation; plus icon at rest.
- Tapping expands a **speed dial** (Google's internal name) fanning **upward**: **Compose** (pencil), **Reminder** (blue finger), and **frequent-contact avatar chips** for one-tap compose-to-person.
- **Icon transition:** plus rotates and cuts out as the pencil rotates and cuts in (a fast swap, not a morph).
- Compose opens as a sheet (bottom sheet on mobile). Replies are inline at the thread bottom in a light Material card.

![Compose FAB fan-out with contacts](https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/10-Inbox-Compose-Windows.png)
*FAB expanded with frequent contacts. (© MakeUseOf, reference use.)*

### 4.8 Done / Sweep / Pin `[documented]`
- **Done** = archive-equivalent; a green **checkmark** sends mail to the **Done** label (recover by opening it and tapping the check again).
- **Sweep** = mark all unpinned items in a section/bundle Done in one action (icon: checkmark with speed lines). Reviewers described the feel as a satisfying "whoosh."
- **Pin** = Inbox's star; **blue pin** icon; pinned items survive a sweep and can be isolated via the "Pinned only" toggle or `is:pinned`. Pin toggle animation: the pin shoots out in its facing direction, then back in reversed as the button restyles.

![Pinned items](https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/08-Inbox-Pinned.png)
*Pinned messages. (© MakeUseOf, reference use.)*

### 4.9 Swipe gestures `[documented]`
- **Swipe right = Done** (green reveal + checkmark beneath the sliding card).
- **Swipe left = Snooze** (reveals the snooze picker; clock-style icon beneath).
- **Fixed, not user-configurable** (unlike later Gmail). Material swipe-to-dismiss physics.

![Swipe shortcuts](https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/07-Inbox-Sliding-Shortcuts.png)
*Right = Done, left = Snooze. (© MakeUseOf, reference use.)*

### 4.10 Snooze `[documented]`
Preset options (full set before the Jun 2018 trim):
- **Later today**
- **Tomorrow**
- **This weekend** / **Later this week**
- **Next week**
- **Someday** *(random future time — removed Jun 2018)*
- **Pick date & time** (custom picker)
- **Snooze until you arrive at a place** / "Pick Place" — geofenced to Work/Home *(removed Jun 2018)*

Mar 2015 added user-set **morning/afternoon/evening** default times (linked to Keep), prompting e.g. "Change your morning time to 7:30 AM?" Jul 2015 added **suggested** snooze times with a clock-icon animation. Snoozed items return to the **top** of the inbox at the chosen time/place; a clock icon marks them; a **Snoozed** view lists all pending.

![Snooze options](https://www.droid-life.com/wp-content/uploads/2018/09/Inbox-Snooze1.jpg)
*Snooze preset menu. (© Droid-Life, reference use.)*

### 4.11 Smart Reply `[documented]`
- Debuted on Inbox **Nov 3, 2015** (mobile), **first Google product** to ship it; web Mar 2016.
- Showed **three** short suggested replies at the bottom of an eligible message; tap to load into the compose field (edit or send-as-is — "two-tap email"). Adaptive: unused suggestion styles fade for that contact.
- Tech: two coupled **LSTM** networks (encoder→thought vector→decoder), English only at launch. Grew to ~**12%** of Inbox mobile replies. (Early bug: it over-suggested "I love you" until probabilities were normalized.)

![Smart Reply suggestions](https://static0.anpoimages.com/wordpress/wp-content/uploads/2016/03/nexus2cee_Screenshot-from-2016-03-15-15-33-14.png)
*Three Smart Reply chips (web). (© Android Police, reference use.)*

### 4.12 Undo `[documented]`
A **dark/black snackbar** with an **UNDO** text button appears at the bottom immediately after Done/Sweep/Snooze/Delete. Exact timeout undocumented (~Material 2–5s). Separately, **Undo Send** offered a 10s send-cancel window (added Jun 2015).

### 4.13 Search `[documented, lightly]`
Top search field with **contact suggestions** as you type and **natural-language smart results** — e.g. typing "confirmation number" returned formatted booking cards from Highlight data. This structured search notably **did not** survive into Gmail.

### 4.14 Save to Inbox `[documented]`
From Apr 2016: save web links into Inbox as items (desktop Chrome extension; mobile OS share sheet). Saved URLs grouped in a **Saved** bundle, snoozable like anything else. Removed from Android Aug 2018.

### 4.15 Empty state `[documented, with correction]`
At inbox zero Inbox showed a **sunny-sky illustration** as a reward. *Correction:* the specific copy **"Woohoo!"** is **not confirmed** in sources, and the well-known "woman reading in a garden/sun" flat illustration belongs to **Gmail's** empty state — do not assume it was Inbox's exact art. Recreate "a calm, sunny illustration on clearing the inbox" and verify against a live capture (Appendix C).

---

## 5. Platform differences `[documented]`

| Aspect | Web (inbox.google.com) | Android | iOS |
|---|---|---|---|
| Design intent | Same language, denser list (12–17 rows) | **Mobile-first**; swipe-centric | Mobile-first; praised as *better than Gmail's own iOS app* at launch (The Verge) |
| Smart Reply | Added Mar 2016 | Launch Nov 2015 | Launch Nov 2015 |
| Save to Inbox | Chrome extension | Share sheet (removed Aug 2018) | Share sheet |
| Canned responses | Desktop-only | — | — |
| Bulk attachment download | — | Android-specific | — |
| Snooze-by-location | n/a (no device GPS) | Yes | Yes |

Core swipe (right=Done / left=Snooze) and sweep were mobile-optimized; web exposed equivalents via hover buttons + keyboard shortcuts.

---

## 6. What migrated to Gmail vs. what died `[documented]`

**Survived (into the Apr 2018 Gmail redesign and after):** Snooze · Smart Reply · Nudges (follow-up reminders) · High-priority notifications · Hover actions.
**Died with Inbox / never shipped to Gmail:** Bundles (the adaptive, sweepable kind — *promised but never delivered*) · **Trip Bundles** (explicitly acknowledged as not migrating; standalone Google Trips also shut down 2019) · **Pin** · inline **Reminders/Assists** · **Save to Inbox** · natural-language **smart search cards** · the unified **Done/Sweep** model · **Highlights** (live flight/package extraction).

**Myth-bust:** **Smart Compose is NOT an Inbox feature** — it originated in the 2018 Gmail redesign. The Inbox-born ML feature was **Smart Reply**. The two are frequently conflated.

Reception: a devoted-but-niche following; the shutdown drew Change.org petitions and accusations that Google overstated the migration. **Shortwave** (2022, by ex-Googlers) was built explicitly to revive the Inbox experience.

---

## 7. Reconstruction confidence summary

| Area | Confidence | Notes |
|---|---|---|
| Logo hex values | **High** | Parsed from official SVG |
| Primary UI blue `#4285F4` | **Medium** | Canonical Google Blue, but inferred — no official spec pins the app bar to it |
| Roboto typeface | **High** | Documented |
| Material elevation/grid/type scale | **High** | Official MD1 spec (inherited) |
| FAB red exact hex | **Medium** | Red certain; `#d23f31`/`#DB4437` recreation/estimate |
| Done green `#0F9D58` | **Low** | Green certain; hex inferred |
| Bundle category colors | **Low** | From recreation, not original |
| Reminder yellow background | **Refuted/Unverified** | Icon was blue; yellow bg undocumented |
| Inbox-specific row heights | **Low** | Known customized, values unpublished |
| Empty-state "Woohoo!" copy | **Unverified** | Sunny illustration documented; copy not |
| All dates in §1 | **High** | Primary sources |

**To close the remaining gaps:** color-pick the Appendix B plates; or decompile the final Inbox APK (Android 1.78, Oct 2018) for `colors.xml`/`dimens.xml`/drawables — the only way to recover exact official values.

---

## Appendix A — Quick design tokens (copy-paste starting set)

```css
/* Inbox by Gmail — reconstructed tokens. Verify * against image plates / APK. */
--inbox-blue:            #4285F4;   /* inferred Google Blue* */
--inbox-blue-dark:       #3367D6;   /* documented (asset) */
--inbox-fab-red:         #d23f31;   /* recreation* */
--inbox-brand-red:       #DB4437;   /* Google red, alt* */
--inbox-done-green:      #0F9D58;   /* estimated* */
--inbox-bg-page:         #F2F2F2;   /* recreation */
--inbox-bg-card:         #FFFFFF;
--inbox-divider:         #DDDDDD;   /* recreation */
--inbox-text-primary:    #212121;   /* estimated */
--inbox-text-secondary:  #5F6368;
--inbox-font:            'Roboto', sans-serif;

/* Type (sp) */
--t-appbar: 20; --t-sender: 16; --t-subject: 16; --t-snippet: 14; --t-time: 12;

/* Elevation (dp) */
--e-fab: 6; --e-fab-pressed: 12; --e-card: 2; --e-appbar: 4; --e-drawer: 16;

/* Grid (dp) */
--grid: 8; --touch: 48; --margin-mobile: 16; --keyline: 72; --appbar-h-mobile: 56;
```

---

## Appendix B — Image plates (all URLs HTTP-200 verified)

> **Licensing:** Only the two Wikimedia logo files are public domain. All press screenshots are **copyrighted Google UI captured by the outlet** — fine for an internal/editorial design reference, **not** for commercial redistribution. Attributions noted per item.

**Plate A — Logo / wordmark (PUBLIC DOMAIN)**
- Icon PNG 512²: `https://upload.wikimedia.org/wikipedia/commons/d/de/Google_Inbox_by_Gmail_logo.png`
- Icon SVG: `https://upload.wikimedia.org/wikipedia/commons/f/f2/Google_Inbox_by_Gmail_logo.svg`

**Plate B — Main inbox / bundles / cards**
- Android (Wikipedia): `https://upload.wikimedia.org/wikipedia/en/6/61/Google_Inbox.png`
- Mobile bundles (Google System): `https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEhuH0XyJ_g9mK8SmsWUwNV4GCXHe-CuYfxIZTueVTc2_rT0cNe_nCnjqs2gkxabWskojDo4Cn2nHK4t4gaHIRVTcGzcb0P-XMmGJdmAuYfu-wr_Fjf9C2PVo75tWuoztAVnHhWHxQ/s1600/inbox4.png`
- Web desktop (Google System): `https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEjPjvQUophSBXkD0pv5794hq9pU6xCxaZtpzVPe7mcShrex6G5RXqsh7-v2yqRlsHfSKHeanPCRDao_qxify02JkkeQIJN_kU8YmM2-v4CU4GM7Cx4iujNuC92q8PBc1RhLZzq44Q/s1600/inbox-web.png`
- Web desktop (MakeUseOf): `https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/12-Inbox-Web.png`
- iOS retina: `https://elementalstudios.us/wp-content/uploads/2014/11/google-inbox-app-2-1242x2208.png`
- Mobile vs desktop: `https://jennisheppard.com/wp-content/uploads/2014/11/Mobile-v-desktop-gmail-inbox-google.jpg`
- GIGAZINE timeline: `https://i.gzn.jp/img/2014/11/25/inbox-review/a13.png`

**Plate C — Nav drawer / bundle settings**
- Drawer + list (MakeUseOf): `https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/02-Inbox-Inbox-And-Slide.png`
- Home/nav (iOS): `https://elementalstudios.us/wp-content/uploads/2014/11/google-inbox-app-4-1242x2208.png`

**Plate D — Bundle expanded / custom bundles**
- Purchases bundle: `https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/04-Inbox-Purchases-Label.png`
- Bundle (iOS): `https://elementalstudios.us/wp-content/uploads/2014/11/google-inbox-app-10-1242x2208.png`
- Create label/bundle: `https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/05-Inbox-Create-Label.png`

**Plate E — Highlights (flight/package/photo)**
- Highlights & Assists (Google System): `https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEiAdmZgT10aBgfBVQza_2vTcLkEEvwEZt9qEIKeUF6LRTaoseUr6gnWArkfg29VUm5EwloB5g2t3dRrhFTerOwgctquGu8aAOyUCdmPwmnSTE-MOofvyLKzgtG5OHVSqj2heeKpVw/s1600/inbox5.png`
- Highlights (iOS): `https://elementalstudios.us/wp-content/uploads/2014/11/google-inbox-app-11-1242x2208.png`

**Plate F — Compose FAB / fan-out**
- FAB fan-out + contacts: `https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/10-Inbox-Compose-Windows.png`
- Compose (iOS): `https://elementalstudios.us/wp-content/uploads/2014/11/google-inbox-app-16-1242x2208.jpg`
- Extra reminder button test: `https://static0.anpoimages.com/wordpress/wp-content/uploads/2015/03/img_550f3cc5a912f.png`

**Plate G — Trip bundles**
- Itinerary 1: `https://www.droid-life.com/wp-content/uploads/2015/06/inbox-trip-bundles.jpg`
- Itinerary 2 (expanded): `https://www.droid-life.com/wp-content/uploads/2015/06/inbox-trip-bundles1.jpg`
- Android Police: `https://static0.anpoimages.com/wordpress/wp-content/uploads/2015/06/nexus2cee_trips21.png`

**Plate H — Reminders & Assists**
- Reminders (iOS): `https://elementalstudios.us/wp-content/uploads/2014/11/google-inbox-app-13-1242x2208.png`
- Reminders w/ suggestions: `https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/09-Inbox-Reminders.png`
- Assists (iOS): `https://elementalstudios.us/wp-content/uploads/2014/11/google-inbox-app-17-1242x2208.jpg`

**Plate I — Snooze**
- Snooze presets: `https://www.droid-life.com/wp-content/uploads/2018/09/Inbox-Snooze1.jpg`
- GIGAZINE snooze series: `https://i.gzn.jp/img/2014/11/25/inbox-review/a15.png` … `a16.png` `a17.png` `a18.png` `a19.png` `a20.png`
- Snooze/swipe (iOS): `https://elementalstudios.us/wp-content/uploads/2014/11/google-inbox-app-15-1242x2208.jpg`

**Plate J — Swipe gestures / Done**
- Swipe GIF (animated): `https://www.droid-life.com/wp-content/uploads/2015/07/inbox-gesture4.gif`
- Swipe still: `https://www.droid-life.com/wp-content/uploads/2015/07/inbox-gesture-2.jpg`
- Sliding shortcuts: `https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/07-Inbox-Sliding-Shortcuts.png`
- Mark as Done: `https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/06-Inbox-Done.png`

**Plate K — Pin**
- Pinned (web): `https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/08-Inbox-Pinned.png`
- Pinned (iOS): `https://elementalstudios.us/wp-content/uploads/2014/11/google-inbox-app-14-1242x2208.jpg`

**Plate L — Smart Reply**
- Web Smart Reply: `https://static0.anpoimages.com/wordpress/wp-content/uploads/2016/03/nexus2cee_Screenshot-from-2016-03-15-15-33-14.png`

**Plate M — Compose / reply / message view**
- Compose (GIGAZINE): `https://i.gzn.jp/img/2014/11/25/inbox-review/a27.png`
- Inbox vs Gmail message: `https://static0.makeuseofimages.com/wordpress/wp-content/uploads/2014/10/03-Inbox-vs-Gmail-Message.png`

**Plate N — Empty state (note: largely Gmail's art; verify)**
- Old empty-state side-by-side: `https://m-cdn.phonearena.com/images/articles/384545-image/inbox-z.webp`
- Empty-state (Android Police): `https://static0.anpoimages.com/wordpress/wp-content/uploads/2022/01/gmail-inbox-zero.png`

**Best multi-shot galleries for browsing/eyedropping:**
- GIGAZINE walkthrough (Nov 2014): `https://gigazine.net/gsc_news/en/20141125-inbox-review/`
- MakeUseOf review (13 labeled shots): `https://www.makeuseof.com/tag/google-inbox-review-breath-fresh-air/`
- Elemental Studios iOS retina set: `https://elementalstudios.us/hands-on-google-inbox/`
- Droid-Life "features we still need": `https://www.droid-life.com/2018/09/12/here-are-a-bunch-of-features-from-inbox-we-still-need-in-gmail/`

---

## Appendix C — Recovering exact official values
1. **Wayback Machine:** browse `https://web.archive.org/web/2016*/https://www.google.com/inbox/` (marketing hero shots) and captures of `inbox.google.com` — inspect served CSS for color values.
2. **APK teardown (most authoritative):** the final Inbox APK (Android **1.78**, Oct 16 2018) is archived; extract `res/values/colors.xml` and `dimens.xml` for the true hexes and the custom dense row heights this doc lists as unpublished.
3. **USPTO patent US 10,911,389 "Rich preview of bundled content"** — documents bundle-card preview layout.

---

## Sources (primary & key)
- Google — *An inbox that works for you* (Oct 22 2014): https://blog.google/products-and-platforms/products/gmail/an-inbox-that-works-for-you/
- Google Workspace Updates — *Inbox shutting down* (Sep 12 2018): https://workspaceupdates.googleblog.com/2018/09/inbox-by-gmail-shutdown.html
- Gmail Blog — *A bit about Bundles* (Nov 2014): https://gmail.googleblog.com/2014/11/a-bit-about-bundles-in-inbox.html
- Gmail Blog — *Trip Bundles* (Jun 2015): https://gmail.googleblog.com/2015/06/trip-bundles-in-inbox-by-gmail.html
- Gmail Blog — *Custom snooze* (Mar 31 2015): https://gmail.googleblog.com/2015/03/custom-snooze-in-inbox-by-gmail-rise.html
- Google — *Computer, respond to this email* (Smart Reply, Nov 3 2015): https://blog.google/products/gmail/computer-respond-to-this-email/
- Google Developers — *What Are Highlights?*: https://developers.google.com/workspace/gmail/markup/highlights
- Google Research — *Computer, respond to this email* (LSTM detail): https://research.google/blog/computer-respond-to-this-email/
- Google Design (Medium) — *How Google Designers Adapt Material*: https://medium.com/google-design/how-google-designers-adapt-material-e2818ad09d7d
- Material Design 1 — Type / Elevation / Lists / FAB / Layout: https://m1.material.io/
- Wikipedia — *Inbox by Gmail*: https://en.wikipedia.org/wiki/Inbox_by_Gmail
- Inbox Reborn (community recreation, CSS source): https://github.com/team-inbox/inbox-reborn
- The Verge / TechCrunch / Android Police / 9to5Google / Droid-Life / MakeUseOf / GIGAZINE / Elemental Studios — launch and feature coverage (see image plates for specific article URLs)
- 9to5Google — *Inbox dies: features that haven't hit Gmail* (Apr 1 2019): https://9to5google.com/2019/04/01/inbox-gmail-features-havent-arrived/
- Android Police — *RIP Inbox by Gmail* (Apr 2 2019): https://www.androidpolice.com/2019/04/02/the-good-die-young-rest-in-peace-inbox-by-gmail/
