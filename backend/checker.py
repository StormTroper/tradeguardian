"""
checker.py — Core trade rule logic for TradeGuardian AI
All rule decisions happen here, completely separate from the API layer.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class TradeInput:
    account_size: float       # e.g. 10000
    max_risk_percent: float   # e.g. 2.0 (meaning 2%)
    stop_loss_pips: float     # e.g. 20
    lot_size: float           # e.g. 0.5
    pip_value: float = 10.0   # default: £10 per pip per standard lot


@dataclass
class CheckResult:
    status: str               # "APPROVED" or "REJECTED" or "WARNING"
    reason: str
    actual_risk: float
    allowed_risk: float
    risk_percent_used: float


def check_trade(trade: TradeInput) -> CheckResult:
    """
    Run all rule checks on a proposed trade.
    Returns a CheckResult with the decision and reason.
    """

    # --- Rule 1: No stop loss ---
    if trade.stop_loss_pips <= 0:
        return CheckResult(
            status="REJECTED",
            reason="No stop loss set. You must define a stop loss before trading.",
            actual_risk=0,
            allowed_risk=_allowed_risk(trade),
            risk_percent_used=0
        )

    # --- Rule 2: No lot size ---
    if trade.lot_size <= 0:
        return CheckResult(
            status="REJECTED",
            reason="Lot size must be greater than zero.",
            actual_risk=0,
            allowed_risk=_allowed_risk(trade),
            risk_percent_used=0
        )

    # --- Core calculation ---
    actual_risk = trade.lot_size * trade.stop_loss_pips * trade.pip_value
    allowed = _allowed_risk(trade)
    risk_pct_used = (actual_risk / trade.account_size) * 100

    # --- Rule 3: Risk too high ---
    if actual_risk > allowed:
        return CheckResult(
            status="REJECTED",
            reason=(
                f"Risk £{actual_risk:.2f} exceeds your allowed limit of £{allowed:.2f} "
                f"({trade.max_risk_percent}% of £{trade.account_size:,.0f}). "
                f"Reduce lot size or widen stop loss."
            ),
            actual_risk=actual_risk,
            allowed_risk=allowed,
            risk_percent_used=risk_pct_used
        )

    # --- Rule 4: Warning zone (80–100% of limit) ---
    if actual_risk >= allowed * 0.8:
        return CheckResult(
            status="WARNING",
            reason=(
                f"Trade is within rules but risk is high at £{actual_risk:.2f} "
                f"({risk_pct_used:.1f}% of account). Proceed with caution."
            ),
            actual_risk=actual_risk,
            allowed_risk=allowed,
            risk_percent_used=risk_pct_used
        )

    # --- All clear ---
    return CheckResult(
        status="APPROVED",
        reason=f"Trade approved. Risk: £{actual_risk:.2f} ({risk_pct_used:.1f}% of account).",
        actual_risk=actual_risk,
        allowed_risk=allowed,
        risk_percent_used=risk_pct_used
    )


def check_daily_limits(trades_today: int, losses_today: float,
                        max_trades: int, max_daily_loss: float) -> Optional[str]:
    """
    Check whether the user has hit their daily trading limits.
    Returns a block reason string if blocked, None if clear.
    """
    if trades_today >= max_trades:
        return f"Daily trade limit reached ({trades_today}/{max_trades}). No more trades today."

    if losses_today >= max_daily_loss:
        return (
            f"Daily loss limit hit (£{losses_today:.2f} of £{max_daily_loss:.2f} allowed). "
            f"Stop trading for today."
        )

    return None  # All clear


def behaviour_warnings(trade_count: int, loss_streak: int) -> list[str]:
    """
    Soft behavioural warnings based on session patterns.
    These don't block — they alert.
    """
    warnings = []

    if trade_count >= 10:
        warnings.append("⚠️ You have placed 10+ trades today. You may be overtrading.")

    if loss_streak >= 3:
        warnings.append(
            f"⚠️ You have lost {loss_streak} trades in a row. "
            "Consider stepping away and reviewing before your next entry."
        )

    if loss_streak >= 5:
        warnings.append("🛑 5 consecutive losses. Strongly recommend stopping for today.")

    return warnings


# --- Internal helpers ---

def _allowed_risk(trade: TradeInput) -> float:
    return trade.account_size * (trade.max_risk_percent / 100)
