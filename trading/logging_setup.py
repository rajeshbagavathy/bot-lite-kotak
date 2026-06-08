"""Bot logging configuration (stderr + bot.log)."""
import logging
import os
import sys


def _default_bot_log_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.log")


def _configure_bot_logging() -> None:
    env_lp = os.environ.get("BOT_LOG_PATH")
    log_path = os.path.abspath(env_lp) if env_lp else _default_bot_log_path()
    os.environ["BOT_LOG_PATH"] = log_path
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    for h in root.handlers[:]:
        root.removeHandler(h)
    stderr_root = logging.StreamHandler(sys.stderr)
    stderr_root.setFormatter(fmt)
    root.addHandler(stderr_root)

    bot_log = logging.getLogger("xts-bot-lite")
    bot_log.handlers.clear()
    bot_log.setLevel(logging.INFO)
    bot_log.propagate = False
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    bot_log.addHandler(sh)
    try:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        bot_log.addHandler(fh)
    except OSError as e:
        sys.stderr.write(f"WARNING: Could not open {log_path} for logging: {e}\n")

    for _name in (
        "werkzeug",
        "werkzeug.serving",
        "boto3",
        "botocore",
        "botocore.credentials",
        "urllib3",
        "urllib3.connectionpool",
        "s3transfer",
    ):
        _lg = logging.getLogger(_name)
        _lg.handlers.clear()
        _lg.propagate = False
        _lg.setLevel(logging.CRITICAL)

    bot_log.debug("Bot logging: stderr (journald) + file %s", log_path)
