import email
import io
import sys
import unittest
from unittest.mock import MagicMock, patch

import unsubscribe_harvest as uh


class TestExtractDomain(unittest.TestCase):
    def test_standard_domain(self):
        self.assertEqual(uh.extract_domain("User <info@example.com>"), "example.com")

    def test_subdomain(self):
        self.assertEqual(uh.extract_domain("Deals <promo@store.target.com>"), "target.com")

    def test_two_part_tld(self):
        self.assertEqual(uh.extract_domain("BBC News <news@mail.bbc.co.uk>"), "bbc.co.uk")
        self.assertEqual(uh.extract_domain("Support <help@service.com.au>"), "service.com.au")

    def test_invalid_and_none(self):
        self.assertIsNone(uh.extract_domain(None))
        self.assertIsNone(uh.extract_domain("no-at-sign-here"))


class TestDecodeEmailSubject(unittest.TestCase):
    def test_plain_subject(self):
        self.assertEqual(uh.decode_email_subject("Hello World"), "Hello World")

    def test_mime_encoded_subject(self):
        self.assertEqual(
            uh.decode_email_subject("=?UTF-8?B?VGVzdCBTdWJqZWN0?= "), "Test Subject"
        )

    def test_empty_subject(self):
        self.assertEqual(uh.decode_email_subject(""), "(No Subject)")
        self.assertEqual(uh.decode_email_subject(None), "(No Subject)")


class TestExtractUnsubscribeLink(unittest.TestCase):
    def test_list_unsubscribe_header_brackets(self):
        msg = email.message_from_string(
            "List-Unsubscribe: <https://example.com/unsub?id=123>\n\nBody"
        )
        self.assertEqual(
            uh.extract_unsubscribe_link(msg), "https://example.com/unsub?id=123"
        )

    def test_list_unsubscribe_header_unbracketed(self):
        msg = email.message_from_string(
            "List-Unsubscribe: https://example.com/unsub_raw\n\nBody"
        )
        self.assertEqual(
            uh.extract_unsubscribe_link(msg), "https://example.com/unsub_raw"
        )

    def test_html_anchor_text(self):
        html = '<p>Hi</p><a href="https://service.com/optout">Unsubscribe from list</a>'
        msg = email.message_from_string(f"Content-Type: text/html\n\n{html}")
        self.assertEqual(
            uh.extract_unsubscribe_link(msg), "https://service.com/optout"
        )

    def test_html_image_alt(self):
        html = '<a href="https://promo.com/u"><img alt="Unsubscribe Button" src="x.png"/></a>'
        msg = email.message_from_string(f"Content-Type: text/html\n\n{html}")
        self.assertEqual(uh.extract_unsubscribe_link(msg), "https://promo.com/u")

    def test_html_href_keyword(self):
        html = '<a href="https://news.com/unsubscribe/token123">Click here to manage settings</a>'
        msg = email.message_from_string(f"Content-Type: text/html\n\n{html}")
        self.assertEqual(
            uh.extract_unsubscribe_link(msg), "https://news.com/unsubscribe/token123"
        )

    def test_plain_text_fallback(self):
        body = "To unsubscribe from these updates, visit: https://updates.com/stop"
        msg = email.message_from_string(f"Content-Type: text/plain\n\n{body}")
        self.assertEqual(uh.extract_unsubscribe_link(msg), "https://updates.com/stop")

    def test_no_unsubscribe_receipt(self):
        body = "Thank you for your purchase! Total: $25.00."
        msg = email.message_from_string(f"Content-Type: text/html\n\n{body}")
        self.assertIsNone(uh.extract_unsubscribe_link(msg))


class TestProgressBar(unittest.TestCase):
    def test_create_loading_bar_format(self):
        # 50% progress
        bar = uh.create_loading_bar(5, 10, "target.com")
        self.assertEqual(bar, "[==========----------] target.com")

        # 0% progress without label
        bar_empty = uh.create_loading_bar(0, 10)
        self.assertEqual(bar_empty, "[--------------------]")

        # 100% progress
        bar_full = uh.create_loading_bar(10, 10, "done.com")
        self.assertEqual(bar_full, "[====================] done.com")

        # Alias check
        self.assertEqual(uh.create_progress_bar(1, 2), "[==========----------]")

    def test_render_progress_bar_format(self):
        buf = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = buf
            uh.render_progress_bar(5, 10, "target.com")
        finally:
            sys.stdout = old_stdout

        output = buf.getvalue()
        # Verify bracketed bar format: [=======-------------] <current_domain>
        self.assertIn("[==========----------] target.com", output)

    def test_finish_progress_bar(self):
        buf = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = buf
            uh.finish_progress_bar("Scan finished.")
        finally:
            sys.stdout = old_stdout

        self.assertIn("Scan finished.", buf.getvalue())


