"""
Gmail Unsubscribe Harvester, Clicker & Cleaner

HOW TO CONFIGURE YOUR CREDENTIALS VIA .ENV:
1. Create or edit the '.env' file in this directory.
2. Put your Gmail address and 16-character Google App Password inside:
     GMAIL_USER=your_email@gmail.com
     GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
   (This avoids having to enter passwords through the terminal).

HOW TO GET A GOOGLE APP PASSWORD:
1. Go to your Google Account settings: https://myaccount.google.com/
2. In the left navigation, select 'Security'.
3. Under 'How you sign in to Google', ensure '2-Step Verification' is turned ON.
   (Google will NOT allow you to generate an App Password without 2FA enabled).
4. In the top search bar, search for "App passwords"
   (or visit directly: https://myaccount.google.com/apppasswords).
5. Under 'App name', enter a descriptive name (e.g. 'Gmail Harvester' or 'Python').
6. Click 'Create'.
7. A 16-character code will appear (e.g., 'abcd efgh ijkl mnop').
   Copy this code into your .env file as GMAIL_APP_PASSWORD.
"""

import concurrent.futures
from datetime import datetime, timezone
import email
from email.header import decode_header
import email.utils
import getpass
import imaplib
import os
import re
import sys
import threading
import time
from urllib.parse import urlparse
import webbrowser

from bs4 import BeautifulSoup

try:
    import requests
except ImportError:
    requests = None
    import urllib.request
    import urllib.error


def load_env_file():
    """
    Loads environment variables from .env file if present.
    Supports python-dotenv if installed, with a built-in parser fallback.
    """
    # 1. Try python-dotenv first
    try:
        from dotenv import load_dotenv
        script_dir = os.path.dirname(os.path.abspath(__file__))
        script_env = os.path.join(script_dir, ".env")
        if os.path.exists(script_env):
            load_dotenv(dotenv_path=script_env)
            return
        load_dotenv()
        return
    except ImportError:
        pass

    # 2. Built-in parser fallback
    candidate_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]
    for env_path in candidate_paths:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip().strip('"').strip("'")
                            if k and k not in os.environ:
                                os.environ[k] = v
                break
            except Exception:
                pass


# Load .env immediately on import/execution
load_env_file()

IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993

# Rate-limit & Chunking configuration for Google Pro / Workspace IMAP:
# - Google IMAP daily download bandwidth limit: 2,500 MB (2.5 GB) / day
# - Google max concurrent connections: 15
# - IMAP command buffer: safe batch sizes are 25-100 IDs per FETCH
DEFAULT_CHUNK_SIZE = int(os.environ.get("EMAIL_CHUNK_SIZE", "50"))
DEFAULT_CHUNK_DELAY = float(os.environ.get("CHUNK_DELAY", "0.05"))


def chunk_list(items, chunk_size):
    """
    Yields successive slices of size `chunk_size` from list `items`.
    Guarantees non-empty chunks and handles invalid chunk sizes gracefully.
    """
    if not items:
        return
    if chunk_size is None or chunk_size <= 0:
        chunk_size = DEFAULT_CHUNK_SIZE
    for i in range(0, len(items), chunk_size):
        yield items[i : i + chunk_size]


def parse_fetch_batch_data(data, use_uid=True):
    """
    Parses untagged IMAP FETCH responses returned from multi-message queries.
    Returns a list of tuples: [(msg_id_or_uid, raw_bytes), ...]
    """
    results = []
    if not data:
        return results

    for item in data:
        if isinstance(item, tuple) and len(item) >= 2:
            meta = item[0]
            raw_content = item[1]
            if not isinstance(raw_content, (bytes, bytearray)):
                continue

            msg_id = None
            if use_uid and isinstance(meta, (bytes, bytearray)):
                match = re.search(rb"\bUID\s+(\d+)", meta)
                if match:
                    msg_id = match.group(1)

            if not msg_id and isinstance(meta, (bytes, bytearray)):
                tokens = meta.split()
                if tokens:
                    msg_id = tokens[0].lstrip(b"*").strip()
            elif not msg_id and isinstance(meta, str):
                tokens = meta.split()
                if tokens:
                    msg_id = tokens[0].lstrip("*").strip().encode("ascii")

            if msg_id:
                results.append((msg_id, bytes(raw_content)))

    return results


def execute_imap_chunk_with_retry(
    fetch_fn, max_retries=3, initial_backoff=1.0, reconnect_fn=None
):
    """
    Executes an IMAP chunk fetch function with exponential backoff and automatic reconnection.
    Protects against Google IMAP rate limits, transient connection resets, and socket timeouts.
    """
    attempt = 0
    while True:
        try:
            status, data = fetch_fn()
            if status == "OK" and data:
                return status, data
            if status in ("NO", "BAD"):
                raise imaplib.IMAP4.error(f"IMAP returned {status}: {data}")
            return status, data
        except Exception as e:
            attempt += 1
            if attempt > max_retries:
                print(f"\nWarning: IMAP chunk fetch failed after {max_retries} attempts: {e}")
                return "ERROR", []
            backoff = initial_backoff * (2 ** (attempt - 1))
            print(
                f"\nNotice: IMAP rate limit or connection issue ({type(e).__name__}: {e}). "
                f"Reconnecting and backing off for {backoff:.1f}s (retry {attempt}/{max_retries})..."
            )
            time.sleep(backoff)
            if reconnect_fn:
                try:
                    reconnect_fn()
                except Exception as rec_err:
                    print(f"Notice: Reconnect attempt failed: {rec_err}")



def create_loading_bar(current, total, label="", bar_length=20):
    """
    Creates and returns a formatted loading bar string matching the format:
    [=======-------------] <label>
    """
    if total <= 0:
        percent = 1.0
    else:
        percent = min(1.0, max(0.0, current / total))

    filled = int(bar_length * percent)
    bar = "=" * filled + "-" * (bar_length - filled)
    if label:
        return f"[{bar}] {label}"
    return f"[{bar}]"


def render_progress_bar(current, total, current_domain="", bar_length=20):
    """
    Renders an in-place loading bar matching format:
    [=======-------------] <current_domain>
    Overwrites the line using carriage return to prevent clogging the terminal.
    """
    bar_str = create_loading_bar(current, total, label=current_domain, bar_length=bar_length)
    line = f"\r{bar_str}"
    # Pad to 80 characters to erase any longer domain from previous update
    sys.stdout.write(line.ljust(80))
    sys.stdout.flush()


