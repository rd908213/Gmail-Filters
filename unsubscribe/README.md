# Gmail Unsubscribe Harvester & Mailbox Cleaner

An automated Python tool to scan your Gmail inbox, harvest unsubscribe links, trigger them (via background HTTP requests or default browser tabs), and clean up all emails from those domains containing an unsubscribe button.

---

## Features

- **Credential Security via `.env`**: Store your email and 16-character Google App Password in a local `.env` file so you never need to type credentials into the terminal.
- **Smart Unsubscribe Discovery**:
  - Checks RFC 2369 `List-Unsubscribe` headers.
  - Inspects HTML bodies for unsubscribe buttons, links, images with alt text, and preference management URLs.
  - Supports plain-text fallback detection.
- **Domain Filter / Retain Loop**:
  - Interactive prompt allowing you to exclude domains you want to keep (e.g. your bank, GitHub, utilities) before taking action.
- **Unsubscribe Link Triggering**:
  - **Automated HTTP GET Requests**: Simulates browser visits with custom User-Agent, handles redirects, and detects confirmation messages on the destination page.
  - **Browser Tabs**: Opens links directly in your default browser for services requiring manual confirmation forms.
  - **Hybrid**: Runs automated requests first, then prompts to open any failed/unconfirmed links in your browser.
- **Safe Mailbox Cleanup**:
  - Scans for all emails from the confirmed domains that contain an unsubscribe button.
  - Preserves transactional emails (such as receipts, bills, and order updates) that lack unsubscribe buttons.
  - Provides a **preview table** with email counts per domain before deletion.
  - Moves messages safely to Gmail's `Trash` folder (retaining 30-day recovery) and expunges them from the inbox.
  - Automatically reconnects if your IMAP session expires while reviewing links.

---

## Prerequisites

1. **Python 3.10+**
2. **2-Step Verification (2FA)** enabled on your Google Account.
3. A **Google App Password** (required by Google for IMAP access).

---

## How to Get a Google App Password

Google does not permit standard account passwords for third-party IMAP access. You must create a dedicated 16-character App Password:

1. Go to your [Google Account Management](https://myaccount.google.com/).
2. Select **Security** from the left navigation.
3. Under **How you sign in to Google**, verify that **2-Step Verification** is turned **ON** *(Google will not display the App Passwords option without 2FA enabled)*.
4. Go directly to [Google App Passwords](https://myaccount.google.com/apppasswords) (or search *"App passwords"* in the top search bar).
5. In the **App name** field, type a name (e.g. `Gmail Harvester` or `Python Filter`).
6. Click **Create**.
7. Copy the 16-character code (formatted like `abcd efgh ijkl mnop`).
   > **Note**: Any spaces in the code are automatically stripped by the script.

---

## Setup & Configuration

### 1. Install Dependencies

Navigate to the `unsubscribe` folder, set up your virtual environment, and install dependencies:

```bash
cd unsubscribe

# Create virtual environment (if not already created)
python3 -m venv .venv

# Activate the virtual environment
source .venv/bin/activate

# Install required dependencies
pip install -r requirements.txt
```

### 2. Configure Your `.env` File (Required)

To avoid typing passwords into the terminal, configure your `.env` file in the `unsubscribe/` folder:

1. Open `.env` (a template is provided in [.env.example](file:///Users/rd908/projects/Gmail-Filters/unsubscribe/.env.example)).
2. Fill in your Gmail address and 16-character Google App Password:

```env
GMAIL_USER=your_email@gmail.com
GMAIL_APP_PASSWORD=abcd efgh ijkl mnop
```

> **Security Note**: `.env` is listed in [.gitignore](file:///Users/rd908/projects/Gmail-Filters/.gitignore) so your credentials will **never** be committed to version control.

---

## Usage

Once your `.env` file is configured, simply run:

```bash
python unsubscribe_harvest.py
```

The script will automatically detect and load your credentials from `.env`:
```text
Loaded credentials from .env for: your_email@gmail.com
Connecting to Gmail IMAP...
```

*(If `.env` is missing or contains default placeholders, the script will fall back to prompting for credentials interactively).*

---

## Step-by-Step Workflow

1. **Inbox Scanning**:
   The script searches your `INBOX` for emails containing unsubscribe links or headers from newest to oldest.

2. **Domain Exclusion Loop**:
   A summary of discovered sender domains is printed. You can enter any domain name (e.g. `github.com`) to remove it from the cancellation list, or type `N` when finished.

3. **Step 1: Click Unsubscribe Links**:
   Select how you want to handle the unsubscribe URLs:
   - `[1] Automated HTTP request`: Sends background HTTP requests with real browser headers and reports success or manual confirmation flags.
   - `[2] Open in browser tabs`: Spawns tabs in your default web browser.
   - `[3] Both`: Sends automated HTTP requests, then offers to open links in the browser.
   - `[S] Skip`: Bypasses clicking without modifying subscriptions.

4. **Step 2: Delete Domain Emails**:
   - The script queries your mailbox for emails from each unsubscribed domain.
   - It verifies that each email contains an unsubscribe button/link before flagging it.
   - A **Deletion Preview** table displays the exact email count per domain.
   - Confirms `[y/N]` before moving the messages to Gmail's `Trash` and expunging them from your inbox.

---

## Safety & File Reference

- [.env](file:///Users/rd908/projects/Gmail-Filters/unsubscribe/.env): Local environment file holding `GMAIL_USER` and `GMAIL_APP_PASSWORD` (ignored by git).
- [.env.example](file:///Users/rd908/projects/Gmail-Filters/unsubscribe/.env.example): Template for `.env`.
- [unsubscribe_harvest.py](file:///Users/rd908/projects/Gmail-Filters/unsubscribe/unsubscribe_harvest.py): Main script containing IMAP harvesting, link processing, and email deletion logic.
- [requirements.txt](file:///Users/rd908/projects/Gmail-Filters/unsubscribe/requirements.txt): Python dependencies (`beautifulsoup4`, `requests`, `python-dotenv`).