class TestTrashFolderDetection(unittest.TestCase):
    def test_standard_us_trash(self):
        mock_mail = MagicMock()
        mock_mail.list.return_value = (
            "OK",
            [
                b'(\\HasNoChildren \\Inbox) "/" "INBOX"',
                b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"',
            ],
        )
        self.assertEqual(uh.get_trash_folder(mock_mail), "[Gmail]/Trash")

    def test_uk_bin_folder(self):
        mock_mail = MagicMock()
        mock_mail.list.return_value = (
            "OK",
            [b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Bin"'],
        )
        self.assertEqual(uh.get_trash_folder(mock_mail), "[Gmail]/Bin")


class TestClickSingleLink(unittest.TestCase):
    def test_confirmed_unsubscribe(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "You have been successfully unsubscribed."
        mock_session.get.return_value = mock_resp

        ok, status, note = uh.click_single_link(
            "https://example.com/unsub", session=mock_session
        )
        self.assertTrue(ok)
        self.assertEqual(status, 200)
        self.assertIn("Unsubscribe confirmed", note)


class TestFindEmailsForDomain(unittest.TestCase):
    def test_filters_receipts_and_retains_unsubscribes(self):
        mock_mail = MagicMock()
        mock_mail.uid.side_effect = [
            ("OK", [b"1 2"]),  # SEARCH
            (
                "OK",
                [
                    (
                        b"1",
                        b"From: Promo <deals@store.com>\r\nList-Unsubscribe: <https://store.com/u>\r\n\r\n",
                    )
                ],
            ),  # FETCH 1
            (
                "OK",
                [
                    (
                        b"2",
                        b"From: Receipts <orders@store.com>\r\nSubject: Receipt\r\n\r\nPaid $10",
                    )
                ],
            ),  # FETCH 2
        ]

        results = uh.find_emails_for_domain_with_unsubscribe(mock_mail, "store.com")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["uid"], b"1")
        self.assertEqual(results[0]["link"], "https://store.com/u")

    def test_chunked_domain_search_with_body_fallback(self):
        mock_mail = MagicMock()
        mock_mail.uid.side_effect = [
            ("OK", [b"10 20 30"]),  # SEARCH
            # Header fetch for chunk of 3 UIDs
            (
                "OK",
                [
                    (
                        b"1 (UID 10 BODY[HEADER.FIELDS] {80}",
                        b"From: newsletter@shop.com\r\nList-Unsubscribe: <https://shop.com/u>\r\n\r\n",
                    ),
                    b")",
                    (
                        b"2 (UID 20 BODY[HEADER.FIELDS] {50}",
                        b"From: promo@shop.com\r\nSubject: Special Offer\r\n\r\n",
                    ),
                    b")",
                    (
                        b"3 (UID 30 BODY[HEADER.FIELDS] {50}",
                        b"From: orders@shop.com\r\nSubject: Receipt #123\r\n\r\n",
                    ),
                    b")",
                ],
            ),
            # Body fetch for UID 20 and 30 (which lacked List-Unsubscribe in headers)
            (
                "OK",
                [
                    (
                        b"2 (UID 20 BODY[] {120}",
                        b"From: promo@shop.com\r\nContent-Type: text/html\r\n\r\n<a href='https://shop.com/optout'>Unsubscribe</a>",
                    ),
                    b")",
                    (
                        b"3 (UID 30 BODY[] {100}",
                        b"From: orders@shop.com\r\nContent-Type: text/html\r\n\r\n<p>Your order total is $50.</p>",
                    ),
                    b")",
                ],
            ),
        ]

        results = uh.find_emails_for_domain_with_unsubscribe(
            mock_mail, "shop.com", chunk_size=5, chunk_delay=0.0
        )
        self.assertEqual(len(results), 2)
        uids = [r["uid"] for r in results]
        self.assertIn(b"10", uids)
        self.assertIn(b"20", uids)
        self.assertNotIn(b"30", uids)


class TestChunkingHelpers(unittest.TestCase):
    def test_chunk_list_standard(self):
        items = list(range(10))
        chunks = list(uh.chunk_list(items, chunk_size=3))
        self.assertEqual(chunks, [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]])

    def test_chunk_list_chunk_larger_than_items(self):
        items = [1, 2]
        chunks = list(uh.chunk_list(items, chunk_size=10))
        self.assertEqual(chunks, [[1, 2]])

    def test_chunk_list_empty(self):
        chunks = list(uh.chunk_list([], chunk_size=5))
        self.assertEqual(chunks, [])

    def test_chunk_list_invalid_chunk_size(self):
        items = [1, 2, 3]
        chunks = list(uh.chunk_list(items, chunk_size=0))
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], [1, 2, 3])


