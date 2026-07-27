# Paywall

Cheeky Slack enforcer for a private salary channel: structured disclosures, a pinned board, AUD conversion via the [Wise rates API](https://api-docs.wise.com/api-reference/rate), and a boot for anyone who won't spill (or re-up) in time.

## Features

- Structured disclosure modal (base, super, bonus, RSUs/equity, other)
- Up to 4 concurrent equity grants per person (e.g. a new-hire grant plus yearly top-ups), each valued and shown separately
- Public equity: enter `MARKET:TICKER` + shares vesting per year per grant; valued via [Yahoo Finance](https://finance.yahoo.com/) and converted to AUD
- Private equity: representative annual dollar value per grant
- Non-AUD amounts converted to AUD (shown as `USD 289k (~A$437k)`)
- Bot-owned pinned summary message, rebuilt on every change
- New joiners must disclose within **14 days** (nagged every 3 days)
- Re-validation every **180 days**, with a **14-day** grace period before ejection
- Existing channel members are auto-registered on startup (and re-synced daily), so on first deploy everyone gets 14 days to disclose
- Members who are ejected and re-invited get a fresh 14-day window
- `/salary`, `/salary status`, `/salary overdue` (admin)

## Quick start

### 1. Create the Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest**
2. Paste [`slack-manifest.yml`](slack-manifest.yml)
3. Install the app to your workspace
4. Copy the **Bot User OAuth Token** (`xoxb-…`)
5. Under **Basic Information → App-Level Tokens**, create a token with `connections:write` and copy it (`xapp-…`)
6. Invite the bot to your private salary channel (`/invite @Paywall`)
7. Copy the channel ID and your Slack user ID (admin)

### 2. Wise API token

Create an API token in your Wise business/personal account that can read rates (`GET /v1/rates`).

### 3. Configure and run

```bash
cp .env.example .env
# edit .env with your tokens and IDs

docker compose up -d --build
```

SQLite data is persisted in `./data/salary.db`.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `SLACK_BOT_TOKEN` | — | Bot OAuth token (`xoxb-`) |
| `SLACK_APP_TOKEN` | — | App-level token (`xapp-`) for Socket Mode |
| `SALARY_CHANNEL_ID` | — | Private channel ID |
| `WISE_API_TOKEN` | — | Wise API bearer token |
| `ADMIN_USER_ID` | — | Slack user ID for admin-only commands / kick failures |
| `DB_PATH` | `/data/salary.db` | SQLite path inside the container |
| `REVALIDATE_DAYS` | `180` | Days between re-validations |
| `GRACE_DAYS` | `14` | Join disclosure window / stale grace before kick |
| `NAG_INTERVAL_DAYS` | `3` | Days between reminder DMs |
| `AU_SUPER_PCT` | `12.0` | Legislated AU super rate shown in the summary |
| `BOARD_REPOST_DAYS` | `80` | Repost the pinned board before Slack free-plan ~90-day retention hides it |

## Free workspaces

Paywall is built to run exclusively on Slack free workspaces. All features used (private channels, pins, kick, modals, slash commands, Socket Mode, DMs) are available on the free plan. Long-lived state lives in SQLite, not Slack history.

Free plan caveat: messages older than ~90 days are hidden. Editing a message does not reset that clock, so the bot-owned pinned board is automatically reposted before `BOARD_REPOST_DAYS` (default 80). Custom apps also count toward the free plan's 10-app install limit.

## Usage

| Action | How |
|---|---|
| Disclose / update | `/salary` or the button in the welcome/reminder DM |
| Check your due date | `/salary status` |
| List overdue members | `/salary overdue` (admin only) |
| Re-confirm without changes | "Still that, yeah" button in the reminder DM |

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in values
python -m app.main
```

Run tests:

```bash
pytest
```

## Project layout

```
app/
  main.py          # Socket Mode entrypoint
  config.py        # env config
  db.py            # SQLite schema + queries
  fx.py            # Wise rates client + cache
  modal.py         # disclosure modal + validation
  formatting.py    # money / pinned message rendering
  copy.py          # Paywall voice / user-facing strings
  compliance.py    # overdue / nag / eject rules
  pinned.py        # pinned summary create/update
  scheduler.py     # daily compliance job
  handlers/        # events, actions, slash commands
tests/
Dockerfile
docker-compose.yml
slack-manifest.yml
```