def finish_progress_bar(summary=""):
    """
    Clears the single-line progress bar and prints a summary message.
    """
    sys.stdout.write("\r" + " " * 80 + "\r")
    if summary:
        print(summary)
    sys.stdout.flush()


create_progress_bar = create_loading_bar
render_loading_bar = render_progress_bar




COMMON_TWO_PART_TLDS = {
    "co.uk", "org.uk", "gov.uk", "ac.uk", "net.uk",
    "com.au", "net.au", "org.au", "edu.au",
    "co.nz", "net.nz", "org.nz",
    "co.jp", "ne.jp", "or.jp",
    "com.br", "net.br", "org.br",
    "co.in", "net.in", "org.in",
    "com.sg", "edu.sg",
}


def decode_email_subject(raw_subject):
    """Decodes MIME encoded-words in Subject header (e.g. =?UTF-8?B?...?=)."""
    if not raw_subject:
        return "(No Subject)"
    try:
        decoded_fragments = decode_header(raw_subject)
        pieces = []
        for text, encoding in decoded_fragments:
            if isinstance(text, bytes):
                pieces.append(text.decode(encoding or "utf-8", errors="ignore"))
            else:
                pieces.append(str(text))
        return "".join(pieces).strip()
    except Exception:
        return str(raw_subject)


def extract_domain(from_header):
    """Extracts the base domain from a From header string."""
    if not from_header:
        return None
    match = re.search(r"@([\w.-]+)", from_header)
    if match:
        full_domain = match.group(1).lower().rstrip(">")
        parts = full_domain.split(".")
        if len(parts) > 2:
            tld2 = f"{parts[-2]}.{parts[-1]}"
            if tld2 in COMMON_TWO_PART_TLDS:
                return ".".join(parts[-3:]) if len(parts) >= 3 else full_domain
            return ".".join(parts[-2:])
        return full_domain
    return None