class TestParseFetchBatchData(unittest.TestCase):
    def test_parse_multi_message_uid_response(self):
        mock_data = [
            (
                b"1 (UID 101 BODY[HEADER.FIELDS (FROM LIST-UNSUBSCRIBE)] {60}",
                b"From: promo@store.com\r\nList-Unsubscribe: <https://store.com/u>\r\n\r\n",
            ),
            b")",
            (
                b"2 (UID 102 BODY[HEADER.FIELDS (FROM LIST-UNSUBSCRIBE)] {40}",
                b"From: news@daily.com\r\n\r\n",
            ),
            b")",
        ]
        parsed = uh.parse_fetch_batch_data(mock_data, use_uid=True)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0][0], b"101")
        self.assertIn(b"store.com", parsed[0][1])
        self.assertEqual(parsed[1][0], b"102")
        self.assertIn(b"daily.com", parsed[1][1])

    def test_parse_sequence_numbers(self):
        mock_data = [
            (
                b"42 (BODY[HEADER.FIELDS ...] {30}",
                b"From: hello@world.com\r\n\r\n",
            ),
            b")",
        ]
        parsed = uh.parse_fetch_batch_data(mock_data, use_uid=False)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0][0], b"42")

    def test_parse_empty_or_malformed(self):
        self.assertEqual(uh.parse_fetch_batch_data([]), [])
        self.assertEqual(uh.parse_fetch_batch_data([b")", None, "junk"]), [])


class TestExecuteImapChunkWithRetry(unittest.TestCase):
    def test_successful_first_call(self):
        mock_fn = MagicMock(return_value=("OK", [b"data"]))
        status, data = uh.execute_imap_chunk_with_retry(mock_fn, max_retries=2)
        self.assertEqual(status, "OK")
        self.assertEqual(data, [b"data"])
        self.assertEqual(mock_fn.call_count, 1)

    def test_retry_on_transient_error(self):
        mock_fn = MagicMock(
            side_effect=[Exception("Connection reset by peer"), ("OK", [b"recovered"])]
        )
        status, data = uh.execute_imap_chunk_with_retry(
            mock_fn, max_retries=2, initial_backoff=0.01
        )
        self.assertEqual(status, "OK")
        self.assertEqual(data, [b"recovered"])
        self.assertEqual(mock_fn.call_count, 2)

    def test_exceeds_max_retries(self):
        mock_fn = MagicMock(side_effect=Exception("Timeout"))
        status, data = uh.execute_imap_chunk_with_retry(
            mock_fn, max_retries=2, initial_backoff=0.01
        )
        self.assertEqual(status, "ERROR")
        self.assertEqual(data, [])
        self.assertEqual(mock_fn.call_count, 3)


