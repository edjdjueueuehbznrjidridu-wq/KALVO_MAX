import unittest
from apex import economic_calendar as calendar


SAMPLE = b'''BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:cpi-1@example\r\nDTSTART;TZID=America/New_York:20261110T083000\r\nSUMMARY:Consumer Price Index for October 2026\r\nURL:https://www.bls.gov/news.release/cpi.htm\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:jobs-1@example\r\nDTSTART:20261106T133000Z\r\nSUMMARY:Employment Situation for October 2026\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'''


class EconomicCalendarTests(unittest.TestCase):
    def test_parse_timezone_and_importance(self):
        # Nov 2026 sample dates, now is deliberately before the events.
        rows = calendar.parse_ics(SAMPLE, now=1794000000)
        self.assertEqual(len(rows), 2)
        cpi = next(x for x in rows if x['id'] == __import__('hashlib').sha256(b'cpi-1@example').hexdigest()[:20])
        self.assertEqual(cpi['source_timezone'], 'America/New_York')
        self.assertEqual(cpi['importance'], 'high')
        self.assertEqual(cpi['source_id'], 'bls')

    def test_folded_lines_are_unfolded(self):
        raw = b'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:folded\r\nDTSTART:20261201T120000Z\r\nSUMMARY:Consumer Price Index \r\n for November\r\nEND:VEVENT\r\nEND:VCALENDAR'
        rows = calendar.parse_ics(raw, now=1794000000)
        self.assertEqual(rows[0]['title'], 'Consumer Price Index for November')

    def test_invalid_and_missing_events_are_skipped(self):
        raw = b'BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:No date\nEND:VEVENT\nBEGIN:VEVENT\nDTSTART:bad\nSUMMARY:Bad date\nEND:VEVENT\nEND:VCALENDAR'
        self.assertEqual(calendar.parse_ics(raw, now=1794000000), [])

    def test_future_range_is_bounded(self):
        raw = b'BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20291201T120000Z\nSUMMARY:Too far\nEND:VEVENT\nEND:VCALENDAR'
        self.assertEqual(calendar.parse_ics(raw, now=1794000000), [])

    def test_fomc_date_ranges_are_date_only_and_source_attributed(self):
        html = b"""<html><h3>2027 FOMC Meetings</h3><p>January</p><p>26-27</p>
        <p>Statement</p><p>March</p><p>16-17*</p></html>"""
        rows = calendar.parse_fomc_html(html, now=1794000000)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source_id"], "fomc")
        self.assertEqual(rows[0]["time_precision"], "date_range")
        self.assertEqual(rows[0]["source_date"], "2027-01-26")
        self.assertEqual(rows[0]["source_end_date"], "2027-01-27")

    def test_ecb_events_are_date_only_and_importance_aware(self):
        html = b"""<html><h1>ECB meetings</h1>
        <p>16/12/2026</p><p>Governing Council of the ECB: monetary policy meeting (Day 1)</p>
        <p>17/12/2026</p><p>Governing Council of the ECB: monetary policy meeting (Day 2), followed by press conference</p>
        <p>24/02/2027</p><p>Governing Council of the ECB: non-monetary policy meeting</p>
        </html>"""
        rows = calendar.parse_ecb_html(html, now=1794000000)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["source_id"], "ecb")
        self.assertEqual(rows[0]["time_precision"], "date_only")
        self.assertEqual(rows[0]["importance"], "high")
        self.assertEqual(rows[-1]["importance"], "medium")

    def test_fetch_cache_and_failure(self):
        calendar.clear_cache()
        fake = lambda: SAMPLE
        first = calendar.fetch_calendar(force_refresh=True, now=1794000000, fetcher=fake)
        second = calendar.fetch_calendar(now=1794000000, fetcher=lambda: b'bad')
        self.assertEqual(len(first['events']), 2)
        self.assertTrue(second['cached'])
        calendar.clear_cache()
        failed = calendar.fetch_calendar(force_refresh=True, now=1794000000, fetcher=lambda: (_ for _ in ()).throw(OSError('offline')))
        self.assertEqual(failed['events'], [])
        self.assertTrue(failed['errors'])

    def test_all_source_outage_preserves_last_known_calendar_as_stale_fallback(self):
        from unittest.mock import patch
        calendar.clear_cache()
        good = calendar.fetch_calendar(force_refresh=True, now=1794000000, fetcher=lambda: SAMPLE)
        self.assertTrue(good['events'])
        with patch.object(calendar, '_fetch_ics', side_effect=OSError('offline')), \
             patch.object(calendar, '_fetch_html', side_effect=OSError('offline')):
            fallback = calendar.fetch_calendar(force_refresh=True, now=1794001000)
        self.assertTrue(fallback['cache_fallback'])
        self.assertTrue(fallback['cached'])
        self.assertTrue(fallback['events'])
        self.assertTrue(all(row.get('cache_fallback') for row in fallback['events']))
        calendar.clear_cache()