def extract_unsubscribe_link(msg):
    """
    Looks for an unsubscribe link in headers first (List-Unsubscribe),
    then in HTML body (anchors, images, buttons), then in text/plain.
    Returns the URL string if found, otherwise None.
    """
    # 1. RFC 2369 List-Unsubscribe Header
    list_unsub = msg.get("List-Unsubscribe")
    if list_unsub:
        http_links = re.findall(r"<(https?://[^>]+)>", list_unsub)
        if http_links:
            return http_links[0]
        raw_http = re.findall(r"https?://[^\s>,]+", list_unsub)
        if raw_http:
            return raw_http[0]

    # 2. Body HTML Fallback (looks for <a> tags with 'unsubscribe', 'opt-out', etc.)
    for part in msg.walk():
        content_type = part.get_content_type()
        if content_type == "text/html":
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                html = payload.decode(
                    part.get_content_charset() or "utf-8", errors="ignore"
                )
                soup = BeautifulSoup(html, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    text = a.get_text().strip().lower()
                    title = a.get("title", "").strip().lower()
                    img_alt = " ".join(
                        img.get("alt", "").lower()
                        for img in a.find_all("img", alt=True)
                    )
                    combined_text = f"{text} {title} {img_alt}"
                    keywords = (
                        "unsubscribe",
                        "opt-out",
                        "opt out",
                        "optout",
                        "manage preferences",
                        "email preferences",
                        "update preferences",
                    )
                    if any(k in combined_text for k in keywords) or any(
                        k in href.lower() for k in ("unsubscribe", "opt-out", "optout")
                    ):
                        if href.startswith("http"):
                            return href
            except Exception:
                continue
        elif content_type == "text/plain":
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                text = payload.decode(
                    part.get_content_charset() or "utf-8", errors="ignore"
                )
                match = re.search(
                    r"(?:unsubscribe|opt-out|opt out)[^\n]*?(https?://\S+)",
                    text,
                    re.IGNORECASE,
                )
                if match:
                    return match.group(1).rstrip(".)]>\"'")
            except Exception:
                continue
    return None


def parse_email_date(date_str):
    """
    Parses an RFC 2822/5322 email Date header into a timezone-aware UTC datetime.
    Returns datetime or None if parsing fails.
    """
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        dt = email.utils.parsedate_to_datetime(date_str)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except Exception:
        pass

    try:
        parsed = email.utils.parsedate_tz(date_str)
        if parsed is not None:
            timestamp = email.utils.mktime_tz(parsed)
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except Exception:
        pass

    return None


def is_newer_email_date(new_date_str, current_date_str, new_uid=None, current_uid=None):
    """
    Compares two email dates to determine if new_date_str is strictly newer than current_date_str.
    Falls back to UID comparison (if numeric) if dates are equal or cannot be parsed.
    """
    if not current_date_str:
        return bool(new_date_str)
    if not new_date_str:
        return False

    dt_new = parse_email_date(new_date_str)
    dt_current = parse_email_date(current_date_str)

    if dt_new is not None and dt_current is not None:
        if dt_new > dt_current:
            return True
        elif dt_new < dt_current:
            return False
        # Dates are identical; fall through to UID tie-breaker
    elif dt_new is not None:
        return True
    elif dt_current is not None:
        return False

    # Tie-breaker: higher numeric UID is newer if both can be converted to int
    if new_uid is not None and current_uid is not None:
        try:
            n_val = int(new_uid.decode() if isinstance(new_uid, bytes) else str(new_uid))
            c_val = int(current_uid.decode() if isinstance(current_uid, bytes) else str(current_uid))
            return n_val > c_val
        except (ValueError, TypeError):
            pass

    return False


class UnsubscribeEntry(dict):
    """
    Stores unsubscribe details for a domain:
    - 'link': the newest unsubscribe URL string
    - 'date': RFC 2822 date string of the email the link was obtained from
    - 'emails': list of email records [{'uid': ..., 'subject': ..., 'date': ..., 'link': ...}, ...]
    - 'uid': message UID/ID corresponding to the newest link

    Inherits from dict so entry['link'], entry['date'], entry['emails'] work directly.
    Also equality comparison with a string URL (e.g. entry == "https://...") returns True
    if entry['link'] == url, maintaining full backward compatibility with callers and tests.
    """

    def __init__(self, link, date="", emails=None, uid=None, **kwargs):
        super().__init__(
            link=link,
            date=date or "",
            emails=list(emails) if emails is not None else [],
            uid=uid,
            **kwargs,
        )

    @property
    def link(self):
        return self.get("link")

    @property
    def date(self):
        return self.get("date")

    @property
    def emails(self):
        return self.get("emails", [])

    def __eq__(self, other):
        if isinstance(other, str):
            return self.get("link") == other
        return super().__eq__(other)

    def __str__(self):
        return str(self.get("link", ""))


class UnsubscribeMap(dict):
    """
    Mapping of domain -> UnsubscribeEntry.
    Provides convenience properties:
    - emails: flattened list of all emails containing unsubscribe buttons across all domains
    - domain_emails: mapping of domain -> list of email records
    """

    @property
    def emails(self):
        """Returns all email records containing unsubscribe buttons across all domains."""
        all_msgs = []
        for entry in self.values():
            if isinstance(entry, dict) and "emails" in entry:
                all_msgs.extend(entry["emails"])
        return all_msgs

    @property
    def domain_emails(self):
        """Returns a dict mapping domain -> list of email records."""
        result = {}
        for domain, entry in self.items():
            if isinstance(entry, dict) and "emails" in entry:
                result[domain] = entry["emails"]
            elif isinstance(entry, list):
                result[domain] = entry
            else:
                result[domain] = []
        return result


def update_unsubscribe_hash_entry(
    unsub_map,
    domain,
    link,
    date_str,
    email_record=None,
    lock=None,
):
    """
    Updates or inserts a domain entry in unsub_map when encountering an unsubscribe link:
    - Guarantees thread-safety via lock if provided.
    - Compares date to ensure the newest link is saved for the domain.
    - Appends email_record to the domain's list of emails containing unsubscribe buttons.
    Returns True if the domain's primary link was updated/inserted, False otherwise.
    """
    if not domain or not link:
        return False

    def _do_update():
        current_entry = unsub_map.get(domain)
        updated = False

        if current_entry is None:
            # First time domain is encountered
            emails_list = [email_record] if email_record else []
            unsub_map[domain] = UnsubscribeEntry(
                link=link,
                date=date_str or "",
                emails=emails_list,
                uid=email_record.get("uid") if email_record else None,
            )
            updated = True
        else:
            # Domain already in hash: record the email in emails list
            if email_record:
                existing_uids = {
                    e.get("uid") for e in current_entry.get("emails", []) if e.get("uid") is not None
                }
                if email_record.get("uid") not in existing_uids:
                    current_entry.setdefault("emails", []).append(email_record)

            # Compare date to guarantee the newest link is saved
            current_date = current_entry.get("date")
            current_uid = current_entry.get("uid")
            new_uid = email_record.get("uid") if email_record else None

            if is_newer_email_date(date_str, current_date, new_uid=new_uid, current_uid=current_uid):
                current_entry["link"] = link
                current_entry["date"] = date_str or ""
                if new_uid is not None:
                    current_entry["uid"] = new_uid
                updated = True

        return updated

    if lock is not None:
        with lock:
            return _do_update()
    else:
        return _do_update()


def process_chunk(
    chunk,
    mail_holder,
    domain_unsub_map,
    use_uid=True,
    header_fields_query="(BODY.PEEK[HEADER.FIELDS (FROM LIST-UNSUBSCRIBE SUBJECT DATE)])",
    body_query="(BODY.PEEK[])",
    reconnect_fn=None,
    lock=None,
    on_message_callback=None,
    verbose=False,
    processed_counter=None,
    total_count=0,
):
    """
    Worker function to process a single chunk of email IDs:
    1. Fetches headers in a single multi-message FETCH.
    2. Identifies emails with List-Unsubscribe headers, compares dates, and updates domain_unsub_map.
    3. For messages lacking List-Unsubscribe header, fetches bodies in a single batch.
    4. Identifies emails with body unsubscribe links, compares dates, and updates domain_unsub_map.
    5. Accumulates all emails containing unsubscribe buttons in domain_unsub_map.
    """
    if not chunk:
        return

    id_set = (
        b",".join(chunk)
        if isinstance(chunk[0], bytes)
        else ",".join(str(x) for x in chunk).encode("ascii")
    )

    def do_fetch_headers():
        if use_uid:
            return mail_holder[0].uid("FETCH", id_set, header_fields_query)
        return mail_holder[0].fetch(id_set, header_fields_query)

    status, data = execute_imap_chunk_with_retry(
        do_fetch_headers, max_retries=3, reconnect_fn=reconnect_fn
    )

    parsed_headers = parse_fetch_batch_data(data, use_uid=use_uid)

    if status == "OK" and not parsed_headers:
        def do_fetch_fallback():
            if use_uid:
                return mail_holder[0].uid("FETCH", id_set, body_query)
            return mail_holder[0].fetch(id_set, body_query)

        status, data = execute_imap_chunk_with_retry(
            do_fetch_fallback, max_retries=3, reconnect_fn=reconnect_fn
        )
        parsed_headers = parse_fetch_batch_data(data, use_uid=use_uid)

    headers_dict = {}
    for m_id, raw_hdr in parsed_headers:
        b_id = m_id if isinstance(m_id, bytes) else str(m_id).encode("ascii")
        headers_dict[b_id] = raw_hdr
        try:
            s_id = m_id.decode("ascii") if isinstance(m_id, bytes) else str(m_id)
            headers_dict[s_id] = raw_hdr
        except Exception:
            pass

    ordered_chunk_items = []
    for orig_id in chunk:
        raw_h = headers_dict.get(orig_id)
        if raw_h is None and isinstance(orig_id, bytes):
            raw_h = headers_dict.get(orig_id.decode("ascii", errors="ignore"))
        if raw_h is None and isinstance(orig_id, str):
            raw_h = headers_dict.get(orig_id.encode("ascii"))
        if raw_h is not None:
            ordered_chunk_items.append((orig_id, raw_h))

    if not ordered_chunk_items:
        ordered_chunk_items = parsed_headers

    unresolved_for_body = []

    for m_id, raw_hdr in ordered_chunk_items:
        msg = email.message_from_bytes(raw_hdr)
        from_header = msg.get("From", "")
        domain = extract_domain(from_header)
        date_hdr = msg.get("Date", "")
        subj = decode_email_subject(msg.get("Subject", ""))

        if on_message_callback:
            on_message_callback(domain or "evaluating...")

        if not domain:
            continue

        unsub_link = extract_unsubscribe_link(msg)
        if unsub_link:
            email_rec = {
                "uid": m_id,
                "subject": subj,
                "date": date_hdr,
                "link": unsub_link,
                "domain": domain,
            }
            update_unsubscribe_hash_entry(
                domain_unsub_map,
                domain,
                unsub_link,
                date_hdr,
                email_record=email_rec,
                lock=lock,
            )
            if verbose and processed_counter:
                print(f"[{processed_counter[0]}/{total_count}] [FOUND] {domain} -> {unsub_link} (Date: {date_hdr})")
        else:
            unresolved_for_body.append((m_id, domain, subj, date_hdr))

    if unresolved_for_body:
        needed_uids = [item[0] for item in unresolved_for_body]
        unresolved_map = {}
        for item in unresolved_for_body:
            uid_key = item[0] if isinstance(item[0], bytes) else str(item[0]).encode("ascii")
            unresolved_map[uid_key] = item
            try:
                unresolved_map[item[0].decode("ascii") if isinstance(item[0], bytes) else str(item[0])] = item
            except Exception:
                pass

        body_id_set = (
            b",".join(needed_uids)
            if isinstance(needed_uids[0], bytes)
            else ",".join(str(x) for x in needed_uids).encode("ascii")
        )

        def do_fetch_bodies():
            if use_uid:
                return mail_holder[0].uid("FETCH", body_id_set, body_query)
            return mail_holder[0].fetch(body_id_set, body_query)

        b_status, b_data = execute_imap_chunk_with_retry(
            do_fetch_bodies, max_retries=3, reconnect_fn=reconnect_fn
        )
        parsed_bodies = parse_fetch_batch_data(b_data, use_uid=use_uid)
        for b_id, raw_body in parsed_bodies:
            full_msg = email.message_from_bytes(raw_body)
            orig_item = unresolved_map.get(b_id)
            if orig_item is None and isinstance(b_id, bytes):
                orig_item = unresolved_map.get(b_id.decode("ascii", errors="ignore"))
            if orig_item is None and isinstance(b_id, str):
                orig_item = unresolved_map.get(b_id.encode("ascii"))

            b_domain = orig_item[1] if orig_item else extract_domain(full_msg.get("From", ""))
            b_subj = orig_item[2] if orig_item else decode_email_subject(full_msg.get("Subject", ""))
            b_date = orig_item[3] if orig_item else full_msg.get("Date", "")

            if b_domain:
                body_unsub_link = extract_unsubscribe_link(full_msg)
                if body_unsub_link:
                    email_rec = {
                        "uid": b_id,
                        "subject": b_subj,
                        "date": b_date,
                        "link": body_unsub_link,
                        "domain": b_domain,
                    }
                    update_unsubscribe_hash_entry(
                        domain_unsub_map,
                        b_domain,
                        body_unsub_link,
                        b_date,
                        email_record=email_rec,
                        lock=lock,
                    )
                    if verbose:
                        print(f"[FOUND via Body] {b_domain} -> {body_unsub_link} (Date: {b_date})")


def connect_imap(username, password, mailbox="INBOX"):
    """Establishes an authenticated IMAP SSL connection to Gmail."""
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(username, password)
    mail.select(mailbox)
    return mail


def ensure_imap_connection(mail, username=None, password=None, mailbox="INBOX"):
    """Ensures IMAP connection is alive; reconnects if connection dropped."""
    try:
        mail.noop()
        return mail
    except Exception:
        print("\nIMAP connection timed out or closed. Reconnecting...")
        if username and password:
            return connect_imap(username, password, mailbox)
        return mail


def get_trash_folder(mail):
    """Detects Gmail's Trash/Bin folder name from mailbox list."""
    try:
        status, folders = mail.list()
        if status == "OK" and folders:
            for f in folders:
                if not f:
                    continue
                f_str = f.decode("utf-8", errors="ignore") if isinstance(f, bytes) else str(f)
                if "\\Trash" in f_str:
                    match = re.search(r'"([^"]+)"\s*$', f_str)
                    if match:
                        return match.group(1)
                    parts = f_str.split()
                    if parts:
                        return parts[-1].strip('"')
    except Exception as e:
        print(f"Notice: Could not automatically detect Trash folder ({e}).")
    return "[Gmail]/Trash"


def click_single_link(url, session=None, timeout=10):
    """
    Sends an HTTP request with a realistic browser User-Agent to the unsubscribe URL.
    Returns (success: bool, status_code: int|None, note: str).
    """
    if isinstance(url, dict):
        url = url.get("link", "")
    url = str(url).strip()
    if not url or not url.startswith("http"):
        return False, None, "Invalid or missing URL"

    user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    if requests is not None:
        if session is None:
            session = requests.Session()
        session.headers.update(headers)
        try:
            resp = session.get(url, timeout=timeout, allow_redirects=True)
            status = resp.status_code
            text_sample = resp.text[:2500].lower()

            success_indicators = [
                "unsubscribed",
                "opted out",
                "successfully removed",
                "preference updated",
                "preferences updated",
                "subscription removed",
                "you have been unsubscribed",
                "you're unsubscribed",
            ]
            confirm_indicators = [
                "confirm unsubscribe",
                "click to confirm",
                "are you sure",
                "please confirm",
                "manage your preferences",
            ]

            note = f"HTTP {status}"
            if any(k in text_sample for k in success_indicators):
                note += " (Unsubscribe confirmed on page)"
            elif any(k in text_sample for k in confirm_indicators):
                note += " (Page may require manual confirmation)"

            is_ok = 200 <= status < 400
            return is_ok, status, note
        except requests.RequestException as e:
            return False, None, f"Request failed: {type(e).__name__} ({e})"
    else:
        # Fallback to standard library urllib
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                status = response.status
                return (200 <= status < 400), status, f"HTTP {status}"
        except urllib.error.HTTPError as e:
            return False, e.code, f"HTTP {e.code}"
        except Exception as e:
            return False, None, f"Request failed: {e}"


def _normalize_link_item(item):
    """
    Extracts (domain, url) from various item formats:
    - (domain, url_or_record) or (domain, url_or_record, ...) tuple
    - dict with 'domain' and/or 'link'
    - object with domain and/or link attributes
    - str (url)
    """
    domain = ""
    link = ""

    if isinstance(item, tuple) and len(item) >= 2:
        domain = str(item[0]) if item[0] is not None else ""
        raw_val = item[1]
        if isinstance(raw_val, dict):
            link = str(raw_val.get("link") or "")
        elif hasattr(raw_val, "link"):
            link = str(getattr(raw_val, "link") or "")
        else:
            link = str(raw_val or "")
    elif isinstance(item, dict):
        domain = str(item.get("domain", ""))
        link = str(item.get("link", ""))
    elif hasattr(item, "link"):
        link = str(getattr(item, "link", ""))
        domain = str(getattr(item, "domain", ""))
    else:
        link = str(item or "")

    link = link.strip()
    domain = domain.strip()

    if not domain and link:
        try:
            parsed = urlparse(link)
            netloc = parsed.netloc or ""
            domain = re.sub(r"^www\.", "", netloc.lower())
        except Exception:
            domain = ""

    if not domain and link:
        domain = link[:30]

    return domain, link


def open_links_in_browser(
    links_to_open,
    progress_bar=True,
    chunk_size=DEFAULT_CHUNK_SIZE,
    tab_delay=0.3,
):
    """
    Opens unsubscribe links in default web browser tabs in chunked batches.
    Uses create_loading_bar / render_progress_bar to display an in-place loading bar
    when progress_bar=True, or detailed line outputs when progress_bar=False.
    """
    if not links_to_open:
        print("No links to open in browser.")
        return

    if isinstance(links_to_open, dict):
        raw_items = list(links_to_open.items())
    else:
        raw_items = list(links_to_open)

    normalized_items = [_normalize_link_item(item) for item in raw_items]
    normalized_items = [(dom, url) for dom, url in normalized_items if url]

    if not normalized_items:
        print("No valid links to open in browser.")
        return

    total_browser_links = len(normalized_items)
    browser_chunk_size = min(chunk_size, 10) if (chunk_size and chunk_size > 0) else 10
    browser_chunks = list(chunk_list(normalized_items, browser_chunk_size))
    total_browser_batches = len(browser_chunks)

    print(f"\nOpening {total_browser_links} unsubscribe link(s) in default web browser...")
    if total_browser_batches > 1:
        print(f"Batching into {total_browser_batches} chunk(s) of up to {browser_chunk_size} tabs each.")

    opened_count = 0
    for b_idx, link_batch in enumerate(browser_chunks, start=1):
        if total_browser_batches > 1:
            print(f"\n--- Browser Batch {b_idx}/{total_browser_batches} ({len(link_batch)} tabs) ---")

        for domain, link in link_batch:
            opened_count += 1
            if progress_bar:
                render_progress_bar(opened_count, total_browser_links, domain)
            else:
                print(f"  [{opened_count}/{total_browser_links}] Opening: {link[:70]}...")
            webbrowser.open_new_tab(link)
            if tab_delay > 0:
                time.sleep(tab_delay)

        if b_idx < total_browser_batches:
            if progress_bar:
                finish_progress_bar()
            cont = input(
                f"\nOpened batch {b_idx}/{total_browser_batches}. Press Enter to open next batch (or 's' to skip remaining): "
            ).strip().lower()
            if cont == "s":
                print("Skipped remaining browser tabs.")
                break

    if progress_bar:
        finish_progress_bar("Finished opening tabs in browser.\n")
    else:
        print("Finished opening tabs in browser.")


def process_unsubscribe_links(
    unsub_map,
    progress_bar=True,
    chunk_size=DEFAULT_CHUNK_SIZE,
    chunk_delay=DEFAULT_CHUNK_DELAY,
    session=None,
    max_workers=None,
    tab_delay=None,
):
    """
    Presents options and handles clicking or visiting unsubscribe links:
      1. Automated HTTP GET request (chunked in synchronous/concurrent batches)
      2. Open in default browser tabs (chunked in manageable batches)
      3. Both (Automated HTTP request, then offer browser opening)
      S. Skip
    """
    if not unsub_map:
        print("No unsubscribe links to click.")
        return

    if tab_delay is None:
        tab_delay = 0.3 if (chunk_delay is None or chunk_delay > 0) else 0.0

    print("\n" + "=" * 55)
    print("--- Step 1: Click Unsubscribe Links ---")
    print(f"Total domains with unsubscribe links: {len(unsub_map)}")
    print("Choose how to process the unsubscribe links:")
    print("  [1] Automated HTTP request (attempt one-click unsubscribe via web request)")
    print("  [2] Open links in default web browser tabs")
    print("  [3] Both (send HTTP request, then open in browser)")
    print("  [S] Skip clicking links")

    choice = input("Enter choice [1/2/3/S] (default: 1): ").strip().upper() or "1"
    if choice == "S":
        print("Skipped clicking unsubscribe links.")
        return

    to_open_in_browser = []
    unsub_items = list(unsub_map.items())
    chunks = list(chunk_list(unsub_items, chunk_size))

    # Automated HTTP Request (Option 1 or 3)
    if choice in ("1", "3"):
        total = len(unsub_items)
        num_chunks = len(chunks)
        print(
            f"\nVisiting {total} unsubscribe link(s) via automated HTTP requests "
            f"in {num_chunks} chunk(s) (chunk size: {chunk_size})..."
        )

        success_count = 0
        manual_or_failed = []
        completed_count = 0

        workers_limit = (
            max_workers
            if max_workers is not None
            else int(os.environ.get("CHUNK_WORKERS", "10"))
        )

        for chunk_idx, chunk in enumerate(chunks, start=1):
            chunk_workers = min(len(chunk), max(1, workers_limit))
            with concurrent.futures.ThreadPoolExecutor(max_workers=chunk_workers) as executor:
                future_to_item = {}
                for domain, raw_val in chunk:
                    link_url = raw_val.get("link") if isinstance(raw_val, dict) else str(raw_val)
                    fut = executor.submit(click_single_link, link_url, session)
                    future_to_item[fut] = (domain, link_url)

                for future in concurrent.futures.as_completed(future_to_item):
                    domain, link = future_to_item[future]
                    completed_count += 1
                    try:
                        ok, status, note = future.result()
                    except Exception as e:
                        ok, status, note = False, None, f"Request failed: {type(e).__name__} ({e})"

                    if ok:
                        success_count += 1
                        if "manual confirmation" in note.lower():
                            manual_or_failed.append((domain, link, note))
                    else:
                        manual_or_failed.append((domain, link, note))

                    if progress_bar:
                        render_progress_bar(completed_count, total, domain)
                    else:
                        truncated_link = link[:65] + ("..." if len(link) > 65 else "")
                        print(f"[{completed_count}/{total}] {domain} -> {truncated_link}")
                        if ok:
                            print(f"    ✓ {note}")
                        else:
                            print(f"    ✗ {note}")

            if chunk_idx < num_chunks and chunk_delay > 0:
                time.sleep(chunk_delay)

        if progress_bar:
            finish_progress_bar(f"Automated requests complete: {success_count}/{total} succeeded.\n")
        else:
            print(f"\nAutomated request results: {success_count}/{total} succeeded.")

        if manual_or_failed and choice == "1":
            print(f"\n{len(manual_or_failed)} link(s) failed or may require manual confirmation:")
            for d, l, n in manual_or_failed:
                print(f"  • {d}: {n}")
            open_failed = input("\nOpen these links in your browser to confirm manually? [y/N]: ").strip().lower()
            if open_failed == "y":
                to_open_in_browser = [(d, l) for d, l, _ in manual_or_failed]

    # Open in Browser (Option 2 or 3)
    if choice in ("2", "3"):
        to_open_in_browser = [
            (
                domain,
                item.get("link")
                if isinstance(item, dict)
                else (getattr(item, "link", None) or str(item)),
            )
            for domain, item in unsub_map.items()
        ]

    if to_open_in_browser:
        open_links_in_browser(
            to_open_in_browser,
            progress_bar=progress_bar,
            chunk_size=chunk_size,
            tab_delay=tab_delay,
        )



def find_emails_for_domain_with_unsubscribe(
    mail,
    domain,
    chunk_size=DEFAULT_CHUNK_SIZE,
    chunk_delay=DEFAULT_CHUNK_DELAY,
    username=None,
    password=None,
    mailbox="INBOX",
):
    """
    Searches INBOX for all emails from `domain` that contain an unsubscribe button/link,
    processing messages in synchronous chunks for maximum speed while adhering to Google
    Pro / Workspace IMAP rate limits.

    Returns list of dicts: [{'uid': uid, 'subject': subject, 'date': date, 'link': link}, ...]
    """
    # Search for messages from domain in mailbox
    status, messages = mail.uid("SEARCH", None, "FROM", f'"{domain}"')
    if status != "OK" or not messages or not messages[0]:
        return []

    uids = messages[0].split()
    matched = []
    header_fields_query = "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE LIST-UNSUBSCRIBE)])"
    body_query = "(BODY.PEEK[])"

    mail_holder = [mail]

    def reconnect_if_needed():
        if username and password:
            mail_holder[0] = ensure_imap_connection(mail_holder[0], username, password, mailbox)

    for uid_chunk in chunk_list(uids, chunk_size):
        id_set = (
            b",".join(uid_chunk)
            if isinstance(uid_chunk[0], bytes)
            else ",".join(str(x) for x in uid_chunk).encode("ascii")
        )

        def do_fetch_headers():
            return mail_holder[0].uid("FETCH", id_set, header_fields_query)

        status, data = execute_imap_chunk_with_retry(
            do_fetch_headers, max_retries=3, reconnect_fn=reconnect_if_needed
        )

        parsed_headers = parse_fetch_batch_data(data, use_uid=True)

        # Fallback if server/mock returned full body instead of headers
        if status == "OK" and not parsed_headers:
            def do_fetch_fallback():
                return mail_holder[0].uid("FETCH", id_set, body_query)

            status, data = execute_imap_chunk_with_retry(
                do_fetch_fallback, max_retries=3, reconnect_fn=reconnect_if_needed
            )
            parsed_headers = parse_fetch_batch_data(data, use_uid=True)

        body_needed_uids = []
        for uid, headers_raw in parsed_headers:
            msg = email.message_from_bytes(headers_raw)

            # Verify sender base domain matches
            from_hdr = msg.get("From", "")
            msg_domain = extract_domain(from_hdr)
            if msg_domain != domain:
                continue

            unsub_link = extract_unsubscribe_link(msg)
            if unsub_link:
                subj = decode_email_subject(msg.get("Subject", "(No Subject)"))
                date_hdr = msg.get("Date", "")
                matched.append({
                    "uid": uid,
                    "subject": subj,
                    "date": date_hdr,
                    "link": unsub_link,
                })
            else:
                body_needed_uids.append(uid)

        # Selective body fetch for emails from this domain that lacked List-Unsubscribe header
        if body_needed_uids:
            body_id_set = (
                b",".join(body_needed_uids)
                if isinstance(body_needed_uids[0], bytes)
                else ",".join(str(x) for x in body_needed_uids).encode("ascii")
            )

            def do_fetch_bodies():
                return mail_holder[0].uid("FETCH", body_id_set, body_query)

            b_status, b_data = execute_imap_chunk_with_retry(
                do_fetch_bodies, max_retries=3, reconnect_fn=reconnect_if_needed
            )
            parsed_bodies = parse_fetch_batch_data(b_data, use_uid=True)
            for uid, body_raw in parsed_bodies:
                msg = email.message_from_bytes(body_raw)
                from_hdr = msg.get("From", "")
                msg_domain = extract_domain(from_hdr)
                if msg_domain == domain:
                    unsub_link = extract_unsubscribe_link(msg)
                    if unsub_link:
                        subj = decode_email_subject(msg.get("Subject", "(No Subject)"))
                        date_hdr = msg.get("Date", "")
                        matched.append({
                            "uid": uid,
                            "subject": subj,
                            "date": date_hdr,
                            "link": unsub_link,
                        })

        if chunk_delay > 0:
            time.sleep(chunk_delay)

    return matched


def delete_emails_for_domains(
    mail,
    target_domains,
    username=None,
    password=None,
    mailbox="INBOX",
    progress_bar=True,
    chunk_size=DEFAULT_CHUNK_SIZE,
    chunk_delay=DEFAULT_CHUNK_DELAY,
    domain_emails=None,
):
    """
    Finds all emails from `target_domains` containing an unsubscribe button/link,
    presents a preview/summary, requests user confirmation, and moves them to Trash.
    When `domain_emails` is provided (pre-scanned from initial scan), avoids re-scanning.
    """
    if not target_domains:
        print("No domains selected for email deletion.")
        return

    print("\n" + "=" * 55)
    print("--- Step 2: Delete Emails Containing Unsubscribe Button ---")
    print(f"Candidate domain(s) for cleanup: {len(target_domains)}")

    domain_matches = {}
    total_found = 0

    if domain_emails is not None:
        print("Using stored list of emails containing unsubscribe buttons (no re-scanning needed).")
        for domain in target_domains:
            entry = domain_emails.get(domain, [])
            if isinstance(entry, dict) and "emails" in entry:
                matched = entry["emails"]
            elif isinstance(entry, list):
                matched = entry
            else:
                matched = []
            if matched:
                domain_matches[domain] = matched
                total_found += len(matched)
        print(f"Loaded {total_found} pre-scanned email(s) across {len(domain_matches)} domain(s).\n")
    else:
        confirm_scan = input(
            f"Scan inbox to find all mail from these {len(target_domains)} domain(s) containing unsubscribe buttons? [Y/n]: "
        ).strip().lower()
        if confirm_scan == "n":
            print("Skipped email deletion.")
            return

        print("\nScanning mailbox for matching emails...")
        total_domains = len(target_domains)

        for idx, domain in enumerate(target_domains, start=1):
            if progress_bar:
                render_progress_bar(idx, total_domains, domain)
            else:
                print(f"  [{idx}/{total_domains}] Searching for emails from '{domain}'...", end="", flush=True)

            mail = ensure_imap_connection(mail, username, password, mailbox)
            matched = find_emails_for_domain_with_unsubscribe(
                mail,
                domain,
                chunk_size=chunk_size,
                chunk_delay=chunk_delay,
                username=username,
                password=password,
                mailbox=mailbox,
            )

            if not progress_bar:
                print(f" found {len(matched)} email(s) with unsubscribe button.")

            if matched:
                domain_matches[domain] = matched
                total_found += len(matched)

        if progress_bar:
            finish_progress_bar(f"Scan complete: Found {total_found} email(s) across {len(domain_matches)} domain(s).\n")

    if total_found == 0:
        print("\nNo emails containing unsubscribe buttons were found for the selected domains.")
        return

    mail = ensure_imap_connection(mail, username, password, mailbox)
    trash_folder = get_trash_folder(mail)
    print(f"Detected Gmail Trash destination: '{trash_folder}'")

    print("\n" + "-" * 50)
    print("---------------- DELETION PREVIEW ----------------")
    print(f"{'Domain':<30} {'Emails with Unsubscribe':>20}")
    print("-" * 50)
    for domain, msgs in domain_matches.items():
        print(f"{domain:<30} {len(msgs):>20}")
    print("-" * 50)
    print(f"{'TOTAL EMAILS TO DELETE:':<30} {total_found:>20}")
    print("-" * 50)

    # Confirmation
    confirm_del = input(
        f"\nAre you sure you want to move all {total_found} email(s) to Trash? [y/N]: "
    ).strip().lower()

    if confirm_del != "y":
        print("Deletion aborted. No emails were touched.")
        return

    print(f"\nMoving {total_found} email(s) to Trash ({trash_folder})...")
    all_uids = [item["uid"] for msgs in domain_matches.values() for item in msgs]

    mail = ensure_imap_connection(mail, username, password, mailbox)

    # Process in batches of chunk_size
    deleted_count = 0
    for batch in chunk_list(all_uids, chunk_size):
        uid_set = b",".join(batch) if isinstance(batch[0], bytes) else ",".join(str(x) for x in batch).encode("ascii")
        try:
            # 1. Copy to Gmail Trash folder
            mail.uid("COPY", uid_set, trash_folder)
            # 2. Mark as \Deleted in current folder
            mail.uid("STORE", uid_set, "+FLAGS", "(\\Deleted)")
            deleted_count += len(batch)
        except Exception:
            # Fallback to individual commands if batch copy fails
            for single_uid in batch:
                s_uid = single_uid if isinstance(single_uid, bytes) else str(single_uid).encode("ascii")
                try:
                    mail.uid("COPY", s_uid, trash_folder)
                    mail.uid("STORE", s_uid, "+FLAGS", "(\\Deleted)")
                    deleted_count += 1
                except Exception:
                    try:
                        mail.uid("STORE", s_uid, "+FLAGS", "(\\Deleted)")
                        deleted_count += 1
                    except Exception as e:
                        print(f"Error deleting message {single_uid}: {e}")

    # Expunge to finalize removal from INBOX
    try:
        mail.expunge()
    except Exception as e:
        print(f"Expunge notice: {e}")

    print(f"✓ Successfully deleted {deleted_count} email(s) (moved to '{trash_folder}').")


def build_unsubscribe_hash(
    mail,
    progress_bar=True,
    chunk_size=DEFAULT_CHUNK_SIZE,
    chunk_delay=DEFAULT_CHUNK_DELAY,
    username=None,
    password=None,
    mailbox="INBOX",
):
    """
    Scans the mailbox for candidate messages containing 'unsubscribe',
    extracts the newest unsubscribe link for each sender domain by comparing
    email dates, and returns an UnsubscribeMap storing:
      - 'link': the newest unsubscribe link URL
      - 'date': the date of the email the link was obtained from
      - 'emails': list of all emails from that domain with unsubscribe buttons

    Also provides:
      - .emails: all emails containing unsubscribe buttons across domains
      - .domain_emails: dictionary mapping domain -> list of email records
    """
    use_uid = True
    try:
        status, messages = mail.uid("SEARCH", None, '(BODY "unsubscribe")')
        if status != "OK" or not messages or not messages[0]:
            status, messages = mail.search(None, '(BODY "unsubscribe")')
            use_uid = False
    except Exception:
        status, messages = mail.search(None, '(BODY "unsubscribe")')
        use_uid = False

    if status != "OK" or not messages or not messages[0]:
        print("No candidate messages found in inbox.")
        return UnsubscribeMap()

    msg_ids = messages[0].split()
    msg_ids.reverse()  # Newest to oldest

    domain_unsub_map = UnsubscribeMap()
    total = len(msg_ids)
    print(
        f"Scanning {total} candidate messages from newest to oldest (chunk size: {chunk_size})...\n"
    )

    mail_holder = [mail]

    def reconnect_if_needed():
        if username and password:
            mail_holder[0] = ensure_imap_connection(mail_holder[0], username, password, mailbox)

    processed_count = [0]
    header_fields_query = "(BODY.PEEK[HEADER.FIELDS (FROM LIST-UNSUBSCRIBE SUBJECT DATE)])"
    body_query = "(BODY.PEEK[])"

    for chunk in chunk_list(msg_ids, chunk_size):
        def on_msg(dom):
            processed_count[0] += 1
            if progress_bar:
                render_progress_bar(processed_count[0], total, dom)

        process_chunk(
            chunk=chunk,
            mail_holder=mail_holder,
            domain_unsub_map=domain_unsub_map,
            use_uid=use_uid,
            header_fields_query=header_fields_query,
            body_query=body_query,
            reconnect_fn=reconnect_if_needed,
            on_message_callback=on_msg,
            verbose=not progress_bar,
            processed_counter=processed_count,
            total_count=total,
        )

        # Respect Google rate limits with a small delay between chunks
        if chunk_delay > 0:
            time.sleep(chunk_delay)

    if progress_bar:
        finish_progress_bar(
            f"Scan complete: Found {len(domain_unsub_map)} sender domain(s) with unsubscribe links.\n"
        )

    return domain_unsub_map


def main():
    print("=" * 55)
    print("   Gmail Unsubscribe Harvester, Clicker & Cleaner   ")
    print("=" * 55)

    env_user = os.environ.get("GMAIL_USER", "").strip()
    env_pass = os.environ.get("GMAIL_APP_PASSWORD", "").strip()

    # Check if real credentials are provided via .env / environment
    has_valid_env = (
        bool(env_user and env_pass)
        and "your_email" not in env_user.lower()
        and "xxxx" not in env_pass.lower()
    )

    if has_valid_env:
        username = env_user
        raw_password = env_pass
        print(f"Loaded credentials from .env for: {username}")
    else:
        if env_user or env_pass:
            print("Notice: .env contains default placeholder credentials. Please update .env or enter below.\n")
        username = input("Enter your Gmail address: ").strip()
        raw_password = getpass.getpass("Enter your Google App Password (16 chars): ").strip()

    password = raw_password.replace(" ", "")

    if not username or not password:
        print("Error: Gmail address and App Password are required.")
        return

    # Determine output mode: clean single-line loading bar vs verbose
    if "--verbose" in sys.argv or "-v" in sys.argv:
        use_progress_bar = False
    elif "--progress" in sys.argv or "--quiet" in sys.argv or "-q" in sys.argv:
        use_progress_bar = True
    elif os.environ.get("PROGRESS_BAR_MODE") is not None:
        use_progress_bar = os.environ.get("PROGRESS_BAR_MODE", "").strip().lower() in ("true", "1", "yes", "on")
    else:
        print("\nTerminal Output Mode:")
        print("  [1] Clean Loading Bar: [=======-------------] <domain> (Recommended)")
        print("  [2] Verbose Output: Print every email and link found")
        mode_choice = input("Select mode [1/2] (default: 1): ").strip()
        use_progress_bar = (mode_choice != "2")

    if use_progress_bar:
        print("Output mode: Clean single-line loading bar enabled.\n")
    else:
        print("Output mode: Verbose logging enabled.\n")

    chunk_size = int(os.environ.get("EMAIL_CHUNK_SIZE", str(DEFAULT_CHUNK_SIZE)))
    chunk_delay = float(os.environ.get("CHUNK_DELAY", str(DEFAULT_CHUNK_DELAY)))

    print("Connecting to Gmail IMAP...")
    try:
        mail = connect_imap(username, password)
    except imaplib.IMAP4.error as e:
        print(f"\nAuthentication Failed: {e}")
        print("Tip: Make sure you are using a 16-character Google App Password (not your normal password).")
        print("Generate one here: https://myaccount.google.com/apppasswords (requires 2-Step Verification enabled).")
        return
    except Exception as e:
        print(f"Failed to connect to Gmail: {e}")
        return

    try:
        unsub_map = build_unsubscribe_hash(
            mail,
            progress_bar=use_progress_bar,
            chunk_size=chunk_size,
            chunk_delay=chunk_delay,
            username=username,
            password=password,
        )

        print("\n--- Initial Extracted Domains ---")
        if not unsub_map:
            print("No domains with unsubscribe links were found.")
            return

        for d, entry in unsub_map.items():
            link = entry.get("link") if isinstance(entry, dict) else str(entry)
            date_val = entry.get("date") if isinstance(entry, dict) else ""
            num_msgs = len(entry.get("emails", [])) if isinstance(entry, dict) else 0
            date_info = f" (Date: {date_val})" if date_val else ""
            msgs_info = f" [{num_msgs} email(s) found]" if num_msgs > 0 else ""
            print(f"'{d}': '{link}'{date_info}{msgs_info}")

        # Interactive Removal / Filter loop
        print("\n--- Domain Filter / Removal Loop ---")
        print("Remove any domains you want to keep (e.g. your bank, GitHub, etc.).")
        while True:
            target = input(
                "\nEnter domain to remove (or type 'N' to finish filtering): "
            ).strip()
            if target.upper() == "N":
                break

            if target in unsub_map:
                del unsub_map[target]
                print(f"Removed '{target}'. Current count: {len(unsub_map)}")
            else:
                matched_key = next(
                    (k for k in unsub_map if k.lower() == target.lower()), None
                )
                if matched_key:
                    del unsub_map[matched_key]
                    print(f"Removed '{matched_key}'. Current count: {len(unsub_map)}")
                else:
                    print(f"Domain '{target}' not found in hash.")

        print("\n--- Final Filtered Domains ---")
        for d, entry in unsub_map.items():
            link = entry.get("link") if isinstance(entry, dict) else str(entry)
            date_val = entry.get("date") if isinstance(entry, dict) else ""
            num_msgs = len(entry.get("emails", [])) if isinstance(entry, dict) else 0
            date_info = f" (Date: {date_val})" if date_val else ""
            msgs_info = f" [{num_msgs} email(s) found]" if num_msgs > 0 else ""
            print(f"'{d}': '{link}'{date_info}{msgs_info}")

        if not unsub_map:
            print("All domains were filtered out. Nothing left to process.")
            return

        # Feature 1: Click all unsubscribe links
        process_unsubscribe_links(
            unsub_map,
            progress_bar=use_progress_bar,
            chunk_size=chunk_size,
            chunk_delay=chunk_delay,
        )

        # Feature 2: Delete all mail from those domains containing an unsubscribe button
        target_domains = list(unsub_map.keys())
        delete_emails_for_domains(
            mail,
            target_domains,
            username=username,
            password=password,
            progress_bar=use_progress_bar,
            chunk_size=chunk_size,
            chunk_delay=chunk_delay,
            domain_emails=unsub_map.domain_emails,
        )


    finally:
        try:
            mail.close()
            mail.logout()
        except Exception:
            pass

    print("\n✓ Process completed successfully. Have a clean inbox!")


if __name__ == "__main__":
    main()