class TestBuildUnsubscribeHashChunked(unittest.TestCase):
    def test_build_unsubscribe_hash_processes_chunks(self):
        mock_mail = MagicMock()
        # Search returns 3 UIDs: 101, 102, 103
        mock_mail.uid.side_effect = [
            ("OK", [b"101 102 103"]),  # SEARCH
            (
                "OK",
                [
                    (
                        b"1 (UID 103 BODY[HEADER.FIELDS] {80}",
                        b"From: newsletter@substack.com\r\nList-Unsubscribe: <https://substack.com/u>\r\n\r\n",
                    ),
                    b")",
                    (
                        b"2 (UID 102 BODY[HEADER.FIELDS] {80}",
                        b"From: promo@store.com\r\nList-Unsubscribe: <https://store.com/u>\r\n\r\n",
                    ),
                    b")",
                ],
            ),  # Chunk 1 FETCH headers (UID 103, 102)
            (
                "OK",
                [
                    (
                        b"3 (UID 101 BODY[HEADER.FIELDS] {80}",
                        b"From: older@store.com\r\nList-Unsubscribe: <https://store.com/old_u>\r\n\r\n",
                    ),
                    b")",
                ],
            ),  # Chunk 2 FETCH headers (UID 101)
        ]

        result = uh.build_unsubscribe_hash(
            mock_mail, progress_bar=False, chunk_size=2, chunk_delay=0.0
        )
        # Should have found substack.com and store.com
        self.assertEqual(len(result), 2)
        self.assertEqual(result["substack.com"], "https://substack.com/u")
        self.assertEqual(result["store.com"], "https://store.com/u")  # Newest link retained


