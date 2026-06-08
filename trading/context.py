"""Process-wide mutable state shared across trading modules."""
from typing import Dict

STRATEGY_STATE: Dict[str, dict] = {}

_MAIN_LOOP_LAST_TICK: float = 0.0
_JOBS_SCHEDULED_FLAG: bool = False
_SCHEDULER_MINIMAL_MODE: bool = False

_LAST_MTM_LOG: Dict[str, float] = {}
_LAST_PORTFOLIO_MTM_SNAPSHOT_TS: float = 0.0

# Serialize concurrent strategy entry (one straddle placement at a time).
STRATEGY_EXEC_LOCK = None  # lazy threading.Lock in executor
