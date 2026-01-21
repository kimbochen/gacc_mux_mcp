import json
import os
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from mcp.server.fastmcp import FastMCP

# Scopes - Gmail read-only, Calendar full access
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
]

CREDENTIALS_PATH = Path(__file__).parent / "credentials.json"

# Account configurations (file-based for local dev)
ACCOUNTS = {
    "personal": Path(__file__).parent / "token_personal.json",
    "school": Path(__file__).parent / "token_school.json",
    "work": Path(__file__).parent / "token_work.json",
}

# Environment variable names for deployed tokens
ENV_TOKENS = {
    "personal": "GOOGLE_TOKEN_PERSONAL",
    "school": "GOOGLE_TOKEN_SCHOOL",
    "work": "GOOGLE_TOKEN_WORK",
}


def get_credentials(account: str = "personal") -> Credentials:
    """Get or refresh Google credentials for a specific account."""
    if account not in ACCOUNTS:
        raise ValueError(f"Unknown account: {account}. Valid accounts: {list(ACCOUNTS.keys())}")

    creds = None

    # First, try environment variable (for deployed environments)
    env_var = ENV_TOKENS.get(account)
    if env_var and os.environ.get(env_var):
        token_data = json.loads(os.environ[env_var])
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
    # Fall back to file-based tokens (for local development)
    elif ACCOUNTS[account].exists():
        creds = Credentials.from_authorized_user_file(str(ACCOUNTS[account]), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Only allow interactive flow in local development
            if os.environ.get(env_var):
                raise RuntimeError(f"Token for {account} is invalid and cannot be refreshed in deployed environment")
            print(f"Please authorize the {account} account in your browser...", file=__import__('sys').stderr)
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
            ACCOUNTS[account].write_text(creds.to_json())

    return creds

def get_gmail(account: str = "personal"):
    return build("gmail", "v1", credentials=get_credentials(account))

def get_calendar(account: str = "personal"):
    return build("calendar", "v3", credentials=get_credentials(account))

# Initialize MCP server
mcp = FastMCP("gmail-calendar-mcp")

# ============== Gmail Tools ==============

@mcp.tool()
def gmail_search(query: str, max_results: int = 10, account: str = "personal") -> str:
    """Search Gmail messages. Use Gmail search syntax (e.g., 'from:someone@example.com', 'subject:hello', 'is:unread'). Account can be 'personal' or 'school'."""
    gmail = get_gmail(account)
    results = gmail.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    messages = results.get("messages", [])
    
    if not messages:
        return "No messages found."
    
    output = []
    for msg in messages:
        detail = gmail.users().messages().get(userId="me", id=msg["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"]).execute()
        headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        output.append({
            "id": msg["id"],
            "threadId": msg["threadId"],
            "from": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "snippet": detail.get("snippet", "")
        })
    
    return json.dumps(output, indent=2)


@mcp.tool()
def gmail_read_thread(thread_id: str, account: str = "personal") -> str:
    """Read a full Gmail thread by ID. Account can be 'personal' or 'school'."""
    gmail = get_gmail(account)
    thread = gmail.users().threads().get(userId="me", id=thread_id, format="full").execute()
    
    messages = []
    for msg in thread.get("messages", []):
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        
        # Extract body
        body = ""
        payload = msg.get("payload", {})
        if "body" in payload and payload["body"].get("data"):
            import base64
            body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
        elif "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                    import base64
                    body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
                    break
        
        messages.append({
            "id": msg["id"],
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "body": body[:5000]  # Truncate long bodies
        })
    
    return json.dumps(messages, indent=2)


@mcp.tool()
def gmail_get_profile(account: str = "personal") -> str:
    """Get the authenticated user's Gmail profile. Account can be 'personal' or 'school'."""
    gmail = get_gmail(account)
    profile = gmail.users().getProfile(userId="me").execute()
    return json.dumps(profile, indent=2)

# ============== Calendar Tools ==============

@mcp.tool()
def calendar_list(account: str = "personal") -> str:
    """List all calendars the user has access to. Account can be 'personal' or 'school'."""
    cal = get_calendar(account)
    results = cal.calendarList().list().execute()
    calendars = results.get("items", [])
    
    output = []
    for c in calendars:
        output.append({
            "id": c["id"],
            "summary": c.get("summary", ""),
            "accessRole": c.get("accessRole", ""),
            "primary": c.get("primary", False)
        })
    
    return json.dumps(output, indent=2)


@mcp.tool()
def calendar_list_events(
    calendar_id: str = "primary",
    time_min: str = None,
    time_max: str = None,
    max_results: int = 25,
    query: str = None,
    account: str = "personal"
) -> str:
    """List events from a calendar. time_min and time_max should be RFC3339 format (e.g., '2025-01-20T00:00:00Z'). Account can be 'personal' or 'school'."""
    cal = get_calendar(account)
    
    kwargs = {
        "calendarId": calendar_id,
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime"
    }
    if time_min:
        kwargs["timeMin"] = time_min
    if time_max:
        kwargs["timeMax"] = time_max
    if query:
        kwargs["q"] = query
    
    results = cal.events().list(**kwargs).execute()
    events = results.get("items", [])
    
    output = []
    for e in events:
        output.append({
            "id": e["id"],
            "summary": e.get("summary", "(No title)"),
            "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"),
            "end": e.get("end", {}).get("dateTime") or e.get("end", {}).get("date"),
            "location": e.get("location", ""),
            "description": e.get("description", "")[:500] if e.get("description") else ""
        })
    
    return json.dumps(output, indent=2)