class TestProcessUnsubscribeLinks(unittest.TestCase):
    def test_empty_map(self):
        buf = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = buf
            uh.process_unsubscribe_links({})
        finally:
            sys.stdout = old_stdout
        self.assertIn("No unsubscribe links to click.", buf.getvalue())

    @patch("builtins.input", return_value="S")
    def test_skip_option(self, mock_input):
        buf = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = buf
            uh.process_unsubscribe_links({"example.com": "https://example.com/unsub"})
        finally:
            sys.stdout = old_stdout
        self.assertIn("Skipped clicking unsubscribe links.", buf.getvalue())

    @patch("builtins.input", return_value="1")
    @patch("unsubscribe_harvest.click_single_link")
    def test_automated_http_chunking(self, mock_click, mock_input):
        mock_click.return_value = (True, 200, "HTTP 200 (Unsubscribe confirmed on page)")
        unsub_map = {
            f"domain{i}.com": f"https://domain{i}.com/unsub" for i in range(1, 6)
        }
        buf = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = buf
            uh.process_unsubscribe_links(
                unsub_map,
                progress_bar=False,
                chunk_size=2,
                chunk_delay=0.0,
            )
        finally:
            sys.stdout = old_stdout

        self.assertEqual(mock_click.call_count, 5)
        output = buf.getvalue()
        self.assertIn("in 3 chunk(s) (chunk size: 2)", output)
        self.assertIn("Automated request results: 5/5 succeeded", output)

    @patch("builtins.input")
    @patch("unsubscribe_harvest.click_single_link")
    @patch("webbrowser.open_new_tab")
    def test_automated_http_manual_fallback(self, mock_browser, mock_click, mock_input):
        mock_input.side_effect = ["1", "y"]  # Choose 1 (automated), then 'y' to open failed in browser
        mock_click.return_value = (False, 404, "HTTP 404")
        unsub_map = {"failed.com": "https://failed.com/unsub"}

        buf = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = buf
            uh.process_unsubscribe_links(
                unsub_map,
                progress_bar=False,
                chunk_size=5,
                chunk_delay=0.0,
            )
        finally:
            sys.stdout = old_stdout

        self.assertEqual(mock_click.call_count, 1)
        mock_browser.assert_called_once_with("https://failed.com/unsub")
        self.assertIn("1 link(s) failed or may require manual confirmation", buf.getvalue())

    @patch("builtins.input")
    @patch("webbrowser.open_new_tab")
    def test_browser_chunking(self, mock_browser, mock_input):
        # 15 links, chunk_size 10 -> 2 batches. First input is "2" (browser choice), second input is "" (Enter to continue batch)
        mock_input.side_effect = ["2", ""]
        unsub_map = {f"site{i}.com": f"https://site{i}.com/u" for i in range(1, 16)}

        buf = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = buf
            uh.process_unsubscribe_links(
                unsub_map,
                progress_bar=False,
                chunk_size=10,
                chunk_delay=0.0,
            )
        finally:
            sys.stdout = old_stdout

        self.assertEqual(mock_browser.call_count, 15)
        output = buf.getvalue()
        self.assertIn("Batching into 2 chunk(s) of up to 10 tabs each.", output)
        self.assertIn("Browser Batch 1/2", output)
        self.assertIn("Browser Batch 2/2", output)

    @patch("unsubscribe_harvest.render_progress_bar")
    @patch("unsubscribe_harvest.finish_progress_bar")
    @patch("webbrowser.open_new_tab")
    def test_open_links_in_browser_with_progress_bar(self, mock_browser, mock_finish, mock_render):
        links = [
            ("domain1.com", "https://domain1.com/unsub"),
            ("domain2.com", "https://domain2.com/unsub"),
        ]
        uh.open_links_in_browser(links, progress_bar=True, chunk_size=10, tab_delay=0.0)

        self.assertEqual(mock_browser.call_count, 2)
        mock_browser.assert_any_call("https://domain1.com/unsub")
        mock_browser.assert_any_call("https://domain2.com/unsub")

        self.assertEqual(mock_render.call_count, 2)
        mock_render.assert_any_call(1, 2, "domain1.com")
        mock_render.assert_any_call(2, 2, "domain2.com")
        mock_finish.assert_called_with("Finished opening tabs in browser.\n")

    @patch("builtins.input", return_value="")
    @patch("unsubscribe_harvest.render_progress_bar")
    @patch("unsubscribe_harvest.finish_progress_bar")
    @patch("webbrowser.open_new_tab")
    def test_open_links_in_browser_multi_batch_progress_bar(self, mock_browser, mock_finish, mock_render, mock_input):
        links = [f"https://site{i}.com/u" for i in range(1, 5)]
        uh.open_links_in_browser(links, progress_bar=True, chunk_size=2, tab_delay=0.0)

        self.assertEqual(mock_browser.call_count, 4)
        self.assertEqual(mock_render.call_count, 4)
        mock_input.assert_called_once()
        self.assertGreaterEqual(mock_finish.call_count, 2)
        mock_finish.assert_called_with("Finished opening tabs in browser.\n")

    @patch("builtins.input", return_value="s")
    @patch("webbrowser.open_new_tab")
    def test_open_links_in_browser_skip(self, mock_browser, mock_input):
        links = [f"https://site{i}.com/u" for i in range(1, 6)]
        buf = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = buf
            uh.open_links_in_browser(links, progress_bar=False, chunk_size=2, tab_delay=0.0)
        finally:
            sys.stdout = old_stdout

        self.assertEqual(mock_browser.call_count, 2)
        self.assertIn("Skipped remaining browser tabs.", buf.getvalue())

    @patch("builtins.input", return_value="2")
    @patch("unsubscribe_harvest.render_progress_bar")
    @patch("unsubscribe_harvest.finish_progress_bar")
    @patch("webbrowser.open_new_tab")
    def test_process_unsubscribe_links_browser_progress_bar(self, mock_browser, mock_finish, mock_render, mock_input):
        unsub_map = {
            "alpha.com": "https://alpha.com/u",
            "beta.com": "https://beta.com/u",
        }
        uh.process_unsubscribe_links(
            unsub_map,
            progress_bar=True,
            chunk_size=10,
            chunk_delay=0.0,
            tab_delay=0.0,
        )
        self.assertEqual(mock_browser.call_count, 2)
        self.assertEqual(mock_render.call_count, 2)
        mock_finish.assert_called_with("Finished opening tabs in browser.\n")

    def test_normalize_link_item(self):
        self.assertEqual(
            uh._normalize_link_item(("custom.org", "https://custom.org/u")),
            ("custom.org", "https://custom.org/u"),
        )
        self.assertEqual(
            uh._normalize_link_item("https://www.service.io/unsub"),
            ("service.io", "https://www.service.io/unsub"),
        )
        self.assertEqual(
            uh._normalize_link_item({"domain": "portal.net", "link": "https://portal.net/leave"}),
            ("portal.net", "https://portal.net/leave"),
        )



