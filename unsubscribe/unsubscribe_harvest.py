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

import email
from email.header import decode_header
import getpass
import imaplib
import os
import re
import sys
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
            if tld2 in COMMON_TWO_PART_TLDS and len(parts) > 3:
                return ".".join(parts[-3:])
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


def connect_imap(username, password, mailbox="INBOX"):
    """Establishes an authenticated IMAP SSL connection to Gmail."""
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(username, password)
    mail.select(mailbox)
    return mail


def ensure_imap_connection(mail, username, password, mailbox="INBOX"):
    """Ensures IMAP connection is alive; reconnects if connection dropped."""
    try:
        mail.noop()
        return mail
    except Exception:
        print("\nIMAP connection timed out or closed. Reconnecting...")
        return connect_imap(username, password, mailbox)


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


def process_unsubscribe_links(unsub_map):
    """
    Presents options and handles clicking or visiting unsubscribe links:
      1. Automated HTTP GET request
      2. Open in default browser tabs
      3. Both (Automated HTTP request, then offer browser opening)
      S. Skip
    """
    if not unsub_map:
        print("No unsubscribe links to click.")
        return

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

    # Automated HTTP Request (Option 1 or 3)
    if choice in ("1", "3"):
        print(f"\nVisiting {len(unsub_map)} unsubscribe link(s) via automated HTTP requests...")
        session = requests.Session() if requests is not None else None

        success_count = 0
        manual_or_failed = []

        for idx, (domain, link) in enumerate(unsub_map.items(), start=1):
            truncated_link = link[:65] + ("..." if len(link) > 65 else "")
            print(f"[{idx}/{len(unsub_map)}] {domain} -> {truncated_link}")
            ok, status, note = click_single_link(link, session=session)
            if ok:
                success_count += 1
                print(f"    ✓ {note}")
                if "manual confirmation" in note.lower():
                    manual_or_failed.append((domain, link, note))
            else:
                print(f"    ✗ {note}")
                manual_or_failed.append((domain, link, note))
            time.sleep(0.3)  # Polite pacing

        print(f"\nAutomated request results: {success_count}/{len(unsub_map)} succeeded.")

        if manual_or_failed and choice == "1":
            print(f"\n{len(manual_or_failed)} link(s) failed or may require manual confirmation:")
            for d, l, n in manual_or_failed:
                print(f"  • {d}: {n}")
            open_failed = input("\nOpen these links in your browser to confirm manually? [y/N]: ").strip().lower()
            if open_failed == "y":
                to_open_in_browser = [l for _, l, _ in manual_or_failed]

    # Open in Browser (Option 2 or 3)
    if choice in ("2", "3"):
        to_open_in_browser = list(unsub_map.values())

    if to_open_in_browser:
        print(f"\nOpening {len(to_open_in_browser)} unsubscribe link(s) in your default web browser...")
        for idx, link in enumerate(to_open_in_browser, start=1):
            print(f"  [{idx}/{len(to_open_in_browser)}] Opening: {link[:70]}...")
            webbrowser.open_new_tab(link)
            time.sleep(0.4)
        print("Finished opening tabs in browser.")


def find_emails_for_domain_with_unsubscribe(mail, domain):
    """
    Searches INBOX for all emails from `domain` that contain an unsubscribe button/link.
    Returns list of dicts: [{'uid': uid, 'subject': subject, 'date': date, 'link': link}, ...]
    """
    # Search for messages from domain in mailbox
    status, messages = mail.uid("SEARCH", None, "FROM", f'"{domain}"')
    if status != "OK" or not messages or not messages[0]:
        return []

    uids = messages[0].split()
    matched = []

    for uid in uids:
        try:
            status, data = mail.uid("FETCH", uid, "(RFC822)")
            if status != "OK" or not data or not data[0]:
                continue
            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            # Verify sender base domain matches
            from_hdr = msg.get("From", "")
            msg_domain = extract_domain(from_hdr)
            if msg_domain != domain:
                continue

            # Verify message contains an unsubscribe button/link
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
        except Exception:
            continue

    return matched