@mcp.tool()
def calendar_create_event(
    summary: str,
    start: str,
    end: str,
    calendar_id: str = "primary",
    description: str = None,
    location: str = None,
    timezone: str = "UTC",
    account: str = "personal"
) -> str:
    """Create a calendar event. start and end should be RFC3339 format (e.g., '2025-01-20T10:00:00') or date format for all-day events ('2025-01-20'). Account can be 'personal' or 'school'."""
    cal = get_calendar(account)
    
    # Determine if all-day event
    is_all_day = len(start) == 10  # 'YYYY-MM-DD' format
    
    event = {
        "summary": summary,
        "start": {"date": start, "timeZone": timezone} if is_all_day else {"dateTime": start, "timeZone": timezone},
        "end": {"date": end, "timeZone": timezone} if is_all_day else {"dateTime": end, "timeZone": timezone},
    }
    if description:
        event["description"] = description
    if location:
        event["location"] = location
    
    result = cal.events().insert(calendarId=calendar_id, body=event).execute()
    return json.dumps({"id": result["id"], "htmlLink": result.get("htmlLink", "")}, indent=2)


@mcp.tool()
def calendar_update_event(
    event_id: str,
    calendar_id: str = "primary",
    summary: str = None,
    start: str = None,
    end: str = None,
    description: str = None,
    location: str = None,
    timezone: str = "UTC",
    account: str = "personal"
) -> str:
    """Update an existing calendar event. Only provided fields will be updated. Account can be 'personal' or 'school'."""
    cal = get_calendar(account)
    
    # Get existing event
    event = cal.events().get(calendarId=calendar_id, eventId=event_id).execute()
    
    if summary:
        event["summary"] = summary
    if description is not None:
        event["description"] = description
    if location is not None:
        event["location"] = location
    if start:
        is_all_day = len(start) == 10
        event["start"] = {"date": start, "timeZone": timezone} if is_all_day else {"dateTime": start, "timeZone": timezone}
    if end:
        is_all_day = len(end) == 10
        event["end"] = {"date": end, "timeZone": timezone} if is_all_day else {"dateTime": end, "timeZone": timezone}
    
    result = cal.events().update(calendarId=calendar_id, eventId=event_id, body=event).execute()
    return json.dumps({"id": result["id"], "updated": result.get("updated", "")}, indent=2)


@mcp.tool()
def calendar_delete_event(event_id: str, calendar_id: str = "primary", account: str = "personal") -> str:
    """Delete a calendar event. Account can be 'personal' or 'school'."""
    cal = get_calendar(account)
    cal.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    return json.dumps({"deleted": True, "event_id": event_id})


if __name__ == "__main__":
    import sys
    # Use SSE transport for remote deployment, stdio for local
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "sse":
        port = os.environ.get("PORT", "8000")
        print(f"Starting MCP server on 0.0.0.0:{port}", flush=True)
        sys.stdout.flush()
        # FastMCP reads HOST/PORT from environment variables
        os.environ.setdefault("HOST", "0.0.0.0")
        mcp.run(transport="sse")
    else:
        mcp.run()
