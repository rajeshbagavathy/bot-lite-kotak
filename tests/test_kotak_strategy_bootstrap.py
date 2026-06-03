"""Deferred Kotak TOTP must still populate strategy state after bootstrap."""

import unittest
from unittest.mock import MagicMock, patch

import bot
from config import INDEX_CONFIGS, StrategyConfig


class TestLoadStrategyState(unittest.TestCase):
    @patch("bot.replace_strategies")
    @patch("bot.restore_todays_strategies", return_value=[])
    @patch("bot.get_today_strategies")
    def test_load_strategy_state_pushes_to_ui(self, mock_today, _restore, mock_replace):
        mock_today.return_value = [
            StrategyConfig("X_H_0946", "09:46:00", 3, 20.0, 0.0),
            StrategyConfig("X_H_1016", "10:16:00", 3, 20.0, 0.0),
        ]
        client = MagicMock()
        client.get_order_book.return_value = []

        bot._load_strategy_state(client, INDEX_CONFIGS["SENSEX"])

        self.assertEqual(len(bot.STRATEGY_STATE), 2)
        self.assertEqual(bot.STRATEGY_STATE["X_H_0946"]["status"], "PENDING")
        mock_replace.assert_called_once_with(bot.STRATEGY_STATE)

    @patch("bot._start_calm_zone_monitor_once")
    @patch("bot._catch_up_missed_scheduled_strategies")
    @patch("bot._schedule_jobs")
    @patch("bot._load_strategy_state")
    @patch("bot.set_index")
    @patch("bot._pick_index_and_expiry")
    @patch("kotak_auth.get_client")
    def test_bootstrap_loads_strategies(
        self,
        mock_get_client,
        mock_pick,
        _set_index,
        mock_load,
        _sched,
        _catch,
        _calm,
    ):
        mock_get_client.return_value = MagicMock()
        mock_pick.return_value = (INDEX_CONFIGS["SENSEX"], "05JUN2026")
        bot._JOBS_SCHEDULED_FLAG = False

        bot._complete_kotak_bootstrap()

        mock_load.assert_called_once()
        bot._JOBS_SCHEDULED_FLAG = False


if __name__ == "__main__":
    unittest.main()
