import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from xts_client import XTSClient


class TestXTSOhlcParse(unittest.TestCase):
    def test_parse_ohlc_data_response_comma_separated(self):
        payload = (
            "1775812559|23874.65|23913.65|23856.35|23893.25|0|0,"
            "1775812619|23922.50|23948.55|23917.30|23938.50|0|0,"
            "1775812679|23936.80|23951.15|23935.80|23943.35|0|0"
        )
        bars = XTSClient.parse_ohlc_data_response(payload)
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[0]["bar_unix"], 1775812559)
        self.assertEqual(bars[2]["bar_unix"], 1775812679)


if __name__ == "__main__":
    unittest.main()

