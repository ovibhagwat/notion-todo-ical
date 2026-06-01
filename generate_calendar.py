import os
import requests
from datetime import datetime, timedelta, date, timezone
from dateutil.parser import isoparse

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

NOTION_VERSION = "2022-06-28"

PROPERTY_NAME = "Name"
PROPERTY_STATUS = "Status"
PROPERTY_AREA = "Area"
PROPERTY_DUE_DATE = "Due Date"
PROPERTY_ASSIGNEE = "Assignee"

STATUS_EMOJIS = {
    "To Do": "⚪",
    "In progress": "🔵",
    "Done": "🟢",
    "Did Not Do": "❌",
}

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


def escape_ics_text(value):
    if value is None:
        return ""
    value = str(value)
    value = value.replace("\\", "\\\\")
    value = value.replace(";", "\\;")
    value = value.replace(",", "\\,")
    value = value.replace("\n", "\\n")
    return value


def get_title(page):
    title_items = page["properties"].get(PROPERTY_NAME, {}).get("title", [])
    return "".join(item.get("plain_text", "") for item in title_items).strip() or "Untitled"


def get_status(page):
    prop = page["properties"].get(PROPERTY_STATUS, {})

    if prop.get("type") == "status" and prop.get("status"):
        return prop["status"]["name"]

    if prop.get("type") == "select" and prop.get("select"):
        return prop["select"]["name"]

    return ""


def get_area(page):
    prop = page["properties"].get(PROPERTY_AREA, {})

    if prop.get("type") == "select" and prop.get("select"):
        return prop["select"]["name"]

    return ""


def get_assignees(page):
    prop = page["properties"].get(PROPERTY_ASSIGNEE, {})
    people = prop.get("people", [])
    names = [p.get("name", "") for p in people if p.get("name")]
    return ", ".join(names)


def get_due_date(page):
    prop = page["properties"].get(PROPERTY_DUE_DATE, {})
    return prop.get("date")


def query_notion_pages():
    pages = []
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"

    payload = {
        "filter": {
            "property": PROPERTY_DUE_DATE,
            "date": {
                "is_not_empty": True
            }
        },
        "page_size": 100
    }

    while True:
        response = requests.post(url, headers=HEADERS, json=payload)
        response.raise_for_status()
        data = response.json()

        pages.extend(data.get("results", []))

        if not data.get("has_more"):
            break

        payload["start_cursor"] = data["next_cursor"]

    return pages


def parse_date_or_datetime(value):
    if "T" not in value:
        return date.fromisoformat(value), True

    return isoparse(value), False


def format_utc_datetime(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def format_date_only(d):
    return d.strftime("%Y%m%d")


def build_event(page):
    due = get_due_date(page)

    if not due or not due.get("start"):
        return ""

    name = get_title(page)
    status = get_status(page)
    area = get_area(page)
    assignees = get_assignees(page)
    notion_url = page.get("url", "")

    emoji = STATUS_EMOJIS.get(status, "•")
    area_prefix = f"[{area}] " if area else ""
    summary = f"{emoji} {area_prefix}{name}"

    description_lines = [
        f"Status: {status}" if status else "",
        f"Area: {area}" if area else "",
        f"Assignee: {assignees}" if assignees else "",
        f"Notion: {notion_url}" if notion_url else "",
    ]

    description = "\\n".join(line for line in description_lines if line)

    uid = f"{page['id']}@notion-todo-calendar"
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    start_raw = due["start"]
    end_raw = due.get("end")

    start_value, start_is_date_only = parse_date_or_datetime(start_raw)

    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"SUMMARY:{escape_ics_text(summary)}",
        f"DESCRIPTION:{escape_ics_text(description)}",
        f"URL:{notion_url}",
    ]

    # Your rule:
    # If Due Date has no end, make it an all-day event.
    if not end_raw:
        if isinstance(start_value, datetime):
            event_date = start_value.date()
        else:
            event_date = start_value

        lines.append(f"DTSTART;VALUE=DATE:{format_date_only(event_date)}")
        lines.append(f"DTEND;VALUE=DATE:{format_date_only(event_date + timedelta(days=1))}")

    else:
        end_value, end_is_date_only = parse_date_or_datetime(end_raw)

        # If both start and end are date-only, make a multi-day all-day event.
        if start_is_date_only and end_is_date_only:
            lines.append(f"DTSTART;VALUE=DATE:{format_date_only(start_value)}")
            lines.append(f"DTEND;VALUE=DATE:{format_date_only(end_value + timedelta(days=1))}")

        # If there is a real start and end time, use the actual timed duration.
        else:
            if not isinstance(start_value, datetime):
                start_value = datetime.combine(start_value, datetime.min.time()).replace(tzinfo=timezone.utc)

            if not isinstance(end_value, datetime):
                end_value = datetime.combine(end_value, datetime.min.time()).replace(tzinfo=timezone.utc)

            lines.append(f"DTSTART:{format_utc_datetime(start_value)}")
            lines.append(f"DTEND:{format_utc_datetime(end_value)}")

    lines.append("END:VEVENT")
    return "\n".join(lines)


def generate_calendar():
    pages = query_notion_pages()
    events = [build_event(page) for page in pages]
    events = [event for event in events if event]

    calendar = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Ovi Notion Todo Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Notion Personal Todo",
        "X-PUBLISHED-TTL:PT1H",
        *events,
        "END:VCALENDAR",
        ""
    ]

    os.makedirs("docs", exist_ok=True)

    with open("docs/calendar.ics", "w", encoding="utf-8") as f:
        f.write("\n".join(calendar))


if __name__ == "__main__":
    generate_calendar()