def delete_emails_for_domains(mail, target_domains, username=None, password=None, mailbox="INBOX"):
    """
    Finds all emails from `target_domains` containing an unsubscribe button/link,
    presents a preview/summary, requests user confirmation, and moves them to Trash.
    """
    if not target_domains:
        print("No domains selected for email deletion.")
        return

    print("\n" + "=" * 55)
    print("--- Step 2: Delete Emails Containing Unsubscribe Button ---")
    print(f"Candidate domain(s) for cleanup: {len(target_domains)}")

    confirm_scan = input(
        f"Scan inbox to find all mail from these {len(target_domains)} domain(s) containing unsubscribe buttons? [Y/n]: "
    ).strip().lower()
    if confirm_scan == "n":
        print("Skipped email deletion.")
        return

    mail = ensure_imap_connection(mail, username, password, mailbox)
    trash_folder = get_trash_folder(mail)
    print(f"Detected Gmail Trash destination: '{trash_folder}'")

    print("\nScanning mailbox for matching emails...")
    domain_matches = {}
    total_found = 0

    for idx, domain in enumerate(target_domains, start=1):
        print(f"  [{idx}/{len(target_domains)}] Searching for emails from '{domain}'...", end="", flush=True)
        mail = ensure_imap_connection(mail, username, password, mailbox)
        matched = find_emails_for_domain_with_unsubscribe(mail, domain)
        print(f" found {len(matched)} email(s) with unsubscribe button.")
        if matched:
            domain_matches[domain] = matched
            total_found += len(matched)

    if total_found == 0:
        print("\nNo emails containing unsubscribe buttons were found for the selected domains.")
        return

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

    # Process in batches of 50
    batch_size = 50
    deleted_count = 0
    for i in range(0, len(all_uids), batch_size):
        batch = all_uids[i:i + batch_size]
        uid_set = b",".join(batch)
        try:
            # 1. Copy to Gmail Trash folder
            mail.uid("COPY", uid_set, trash_folder)
            # 2. Mark as \Deleted in current folder
            mail.uid("STORE", uid_set, "+FLAGS", "(\\Deleted)")
            deleted_count += len(batch)
        except Exception:
            # Fallback to individual commands if batch copy fails
            for single_uid in batch:
                try:
                    mail.uid("COPY", single_uid, trash_folder)
                    mail.uid("STORE", single_uid, "+FLAGS", "(\\Deleted)")
                    deleted_count += 1
                except Exception:
                    try:
                        mail.uid("STORE", single_uid, "+FLAGS", "(\\Deleted)")
                        deleted_count += 1
                    except Exception as e:
                        print(f"Error deleting message {single_uid}: {e}")

    # Expunge to finalize removal from INBOX
    try:
        mail.expunge()
    except Exception as e:
        print(f"Expunge notice: {e}")

    print(f"✓ Successfully deleted {deleted_count} email(s) (moved to '{trash_folder}').")


def build_unsubscribe_hash(mail):
    """
    Scans the mailbox for candidate messages containing 'unsubscribe',
    extracts the most recent unsubscribe link for each sender domain,
    and returns a mapping of domain -> unsubscribe_link.
    """
    status, messages = mail.search(None, '(BODY "unsubscribe")')
    if status != "OK" or not messages or not messages[0]:
        print("No candidate messages found in inbox.")
        return {}

    msg_ids = messages[0].split()
    msg_ids.reverse()  # Newest to oldest

    domain_unsub_map = {}
    print(f"Scanning {len(msg_ids)} candidate messages from newest to oldest...\n")

    for idx, msg_id in enumerate(msg_ids, start=1):
        status, data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK" or not data or not data[0]:
            continue

        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        from_header = msg.get("From", "")
        domain = extract_domain(from_header)
        if not domain:
            continue

        # If domain is already mapped, skip (we already have the newest link)
        if domain in domain_unsub_map:
            continue

        unsub_link = extract_unsubscribe_link(msg)
        if unsub_link:
            domain_unsub_map[domain] = unsub_link
            print(f"[FOUND] {domain} -> {unsub_link}")

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

    print("\nConnecting to Gmail IMAP...")
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
        unsub_map = build_unsubscribe_hash(mail)

        print("\n--- Initial Extracted Domains ---")
        if not unsub_map:
            print("No domains with unsubscribe links were found.")
            return

        for d, link in unsub_map.items():
            print(f"'{d}': '{link}'")

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
        for d, link in unsub_map.items():
            print(f"'{d}': '{link}'")

        if not unsub_map:
            print("All domains were filtered out. Nothing left to process.")
            return

        # Feature 1: Click all unsubscribe links
        process_unsubscribe_links(unsub_map)

        # Feature 2: Delete all mail from those domains containing an unsubscribe button
        target_domains = list(unsub_map.keys())
        delete_emails_for_domains(
            mail, target_domains, username=username, password=password
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