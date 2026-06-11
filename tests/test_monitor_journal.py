"""Trade journal lines for post-entry monitor lifecycle."""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import bot
from trading.context import STRATEGY_STATE
from trading.journal import Phase, init_journal, read_tail


class TestMonitorJournal(unittest.TestCase):
    def setUp(self):
        self._orig = dict(STRATEGY_STATE)
        STRATEGY_STATE.clear()
        self._tmpdir = tempfile.mkdtemp()
        self._journal = os.path.join(self._tmpdir, "trade_journal.jsonl")
        init_journal(self._journal)

    def tearDown(self):
        STRATEGY_STATE.clear()
        STRATEGY_STATE.update(self._orig)

    def _phases(self) -> list:
        return [e.get("phase") for e in read_tail(50)]

    def test_sl_filled_writes_journal(self):
        STRATEGY_STATE["X_W_1201"] = {
            "name": "X_W_1201",
            "status": "OPEN",
            "db_id": 0,
            "positions": [
                {"instrument_id": 1132512, "exit_price": None, "symbol": "CE", "entry_price": 316.55},
                {"instrument_id": 1132443, "exit_price": None, "symbol": "PE", "entry_price": 297.0},
            ],
            "sl_orders": [{"app_order_id": 272015, "tag": "X_W_1201_SL_1132512"}],
            "sl_tag_map": {"X_W_1201_SL_1132512": 1132512},
        }
        strategy = STRATEGY_STATE["X_W_1201"]
        order_book = [
            {
                "AppOrderID": 272015,
                "OrderUniqueIdentifier": "X_W_1201_SL_1132512",
                "OrderStatus": "FILLED",
                "OrderAverageTradedPrice": 380.0,
            }
        ]
        bot._sync_sl_order_status_and_capture_exits(MagicMock(), strategy, order_book=order_book)
        self.assertIn(Phase.SL_FILLED.value, self._phases())
        self.assertIsNotNone(strategy["positions"][0].get("exit_price"))

    @patch("bot.SURVIVOR_SL_TO_COST_ENABLED", True)
    @patch("bot._broker_modify_order_ok", return_value=True)
    def test_survivor_sl_to_cost_writes_journal(self, _ok):
        STRATEGY_STATE["X_W_1201"] = {
            "name": "X_W_1201",
            "status": "OPEN",
            "survivor_sl_adjusted_to_cost": False,
            "positions": [
                {
                    "instrument_id": 1132512,
                    "exit_price": 380.0,
                    "closed_via": "SL_FILLED",
                    "entry_price": 316.55,
                },
                {"instrument_id": 1132443, "exit_price": None, "entry_price": 297.0, "quantity": -20},
            ],
            "sl_orders": [
                {"app_order_id": 272015, "tag": "X_W_1201_SL_1132512"},
                {"app_order_id": 272016, "tag": "X_W_1201_SL_1132443"},
            ],
            "sl_tag_map": {
                "X_W_1201_SL_1132512": 1132512,
                "X_W_1201_SL_1132443": 1132443,
            },
        }
        client = MagicMock()
        index_config = MagicMock()
        index_config.tick_size = 0.05
        order_book = [
            {
                "AppOrderID": 272016,
                "OrderUniqueIdentifier": "X_W_1201_SL_1132443",
                "OrderStatus": "NEW",
                "OrderQuantity": 20,
                "ProductType": "MIS",
                "OrderPrice": 350.0,
                "OrderStopPrice": 349.5,
            }
        ]
        verified_book = [
            {
                **order_book[0],
                "OrderPrice": 297.0,
                "LimitPrice": 297.0,
                "OrderStopPrice": 296.5,
                "StopPrice": 296.5,
            }
        ]
        client.get_order_book.side_effect = [order_book, verified_book, verified_book]
        client.interactive.ORDER_TYPE_STOPLIMIT = "STOPLIMIT"
        client.interactive.PRODUCT_MIS = "MIS"
        client.interactive.VALIDITY_DAY = "DAY"
        client.modify_order.return_value = {"stat": "Ok"}
        bot._adjust_survivor_sl_to_cost_after_peer_sl(
            client, index_config, STRATEGY_STATE["X_W_1201"], order_book=order_book
        )
        phases = self._phases()
        self.assertIn(Phase.SURVIVOR_SL_TO_COST.value, phases)
        events = read_tail(50)
        survivor_msgs = [e for e in events if e.get("phase") == Phase.SURVIVOR_SL_TO_COST.value]
        self.assertTrue(
            any("tightened to cost" in e.get("message", "") for e in survivor_msgs),
            survivor_msgs,
        )

    def test_strategy_close_writes_journal(self):
        STRATEGY_STATE["S"] = {"name": "S", "status": "OPEN", "db_id": 0, "mtm": -100.0, "realized": 0.0}
        client = MagicMock()
        with patch.object(bot, "_close_strategy_via_open_sl_orders"):
            with patch.object(bot, "update_strategy"):
                bot._close_strategy(client, MagicMock(), STRATEGY_STATE["S"], [], "Strategy SL hit")
        self.assertIn(Phase.STRATEGY_CLOSE.value, self._phases())
