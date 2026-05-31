# credentials/ (gitignored — you supply these)

This folder holds OAuth secrets and is intentionally excluded from git.

- `client_secret.json` — download from Google Cloud Console:
  create a project → enable the **Gmail API** → OAuth consent screen (External,
  add yourself; set publishing status to **In production**) → create an
  **OAuth client ID → Desktop app** → download the JSON here.
- `token.json` — created automatically on first run after you approve consent.

Scopes used: `gmail.modify`, `gmail.send`, `gmail.settings.basic`.
