"""Kotak modify_order: SL -> marketable LIMIT uses quick_modification."""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from brokers.kotak_client import KotakNeoClient


class TestKotakModifyOrder(unittest.TestCase):
    @patch.object(KotakNeoClient, "_ensure")
    def test_limit_close_uses_quick_modification(self, _ensure):
        client = KotakNeoClient.__new__(KotakNeoClient)
        client._api = MagicMock()
        client._find_kotak_order_row = MagicMock(
            return_value={
                "nOrdNo": "128979",
                "tok": "1132629",
                "exSeg": "bse_fo",
                "prod": "MIS",
                "trdSym": "SENSEX2661173600CE",
                "trnsTp": "B",
                "ordSt": "open",
            }
        )
        quick = MagicMock(return_value={"stat": "Ok", "stCode": 200})
        with patch("brokers.kotak_client.ModifyOrder") as mod_cls:
            mod_cls.return_value.quick_modification = quick
            mod_cls.return_value.modification_with_orderid = MagicMock()
            resp = client.modify_order(
                app_order_id=128979,
                product_type="MIS",
                order_type="LIMIT",
                quantity=60,
                disclosed_quantity=0,
                stop_price=0,
                limit_price=101.0,
                time_in_force="DAY",
                tag="X_H_0946_SL_1132629",
            )
        quick.assert_called_once()
        mod_cls.return_value.modification_with_orderid.assert_not_called()
        self.assertEqual(resp.get("stat"), "Ok")
        self.assertEqual(quick.call_args[1]["order_type"], "L")
        self.assertEqual(quick.call_args[1]["trigger_price"], "0")
        self.assertEqual(quick.call_args[1]["price"], "101.0")
