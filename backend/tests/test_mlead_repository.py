import unittest
from unittest.mock import MagicMock, patch

from services.mlead.repository import MleadRepository


def _mock_connection():
    """A connect()-shaped mock supporting both `with closing(conn)` and
    `with conn` (repository.py uses `with closing(self._connect()) as conn, conn:`
    for writes)."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.__enter__.return_value = conn
    return conn, cursor


class SaveTests(unittest.TestCase):
    def test_save_includes_turnover_in_the_insert(self):
        conn, cursor = _mock_connection()
        cursor.fetchone.return_value = (42,)
        repo = MleadRepository(dsn="postgresql://fake")

        with patch.object(repo, "_connect", return_value=conn):
            lead_id = repo.save(
                company_name="Ambica Electro Control", gst="27AAPFU0939F1ZV",
                turnover=50.0,
            )

        self.assertEqual(lead_id, 42)
        sql, params = cursor.execute.call_args.args
        self.assertIn("turn_over", sql)
        self.assertIn(50.0, params)

    def test_save_with_no_turnover_still_works(self):
        conn, cursor = _mock_connection()
        cursor.fetchone.return_value = (7,)
        repo = MleadRepository(dsn="postgresql://fake")

        with patch.object(repo, "_connect", return_value=conn):
            lead_id = repo.save(company_name="Some Company")

        self.assertEqual(lead_id, 7)

    def test_save_turnover_is_a_number_not_the_raw_text_label(self):
        # turn_over is NUMERIC in the real schema - passing the raw
        # "50 Crore"-style string (rather than a pre-converted float, see
        # config/targeting.py's turnover_to_crore()) would raise
        # InvalidTextRepresentation against the real database. This test
        # only guards the call-site contract; see test_turnover_to_crore.py
        # for the conversion logic itself.
        conn, cursor = _mock_connection()
        cursor.fetchone.return_value = (1,)
        repo = MleadRepository(dsn="postgresql://fake")

        with patch.object(repo, "_connect", return_value=conn):
            repo.save(company_name="Some Company", turnover=12.8)

        _sql, params = cursor.execute.call_args.args
        self.assertIn(12.8, params)
        self.assertNotIn("Crore", str(params))


class BackfillBlankFieldsTests(unittest.TestCase):
    def test_no_truthy_fields_issues_no_query(self):
        conn, cursor = _mock_connection()
        repo = MleadRepository(dsn="postgresql://fake")

        with patch.object(repo, "_connect", return_value=conn):
            repo.backfill_blank_fields(42, gst=None, turnover="")

        cursor.execute.assert_not_called()

    def test_backfill_only_updates_the_fields_given(self):
        conn, cursor = _mock_connection()
        repo = MleadRepository(dsn="postgresql://fake")

        with patch.object(repo, "_connect", return_value=conn):
            repo.backfill_blank_fields(42, gst="27AAPFU0939F1ZV", turnover=50.0)

        sql, params = cursor.execute.call_args.args
        self.assertIn("gst = COALESCE(NULLIF(gst, ''), %(gst)s)", sql)
        # turn_over is NUMERIC - NULLIF(turn_over, '') itself raises
        # InvalidTextRepresentation against the real column (Postgres
        # tries to parse '' as a number to compare it), so this column
        # must use a plain NULL-only COALESCE, unlike every text column.
        self.assertIn("turn_over = COALESCE(turn_over, %(turn_over)s)", sql)
        self.assertNotIn("NULLIF(turn_over", sql)
        # Only the two fields actually passed appear in the SET clause -
        # nothing else on the row is touched.
        self.assertNotIn("city", sql)
        self.assertEqual(params["gst"], "27AAPFU0939F1ZV")
        self.assertEqual(params["turn_over"], 50.0)
        self.assertEqual(params["lead_id"], 42)

    def test_backfill_never_overwrites_an_existing_value_at_the_sql_level(self):
        # The COALESCE(NULLIF(col, ''), new_value) pattern is what actually
        # guarantees this at the database level - confirm it's present
        # rather than a plain unconditional "col = %(col)s" assignment.
        conn, cursor = _mock_connection()
        repo = MleadRepository(dsn="postgresql://fake")

        with patch.object(repo, "_connect", return_value=conn):
            repo.backfill_blank_fields(1, city="Coimbatore")

        sql, _params = cursor.execute.call_args.args
        self.assertIn("COALESCE(NULLIF(city, ''), %(city)s)", sql)


if __name__ == "__main__":
    unittest.main()
