#!/usr/bin/env python3
"""
Sync PagerDuty on-call shifts directly into Apple Calendar.

Writes events to a dedicated calendar (default: "On-Call") that your family
can toggle on/off without affecting shared calendars. Keeps it in sync
with your shifts from a specific PagerDuty schedule. Safe to run
repeatedly — it adds new shifts, removes stale ones, and skips
ones that already exist.

Usage:
    export PAGERDUTY_API_KEY="your-api-key"
    export PAGERDUTY_USER_ID="PXXXXXX"
    export PAGERDUTY_SCHEDULE_ID="PXXXXXX"

    python3 pagerduty_to_apple_calendar.py            # sync shifts
    python3 pagerduty_to_apple_calendar.py --dry-run   # preview purge
    python3 pagerduty_to_apple_calendar.py --purge     # remove all synced events

To find your IDs:
    - User ID:     Your PagerDuty profile URL → /users/PXXXXXX
    - Schedule ID: The schedule page URL → /schedules/PXXXXXX
    - API Key:     My Profile → User Settings → Create API User Token

Optional env vars:
    ONCALL_LOOKAHEAD_DAYS   How far ahead to look (default: 90)
    ONCALL_CALENDAR_NAME    Calendar name in Apple Calendar (default: Lalo On-Call)
    ONCALL_EVENT_TITLE      What your family sees on the calendar (default: Lalo On-Call 📟)

Automate with launchd or cron:
    crontab -e
    0 */4 * * * cd /path/to/script && /usr/bin/python3 pagerduty_to_apple_calendar.py
"""

import os
import sys
import json
import subprocess
import urllib.request
import urllib.parse
import argparse
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("PAGERDUTY_API_KEY", "")
USER_ID = os.environ.get("PAGERDUTY_USER_ID", "")
SCHEDULE_ID = os.environ.get("PAGERDUTY_SCHEDULE_ID", "")
LOOKAHEAD_DAYS = int(os.environ.get("ONCALL_LOOKAHEAD_DAYS", "90"))
CALENDAR_NAME = os.environ.get("ONCALL_CALENDAR_NAME", "On-Call")
ONCALL_EVENT_TITLE = os.environ.get("ONCALL_EVENT_TITLE", "On-Call 📟")

# Prefix used in event notes to identify events managed by this script
EVENT_TAG = f"pagerduty-sync:{SCHEDULE_ID}"

# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------
missing = []
if not API_KEY:
    missing.append("PAGERDUTY_API_KEY")
if not USER_ID:
    missing.append("PAGERDUTY_USER_ID")
if not SCHEDULE_ID:
    missing.append("PAGERDUTY_SCHEDULE_ID")

if missing:
    print(f"Error: Missing environment variables: {', '.join(missing)}")
    sys.exit(1)

# ---------------------------------------------------------------------------
# PagerDuty API
# ---------------------------------------------------------------------------
def fetch_oncalls():
    """Fetch on-call shifts from PagerDuty filtered to user + schedule."""
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=LOOKAHEAD_DAYS)

    params = urllib.parse.urlencode({
        "since": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "until": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user_ids[]": USER_ID,
        "schedule_ids[]": SCHEDULE_ID,
        "overflow": "true",
    })

    url = f"https://api.pagerduty.com/oncalls?{params}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Token token={API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.pagerduty+json;version=2",
    })

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"PagerDuty API error {e.code}: {body}")
        sys.exit(1)

    return data.get("oncalls", [])

# ---------------------------------------------------------------------------
# AppleScript helpers
# ---------------------------------------------------------------------------
def run_applescript(script):
    """Run an AppleScript and return stdout."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True
    )
    if result.returncode != 0 and result.stderr.strip():
        # Some warnings are benign; only fail on real errors
        err = result.stderr.strip()
        if "execution error" in err.lower():
            print(f"AppleScript error: {err}")
            return None
    return result.stdout.strip()

def ensure_calendar_exists():
    """Create the dedicated calendar if it doesn't already exist."""
    script = f'''
        tell application "Calendar"
            try
                set cal to calendar "{CALENDAR_NAME}"
            on error
                make new calendar with properties {{name:"{CALENDAR_NAME}"}}
            end try
        end tell
    '''
    run_applescript(script)
    print(f"Calendar '{CALENDAR_NAME}' ready.")

