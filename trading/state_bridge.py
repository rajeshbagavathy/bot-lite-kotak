"""State/DB helpers that honor ``bot.*`` patches in unit tests."""
from trading.compat import resolve
from state import update_strategy as _update_strategy_impl
from state import set_spot as _set_spot_impl


def update_strategy(*args, **kwargs):
    return resolve("update_strategy", _update_strategy_impl)(*args, **kwargs)


def set_spot(*args, **kwargs):
    return resolve("set_spot", _set_spot_impl)(*args, **kwargs)