class TestEmailDateParsingAndComparison(unittest.TestCase):
    def test_parse_valid_rfc2822_dates(self):
        d1 = uh.parse_email_date("Mon, 12 Oct 2026 10:00:00 +0000")
        self.assertIsNotNone(d1)
        self.assertEqual(d1.year, 2026)
        self.assertEqual(d1.month, 10)
        self.assertEqual(d1.day, 12)

    def test_parse_timezone_offsets_normalized_to_utc(self):
        # 06:00:00 -0400 is 10:00:00 UTC
        d_ny = uh.parse_email_date("Mon, 12 Oct 2026 06:00:00 -0400")
        d_utc = uh.parse_email_date("Mon, 12 Oct 2026 10:00:00 +0000")
        self.assertEqual(d_ny, d_utc)

    def test_parse_invalid_or_none(self):
        self.assertIsNone(uh.parse_email_date(None))
        self.assertIsNone(uh.parse_email_date(""))
        self.assertIsNone(uh.parse_email_date("invalid date string"))

    def test_is_newer_email_date_chronological(self):
        newer = "Tue, 13 Oct 2026 10:00:00 +0000"
        older = "Mon, 12 Oct 2026 10:00:00 +0000"
        self.assertTrue(uh.is_newer_email_date(newer, older))
        self.assertFalse(uh.is_newer_email_date(older, newer))

    def test_is_newer_email_date_missing_current(self):
        self.assertTrue(uh.is_newer_email_date("Mon, 12 Oct 2026 10:00:00 +0000", None))
        self.assertTrue(uh.is_newer_email_date("Mon, 12 Oct 2026 10:00:00 +0000", ""))

    def test_is_newer_email_date_uid_fallback(self):
        # Equal dates: higher numeric UID is newer
        same_date = "Mon, 12 Oct 2026 10:00:00 +0000"
        self.assertTrue(uh.is_newer_email_date(same_date, same_date, new_uid=b"200", current_uid=b"100"))
        self.assertFalse(uh.is_newer_email_date(same_date, same_date, new_uid=b"100", current_uid=b"200"))


class TestUnsubscribeEntryAndMap(unittest.TestCase):
    def test_unsubscribe_entry_properties_and_equality(self):
        entry = uh.UnsubscribeEntry(
            link="https://store.com/unsub",
            date="Mon, 12 Oct 2026 10:00:00 +0000",
            emails=[{"uid": b"101", "subject": "Promo", "link": "https://store.com/unsub"}],
        )
        self.assertEqual(entry["link"], "https://store.com/unsub")
        self.assertEqual(entry["date"], "Mon, 12 Oct 2026 10:00:00 +0000")
        self.assertEqual(len(entry["emails"]), 1)
        # Equality with str URL preserves backwards compatibility
        self.assertEqual(entry, "https://store.com/unsub")
        self.assertEqual("https://store.com/unsub", entry)
        self.assertEqual(str(entry), "https://store.com/unsub")

    def test_unsubscribe_map_aggregation(self):
        unsub_map = uh.UnsubscribeMap()
        unsub_map["store.com"] = uh.UnsubscribeEntry(
            link="https://store.com/u",
            date="Mon, 12 Oct 2026",
            emails=[
                {"uid": b"1", "subject": "Sale 1", "link": "https://store.com/u"},
                {"uid": b"2", "subject": "Sale 2", "link": "https://store.com/u"},
            ],
        )
        unsub_map["news.com"] = uh.UnsubscribeEntry(
            link="https://news.com/u",
            date="Tue, 13 Oct 2026",
            emails=[{"uid": b"3", "subject": "Headline", "link": "https://news.com/u"}],
        )

        self.assertEqual(len(unsub_map.emails), 3)
        self.assertEqual(len(unsub_map.domain_emails["store.com"]), 2)
        self.assertEqual(len(unsub_map.domain_emails["news.com"]), 1)

        # Deleting domain removes its emails from domain_emails and emails
        del unsub_map["store.com"]
        self.assertNotIn("store.com", unsub_map.domain_emails)
        self.assertEqual(len(unsub_map.emails), 1)