def get_existing_event_uids():
    """Get all event UIDs managed by this script (identified by EVENT_TAG in notes)."""
    script = f'''
        tell application "Calendar"
            set cal to calendar "{CALENDAR_NAME}"
            set uids to {{}}
            repeat with evt in (every event of cal whose description contains "{EVENT_TAG}")
                set end of uids to uid of evt
            end repeat
            set AppleScript's text item delimiters to ","
            return uids as text
        end tell
    '''
    result = run_applescript(script)
    if not result:
        return set()
    return set(uid.strip() for uid in result.split(",") if uid.strip())

def get_event_uid_for_shift(shift_key):
    """Check if a specific shift already exists, return its UID."""
    script = f'''
        tell application "Calendar"
            set cal to calendar "{CALENDAR_NAME}"
            set evts to (every event of cal whose description contains "{shift_key}")
            if (count of evts) > 0 then
                return uid of item 1 of evts
            else
                return ""
            end if
        end tell
    '''
    result = run_applescript(script)
    return result if result else None

def format_applescript_date(iso_str):
    """Convert ISO 8601 to a format AppleScript can parse via shell date command.

    We use 'date' to create an AppleScript-compatible date object because
    AppleScript date parsing is locale-dependent and unreliable.
    """
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    # Convert to local time for Apple Calendar
    local_dt = dt.astimezone()
    return local_dt.strftime("%Y-%m-%dT%H:%M:%S")

def create_event(summary, start_iso, end_iso, shift_key, description):
    """Create a calendar event in Apple Calendar."""
    start_local = format_applescript_date(start_iso)
    end_local = format_applescript_date(end_iso)

    # Escape special characters for AppleScript strings
    desc_escaped = description.replace('\\', '\\\\').replace('"', '\\"')
    summary_escaped = summary.replace('\\', '\\\\').replace('"', '\\"')

    script = f'''
        use scripting additions
        use framework "Foundation"

        on makeDate(dateString)
            set formatter to current application's NSDateFormatter's alloc()'s init()
            formatter's setDateFormat:"yyyy-MM-dd'T'HH:mm:ss"
            set nsDate to formatter's dateFromString:dateString
            return nsDate as date
        end makeDate

        tell application "Calendar"
            set cal to calendar "{CALENDAR_NAME}"
            set startDate to my makeDate("{start_local}")
            set endDate to my makeDate("{end_local}")
            make new event at end of events of cal with properties {{summary:"{summary_escaped}", start date:startDate, end date:endDate, description:"{desc_escaped}", allday event:false}}
        end tell
    '''
    result = run_applescript(script)
    return result is not None

def delete_event_by_uid(uid):
    """Delete a calendar event by UID."""
    uid_escaped = uid.replace('"', '\\"')
    script = f'''
        tell application "Calendar"
            set cal to calendar "{CALENDAR_NAME}"
            set evts to (every event of cal whose uid is "{uid_escaped}")
            repeat with evt in evts
                delete evt
            end repeat
        end tell
    '''
    run_applescript(script)

# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------
def make_shift_key(schedule_id, start, end):
    """Create a unique key for a shift to detect duplicates."""
    return f"{EVENT_TAG}|{start}|{end}"

