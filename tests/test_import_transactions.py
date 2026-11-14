"""Testes de regressão da carga (ST-2) e da escala do fraud_score.

Rodar:  python -m unittest discover -s tests -v   (ou pytest)
Usa um SQLite temporário; nunca toca em data/fio.db.
"""

import os
import pathlib
import sqlite3
import tempfile
from contextlib import closing
import unittest

_TMP = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
_DB_PATH = pathlib.Path(_TMP.name) / "test.db"
# Precisa ser definido ANTES de importar src.models.db_models (engine criado no import).
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH.as_posix()}"

import pandas as pd  # noqa: E402

from src.api.schemas.score import ScoreInput  # noqa: E402
from src.data import import_transactions as imp  # noqa: E402


def _make_csv(path: pathlib.Path, drop_cols: tuple[str, ...] = ()) -> None:
    n = 40
    df = pd.DataFrame({
        "event_external_id": [f"EV{i:03d}" for i in range(n)],
        "customer_id": [f"C{i % 5}" for i in range(n)],
        "card_id": [f"K{i % 7}" for i in range(n)],
        "acceptor_id": [f"A{i % 3}" for i in range(n)],
        "amount": [10.0 + i for i in range(n)],
        "event_occurred_at": ["2025-01-01T10:00:00"] * n,
        "fraud_score": [i * 10.0 for i in range(n)],  # escala 0–999
        "feedback_label": [False] * n,
        "decision": ["approve"] * n,
        "transaction_status": ["approved"] * n,
    }).drop(columns=list(drop_cols))
    df.to_csv(path, index=False)


def _count(table: str) -> int:
    with closing(sqlite3.connect(_DB_PATH)) as c:
        return c.execute(f"select count(*) from {table}").fetchone()[0]


class ImportTests(unittest.TestCase):
    def setUp(self):
        imp.engine.dispose()
        if _DB_PATH.exists():
            _DB_PATH.unlink()
        self.csv = pathlib.Path(_TMP.name) / "small.csv"
        _make_csv(self.csv)

    def tearDown(self):
        imp.engine.dispose()  # libera o arquivo (Windows)

    def test_rerun_does_not_duplicate_anything(self):
        imp.run(self.csv, None, dry_run=False)
        first = {t: _count(t) for t in ("customers", "cards", "acceptors", "transactions", "alerts")}
        self.assertEqual(first["transactions"], 40)
        self.assertGreater(first["alerts"], 0)

        imp.run(self.csv, None, dry_run=False)
        second = {t: _count(t) for t in first}
        self.assertEqual(first, second)

    def test_alerts_have_distinct_events(self):
        imp.run(self.csv, None, dry_run=False)
        with closing(sqlite3.connect(_DB_PATH)) as c:
            n, d = c.execute("select count(*), count(distinct event_external_id) from alerts").fetchone()
        self.assertEqual(n, d)

    def test_dry_run_does_not_create_database(self):
        imp.run(self.csv, None, dry_run=True)
        self.assertFalse(_DB_PATH.exists())

    def test_missing_required_column_exits_cleanly(self):
        bad = pathlib.Path(_TMP.name) / "bad.csv"
        _make_csv(bad, drop_cols=("acceptor_id",))
        with self.assertRaises(SystemExit):
            imp.run(bad, None, dry_run=True)

    def test_raw_json_keeps_numeric_types(self):
        import json
        imp.run(self.csv, None, dry_run=False)
        with closing(sqlite3.connect(_DB_PATH)) as c:
            raw = json.loads(c.execute("select raw_json from transactions limit 1").fetchone()[0])
        self.assertIsInstance(raw["fraud_score"], float)


class ScaleTests(unittest.TestCase):
    def test_score_input_accepts_native_bandeira_scale(self):
        ScoreInput(event_external_id="x", amount=1.0, fraud_score=369.0,
                   visa_fraud_score=999.0, mc_fraud_score=999.0)

    def test_score_input_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            ScoreInput(event_external_id="x", amount=1.0, fraud_score=5000.0)

    def test_alert_risk_level_uses_normalized_scale(self):
        try:
            from src.api.routers.alerts import _alert_to_response
        except ModuleNotFoundError:
            self.skipTest("fastapi não instalado")
        from types import SimpleNamespace as NS

        class _DB:
            def __init__(self, tx):
                self.tx = tx

            def get(self, model, key):
                return self.tx if model.__name__ == "Transaction" else None

        def level(score):
            tx = NS(amount=1.0, customer_id=None, event_occurred_at="x", fraud_score=score)
            alert = NS(alert_id="a", event_external_id="e", generated_at="x", status="NOVO", source="s")
            return _alert_to_response(alert, _DB(tx)).risk_level

        self.assertEqual(level(369.0), "baixo")
        self.assertEqual(level(500.0), "medio")
        self.assertEqual(level(800.0), "alto")


if __name__ == "__main__":
    unittest.main()