class TestDateComparisonGuaranteesNewestLink(unittest.TestCase):
    def test_newest_link_saved_when_encountered_out_of_order(self):
        mock_mail = MagicMock()
        # Search returns 2 UIDs: 101 (older email) and 102 (newer email)
        # Mock IMAP returning older email first, then newer email second
        mock_mail.uid.side_effect = [
            ("OK", [b"101 102"]),
            (
                "OK",
                [
                    (
                        b"1 (UID 101 BODY[HEADER.FIELDS] {80}",
                        b"From: deals@retail.com\r\nDate: Mon, 01 Oct 2026 10:00:00 +0000\r\nList-Unsubscribe: <https://retail.com/old_unsub>\r\n\r\n",
                    ),
                    b")",
                ],
            ),
            (
                "OK",
                [
                    (
                        b"2 (UID 102 BODY[HEADER.FIELDS] {80}",
                        b"From: deals@retail.com\r\nDate: Mon, 12 Oct 2026 10:00:00 +0000\r\nList-Unsubscribe: <https://retail.com/new_unsub>\r\n\r\n",
                    ),
                    b")",
                ],
            ),
        ]

        result = uh.build_unsubscribe_hash(
            mock_mail, progress_bar=False, chunk_size=1, chunk_delay=0.0
        )
        self.assertIn("retail.com", result)
        # Guaranteed that newest link is saved even though older email was processed first
        self.assertEqual(result["retail.com"]["link"], "https://retail.com/new_unsub")
        self.assertEqual(result["retail.com"]["date"], "Mon, 12 Oct 2026 10:00:00 +0000")
        # List of emails contains both emails
        self.assertEqual(len(result["retail.com"]["emails"]), 2)
        uids = [e["uid"] for e in result["retail.com"]["emails"]]
        self.assertIn(b"101", uids)
        self.assertIn(b"102", uids)

    def test_newer_link_retained_when_older_email_encountered_later(self):
        mock_mail = MagicMock()
        # Newer email processed first, older email processed second
        mock_mail.uid.side_effect = [
            ("OK", [b"102 101"]),
            (
                "OK",
                [
                    (
                        b"1 (UID 102 BODY[HEADER.FIELDS] {80}",
                        b"From: deals@retail.com\r\nDate: Mon, 12 Oct 2026 10:00:00 +0000\r\nList-Unsubscribe: <https://retail.com/new_unsub>\r\n\r\n",
                    ),
                    b")",
                ],
            ),
            (
                "OK",
                [
                    (
                        b"2 (UID 101 BODY[HEADER.FIELDS] {80}",
                        b"From: deals@retail.com\r\nDate: Mon, 01 Oct 2026 10:00:00 +0000\r\nList-Unsubscribe: <https://retail.com/old_unsub>\r\n\r\n",
                    ),
                    b")",
                ],
            ),
        ]

        result = uh.build_unsubscribe_hash(
            mock_mail, progress_bar=False, chunk_size=1, chunk_delay=0.0
        )
        self.assertEqual(result["retail.com"]["link"], "https://retail.com/new_unsub")
        self.assertEqual(result["retail.com"]["date"], "Mon, 12 Oct 2026 10:00:00 +0000")
        # Both emails recorded in the list
        self.assertEqual(len(result["retail.com"]["emails"]), 2)


class TestDeleteEmailsWithoutRescanning(unittest.TestCase):
    @patch("builtins.input", return_value="y")
    def test_uses_pre_scanned_emails_without_imap_search_or_fetch(self, mock_input):
        mock_mail = MagicMock()
        mock_mail.list.return_value = ("OK", [b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"'])
        mock_mail.uid.return_value = ("OK", [b"OK"])

        domain_emails = {
            "shop.com": [
                {"uid": b"101", "subject": "Sale 1", "date": "Mon, 12 Oct 2026", "link": "https://shop.com/u"},
                {"uid": b"102", "subject": "Sale 2", "date": "Mon, 12 Oct 2026", "link": "https://shop.com/u"},
            ],
            "news.com": [
                {"uid": b"201", "subject": "Alert", "date": "Tue, 13 Oct 2026", "link": "https://news.com/u"},
            ],
        }

        buf = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = buf
            uh.delete_emails_for_domains(
                mock_mail,
                ["shop.com", "news.com"],
                domain_emails=domain_emails,
                progress_bar=False,
            )
        finally:
            sys.stdout = old_stdout

        output = buf.getvalue()
        # Verify it explicitly mentions no re-scanning
        self.assertIn("no re-scanning needed", output)
        self.assertRegex(output, r"TOTAL EMAILS TO DELETE:\s+3")
        self.assertIn("Successfully deleted 3 email(s)", output)

        # Crucial check: mock_mail.uid should NEVER have been called with "SEARCH" or "FETCH"
        call_types = [call[0][0] for call in mock_mail.uid.call_args_list]
        self.assertNotIn("SEARCH", call_types)
        self.assertNotIn("FETCH", call_types)
        self.assertIn("COPY", call_types)
        self.assertIn("STORE", call_types)


if __name__ == "__main__":
    unittest.main()