def sync(dry_run=False):
    print(f"Fetching on-call shifts for the next {LOOKAHEAD_DAYS} days...")
    oncalls = fetch_oncalls()
    print(f"Found {len(oncalls)} shift(s) from PagerDuty.")

    ensure_calendar_exists()

    # Track which shift keys we see from PagerDuty
    current_shift_keys = set()
    # Track start/end pairs we've already processed to deduplicate
    # across escalation policy layers
    seen_time_ranges = set()
    created = 0
    skipped = 0

    for entry in oncalls:
        start = entry.get("start")
        end = entry.get("end")
        if not start or not end:
            continue

        # Deduplicate: the API returns one entry per escalation layer,
        # but we only need one calendar event per time range
        time_range = (start, end)
        if time_range in seen_time_ranges:
            continue
        seen_time_ranges.add(time_range)

        shift_key = make_shift_key(SCHEDULE_ID, start, end)
        current_shift_keys.add(shift_key)

        # Check if this shift already exists
        existing_uid = get_event_uid_for_shift(shift_key)
        if existing_uid:
            skipped += 1
            continue

        summary = ONCALL_EVENT_TITLE
        # The shift_key is stored in the description so the script can
        # identify its own events on future runs. It's hidden from the
        # normal calendar view.
        description = shift_key

        if dry_run:
            created += 1
            print(f"  Would add: {summary} ({start} → {end})")
        else:
            if create_event(summary, start, end, shift_key, description):
                created += 1
                print(f"  Created: {summary} ({start} → {end})")
            else:
                print(f"  Failed to create: {summary}")

    # Remove stale events that are no longer in PagerDuty
    print("Checking for stale events...")
    existing_uids = get_existing_event_uids()
    removed = 0

    # For each existing event, check if its shift_key is still current
    # We do this by checking each existing event's description
    script = f'''
        tell application "Calendar"
            set cal to calendar "{CALENDAR_NAME}"
            set evts to every event of cal whose description contains "{EVENT_TAG}"
            set info to {{}}
            repeat with evt in evts
                set end of info to (uid of evt) & "|SPLIT|" & (description of evt)
            end repeat
            set AppleScript's text item delimiters to "|||"
            return info as text
        end tell
    '''
    result = run_applescript(script)

    if result:
        for item in result.split("|||"):
            item = item.strip()
            if "|SPLIT|" not in item:
                continue
            uid, desc = item.split("|SPLIT|", 1)
            # Extract the shift key (first line of description)
            desc_key = desc.split("\\n")[0].split("\n")[0].strip()
            if desc_key and desc_key not in current_shift_keys:
                if dry_run:
                    removed += 1
                    print(f"  Would remove stale event: {uid}")
                else:
                    delete_event_by_uid(uid)
                    removed += 1
                    print(f"  Removed stale event: {uid}")

    print()
    if dry_run:
        print(f"Dry run: {created} to add, {skipped} already exist, {removed} stale to remove.")
        print("No changes were made. Run without --dry-run to apply.")
    else:
        print(f"Sync complete: {created} created, {skipped} already existed, {removed} removed.")

# ---------------------------------------------------------------------------
# Purge — remove all events created by this script
# ---------------------------------------------------------------------------
def purge(dry_run=False):
    """Remove ALL events created by this script from the calendar."""
    ensure_calendar_exists()

    script = f'''
        tell application "Calendar"
            set cal to calendar "{CALENDAR_NAME}"
            set evts to every event of cal whose description contains "{EVENT_TAG}"
            set info to {{}}
            repeat with evt in evts
                set end of info to (uid of evt) & "|SPLIT|" & (summary of evt)
            end repeat
            set AppleScript's text item delimiters to "|||"
            return info as text
        end tell
    '''
    result = run_applescript(script)

    if not result:
        print("No events found to remove. Calendar is clean.")
        return

    items = [i.strip() for i in result.split("|||") if "|SPLIT|" in i]

    if not items:
        print("No events found to remove. Calendar is clean.")
        return

    print(f"Found {len(items)} event(s) created by this script:")
    for item in items:
        uid, summary = item.split("|SPLIT|", 1)
        print(f"  - {summary.strip()}")

    if dry_run:
        print()
        print("Dry run — no events were deleted. Run without --dry-run to delete.")
        return

    for item in items:
        uid, summary = item.split("|SPLIT|", 1)
        delete_event_by_uid(uid)
        print(f"  Deleted: {summary.strip()}")

    print(f"\nPurge complete: {len(items)} event(s) removed.")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sync PagerDuty on-call shifts to Apple Calendar."
    )
    parser.add_argument(
        "--purge", action="store_true",
        help="Remove ALL events created by this script from the calendar."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would happen without making changes."
    )
    args = parser.parse_args()

    if args.purge:
        purge(dry_run=args.dry_run)
    else:
        sync(dry_run=args.dry_run)