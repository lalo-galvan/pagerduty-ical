# pagerduty-ical

Sync your PagerDuty on-call shifts directly into Apple Calendar.

Creates a dedicated calendar your family can toggle on/off, keeps it in sync with your shifts from a specific PagerDuty schedule. Safe to run repeatedly — adds new shifts, removes stale ones, skips ones that already exist.

**macOS only** — uses AppleScript to talk to Apple Calendar.

## Requirements

- macOS with Apple Calendar
- [uv](https://docs.astral.sh/uv/) (or plain Python 3.9+)
- A PagerDuty account with API access

## Setup

**1. Clone the repo**

```bash
git clone https://github.com/yourusername/pagerduty-ical.git
cd pagerduty-ical
```

**2. Get your PagerDuty credentials**

| Variable | Where to find it |
|---|---|
| `PAGERDUTY_API_KEY` | My Profile → User Settings → Create API User Token |
| `PAGERDUTY_USER_ID` | Your profile URL: `/users/PXXXXXX` |
| `PAGERDUTY_SCHEDULE_ID` | The schedule page URL: `/schedules/PXXXXXX` |

**3. Set environment variables**

```bash
export PAGERDUTY_API_KEY="your-api-key"
export PAGERDUTY_USER_ID="PXXXXXX"
export PAGERDUTY_SCHEDULE_ID="PXXXXXX"
```

Or put them in a `.env` file and source it, or pass them inline.

## Usage

```bash
# Sync shifts (safe to run repeatedly)
uv run pagerduty_to_apple_calendar.py

# Preview what would change without making changes
uv run pagerduty_to_apple_calendar.py --dry-run

# Remove all events created by this script
uv run pagerduty_to_apple_calendar.py --purge

# Dry-run purge (preview without deleting)
uv run pagerduty_to_apple_calendar.py --purge --dry-run
```

## Optional configuration

| Variable | Default | Description |
|---|---|---|
| `ONCALL_LOOKAHEAD_DAYS` | `90` | How many days ahead to sync |
| `ONCALL_CALENDAR_NAME` | `On-Call` | Calendar name in Apple Calendar |
| `ONCALL_EVENT_TITLE` | `On-Call 📟` | Event title shown on the calendar |

## Sharing the calendar with family

The script writes events into a named calendar. For family sharing to work, that calendar must be an iCloud calendar (not a local one) — so you need to create it manually before running the script for the first time.

**One-time setup:**

1. Open **Apple Calendar**
2. File → **New Calendar** → choose **iCloud** as the account (important — not "On My Mac")
3. Name it whatever you want (default the script looks for: `On-Call`)
   - To use a different name, set `ONCALL_CALENDAR_NAME=Your Name` when running the script
4. Right-click the new calendar → **Share Calendar...**
5. Enter the iCloud email addresses of the people you want to share with
6. They accept the invitation and will see your shifts appear automatically

Once that calendar exists, the script will find it by name and add/remove events into it on every sync run.

> **Note:** iCloud calendar sharing requires all parties to have an iCloud account.

## Automate with launchd (macOS)

Copy and customize the example plist:

```bash
cp com.example.pagerduty-oncall-sync.plist ~/Library/LaunchAgents/com.yourname.pagerduty-oncall-sync.plist
```

Edit the plist to set your credentials, `uv` path, and working directory, then load it:

```bash
launchctl load ~/Library/LaunchAgents/com.yourname.pagerduty-oncall-sync.plist
```

This runs the sync every 12 hours and immediately on login. Logs go to `/tmp/pagerduty-oncall-sync.log`.

To unload:
```bash
launchctl unload ~/Library/LaunchAgents/com.yourname.pagerduty-oncall-sync.plist
```

## Automate with cron

```bash
crontab -e
# Add:
0 */4 * * * cd /path/to/pagerduty-ical && PAGERDUTY_API_KEY=xxx PAGERDUTY_USER_ID=xxx PAGERDUTY_SCHEDULE_ID=xxx uv run pagerduty_to_apple_calendar.py
```

## How it works

- Fetches on-call shifts from the PagerDuty API for the configured lookahead window
- Creates an Apple Calendar event for each shift using AppleScript
- Tags each event with a unique key in the event description (hidden from view)
- On subsequent runs, skips events that already exist and removes events that no longer appear in PagerDuty
- Deduplicates shifts that appear multiple times due to escalation policy layers

## License

MIT
