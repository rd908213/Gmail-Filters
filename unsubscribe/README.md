# Gmail Unsubscribe Harvester & Mailbox Cleaner

An automated Python tool to scan your Gmail inbox, harvest unsubscribe links, trigger them (via background HTTP requests or default browser tabs), and clean up all emails from those domains containing an unsubscribe button.

---

## Features

- **Clean Loading Bar Mode**: Suppresses multi-line terminal spam and renders an in-place updating progress bar (`[=======-------------] <domain>`) so your terminal stays completely uncluttered.
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

## Antigravity IDE Python Interpreter

The repository includes [.vscode/settings.json](file:///Users/rd908/projects/Gmail-Filters/.vscode/settings.json) and [unsubscribe/.vscode/settings.json](file:///Users/rd908/projects/Gmail-Filters/unsubscribe/.vscode/settings.json) pre-configured with:

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/unsubscribe/.venv/bin/python",
  "python.terminal.activateEnvironment": true
}
```

Antigravity IDE will automatically select the virtual environment interpreter (`.venv`). You can also manually switch it at any time by pressing <kbd>⌘</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd> (macOS) / <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd> $\to$ **Python: Select Interpreter** $\to$ choose `./unsubscribe/.venv/bin/python`.

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
2. Fill in your Gmail address, Google App Password, and preferred progress display mode:

```env
GMAIL_USER=your_email@gmail.com
GMAIL_APP_PASSWORD=abcd efgh ijkl mnop

# Set to true to suppress terminal clutter and use an in-place loading bar
PROGRESS_BAR_MODE=true

# Batch / Chunk Processing Configuration (Optimized for Google Pro / Workspace IMAP rate limits):
# Number of emails to process synchronously per batch (default: 50, recommended: 25-100)
EMAIL_CHUNK_SIZE=50

# Polite delay in seconds between chunked IMAP calls to prevent Google burst throttling (default: 0.05)
CHUNK_DELAY=0.05
```

> **Security Note**: `.env` is listed in [.gitignore](file:///Users/rd908/projects/Gmail-Filters/.gitignore) so your credentials will **never** be committed to version control.

---

## High-Performance Synchronous Chunking & Google Pro Rate Limits

Scanning thousands of emails one-by-one over IMAP is network latency bound and risks tripping Google's account rate limits. This tool incorporates **synchronous chunking** engineered specifically around Google Workspace / Google Pro IMAP quotas:

1. **Synchronous IMAP Batching**:
   - Instead of 1 network roundtrip per email, queries are batched in synchronous chunks (default: 50 emails per `FETCH`), reducing network latency by over **90%** and increasing scan speeds drastically.
2. **Google IMAP Daily Bandwidth Quota Protection**:
   - Google enforces a strict **2,500 MB (2.5 GB) per day** IMAP download bandwidth cap. Downloading full `RFC822` email bodies (with embedded images, attachments, and bloated markup) for thousands of messages will quickly exhaust this cap and cause a 24-hour IMAP lockout.
   - The tool performs **header-first discovery** using `BODY.PEEK[HEADER.FIELDS (FROM LIST-UNSUBSCRIBE SUBJECT DATE)]`. A chunk of 50 headers is only ~25 KB (a **99% reduction in bandwidth consumption**).
   - Full message bodies (`BODY.PEEK[]`) are downloaded selectively **only** for newly discovered domains lacking an RFC 2369 `List-Unsubscribe` header.
3. **Read/Unread Flag Preservation**:
   - Using `BODY.PEEK` instead of `RFC822` ensures emails are **not marked as read** in your Gmail inbox during the scanning process.
4. **Adaptive Backoff & Anti-Throttling**:
   - Gmail throttles excessive burst commands. The tool includes polite pacing (`CHUNK_DELAY=0.05s`) and an automated retry mechanism with **exponential backoff** (1s, 2s, 4s) to seamlessly recover from transient disconnects or throttling alerts.


---

## Usage & Terminal Modes

Run the script from within the virtual environment:

```bash
cd unsubscribe
source .venv/bin/activate
python unsubscribe_harvest.py
```

### Clean Loading Bar vs. Verbose Output

- **Clean Loading Bar (Default)**: Overwrites the current line using a single loading bar to prevent terminal clutter:
  ```text
  [=======-------------] target.com
  ```
  Enabled automatically when `PROGRESS_BAR_MODE=true` in `.env`, or via the `--quiet` / `--progress` flag:
  ```bash
  python unsubscribe_harvest.py --progress
  ```

- **Verbose Output**: Prints every message and link line-by-line. Can be forced with `--verbose`:
  ```bash
  python unsubscribe_harvest.py --verbose
  ```

---

## Step-by-Step Workflow

1. **Inbox Scanning**:
   The script searches your `INBOX` for emails containing unsubscribe links or headers from newest to oldest.

2. **Domain Exclusion Loop**:
   A summary of discovered sender domains is printed. You can enter any domain name (e.g. `github.com`) to remove it from the cancellation list, or type `N` when finished.

3. **Step 1: Click Unsubscribe Links**:
   Select how you want to handle the unsubscribe URLs:
   - `[1] Automated HTTP request`: Dispatches background HTTP requests with real browser headers across parallel worker threads in synchronized batches/chunks (`EMAIL_CHUNK_SIZE`), dramatically accelerating speed while respecting chunk delays.
   - `[2] Open in browser tabs`: Opens tabs in your default web browser in manageable chunks (up to 10 tabs per batch) with confirmation prompts so your browser never freezes.
   - `[3] Both`: Sends automated HTTP requests in chunks, then offers to open any unconfirmed/failed links in browser batches.
   - `[S] Skip`: Bypasses clicking without modifying subscriptions.

4. **Step 2: Delete Domain Emails**:
   - The script queries your mailbox for emails from each unsubscribed domain.
   - It verifies that each email contains an unsubscribe button/link before flagging it.
   - A **Deletion Preview** table displays the exact email count per domain.
   - Confirms `[y/N]` before moving the messages to Gmail's `Trash` and expunging them from your inbox.

---

## Running Unit Tests

The test suite covers domain parsing, unsubscribe link extraction, loading bar rendering, and IMAP mock workflows:

```bash
cd unsubscribe
source .venv/bin/activate
python -m unittest discover -s tests -p "test_*.py" -v
```

---

## Safety & File Reference

- [.env](file:///Users/rd908/projects/Gmail-Filters/unsubscribe/.env): Local environment file holding `GMAIL_USER`, `GMAIL_APP_PASSWORD`, and `PROGRESS_BAR_MODE` (ignored by git).
- [.env.example](file:///Users/rd908/projects/Gmail-Filters/unsubscribe/.env.example): Template for `.env`.
- [unsubscribe_harvest.py](file:///Users/rd908/projects/Gmail-Filters/unsubscribe/unsubscribe_harvest.py): Main script containing IMAP harvesting, link processing, loading bar rendering, and email deletion logic.
- [tests/test_unsubscribe_harvest.py](file:///Users/rd908/projects/Gmail-Filters/unsubscribe/tests/test_unsubscribe_harvest.py): Automated unit test suite.
- [requirements.txt](file:///Users/rd908/projects/Gmail-Filters/unsubscribe/requirements.txt): Python dependencies (`beautifulsoup4`, `requests`, `python-dotenv`).
- [.vscode/settings.json](file:///Users/rd908/projects/Gmail-Filters/.vscode/settings.json): Configures the Antigravity IDE Python interpreter.

