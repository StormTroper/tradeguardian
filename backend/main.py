"""
main.py — FastAPI backend for TradeGuardian AI
Run: py -3.12 -m uvicorn main:app --reload
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional
import os

from checker import TradeInput, check_trade, check_daily_limits, behaviour_warnings
from database import (
    init_db, get_or_create_user, get_settings, update_settings,
    save_trade, close_trade, get_today_stats, get_loss_streak, get_recent_trades,
    log_violation, get_violations_summary,
    start_cooldown, get_active_cooldown, complete_cooldown,
    get_checklist_insights
)

app = FastAPI(title="TradeGuardian AI", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()


# ─── Models ────────────────────────────────────────────────────────────────────

class CheckTradeRequest(BaseModel):
    username: str
    lot_size: float = Field(..., gt=0)
    stop_loss_pips: float = Field(..., ge=0)
    account_size: Optional[float] = None
    max_risk_pct: Optional[float] = None
    # Checklist (optional — frontend sends if user answered)
    followed_rules: Optional[int] = None   # 1=yes, 0=no
    revenge_trading: Optional[int] = None  # 1=yes, 0=no
    valid_setup: Optional[int] = None      # 1=yes, 0=no
    emotion_score: Optional[int] = None    # 1-5


class SettingsUpdate(BaseModel):
    username: str
    account_size: Optional[float] = None
    max_risk_pct: Optional[float] = None
    max_trades_day: Optional[int] = None
    max_loss_day: Optional[float] = None
    pip_value: Optional[float] = None
    cooldown_minutes: Optional[int] = None


class CloseTradeRequest(BaseModel):
    trade_id: int
    result: str = Field(..., pattern="^(WIN|LOSS)$")
    pnl: float


class CompleteCooldownRequest(BaseModel):
    username: str
    cooldown_id: int
    reflection: str


# ─── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    same_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    parent_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "index.html")
    if os.path.exists(same_dir):
        return FileResponse(same_dir)
    if os.path.exists(parent_dir):
        return FileResponse(parent_dir)
    return {"status": "frontend not found"}


@app.post("/check-trade")
def check_trade_endpoint(req: CheckTradeRequest):
    user_id  = get_or_create_user(req.username)
    settings = get_settings(user_id)
    if not settings:
        raise HTTPException(500, "Could not load settings")

    account_size = req.account_size or settings["account_size"]
    max_risk_pct = req.max_risk_pct or settings["max_risk_pct"]

    # 1. Active cooldown check
    cooldown = get_active_cooldown(user_id)
    if cooldown:
        return {
            "status": "COOLDOWN",
            "reason": f"You are in a cooldown period. Reason: {cooldown['reason']}",
            "cooldown": cooldown,
            "actual_risk": None,
            "allowed_risk": None,
            "risk_percent_used": None,
            "behaviour_warnings": [],
            "today": get_today_stats(user_id),
            "trade_id": None
        }

    # 2. Checklist flags
    checklist_flags = []
    if req.revenge_trading == 1:
        checklist_flags.append("⚠️ You flagged this as a revenge trade. Are you sure?")
    if req.followed_rules == 0:
        checklist_flags.append("⚠️ You said you're not following your rules.")
    if req.valid_setup == 0:
        checklist_flags.append("⚠️ You said this isn't a valid setup.")
    if req.emotion_score is not None and req.emotion_score >= 4:
        checklist_flags.append(f"⚠️ High emotion score ({req.emotion_score}/5). Your data shows worse results when emotional.")

    # 3. Daily limits
    today_stats = get_today_stats(user_id)
    daily_block = check_daily_limits(
        trades_today=today_stats["trade_count"],
        losses_today=today_stats["total_loss"],
        max_trades=settings["max_trades_day"],
        max_daily_loss=settings["max_loss_day"]
    )
    if daily_block:
        # Auto-start cooldown on daily limit hit
        cd = start_cooldown(user_id, daily_block, settings["cooldown_minutes"])
        log_violation(user_id, "DAILY_LIMIT", daily_block)
        return {
            "status": "BLOCKED",
            "reason": daily_block,
            "cooldown": cd,
            "actual_risk": None,
            "allowed_risk": None,
            "risk_percent_used": None,
            "behaviour_warnings": [],
            "checklist_flags": checklist_flags,
            "today": today_stats,
            "trade_id": None
        }

    # 4. Trade rule check
    trade_input = TradeInput(
        account_size=account_size,
        max_risk_percent=max_risk_pct,
        stop_loss_pips=req.stop_loss_pips,
        lot_size=req.lot_size,
        pip_value=settings["pip_value"]
    )
    result = check_trade(trade_input)

    # 5. Log violations
    violations_logged = []
    if result.status == "REJECTED":
        log_violation(user_id, "RISK_EXCEEDED",
                      f"Risk £{result.actual_risk:.2f} vs limit £{result.allowed_risk:.2f}")
        violations_logged.append("RISK_EXCEEDED")
    if req.stop_loss_pips == 0:
        log_violation(user_id, "NO_STOP_LOSS", "Trade attempted without stop loss")
        violations_logged.append("NO_STOP_LOSS")
    if req.revenge_trading == 1:
        log_violation(user_id, "REVENGE_TRADE", "Trader self-reported revenge trade")
        violations_logged.append("REVENGE_TRADE")
    if req.followed_rules == 0:
        log_violation(user_id, "RULES_NOT_FOLLOWED", "Trader admitted not following rules")
        violations_logged.append("RULES_NOT_FOLLOWED")

    # 6. Behaviour warnings + loss streak cooldown
    loss_streak = get_loss_streak(user_id)
    warnings = behaviour_warnings(today_stats["trade_count"], loss_streak)

    cooldown_started = None
    if loss_streak >= 3:
        existing = get_active_cooldown(user_id)
        if not existing:
            reason = f"{loss_streak} consecutive losses — mandatory pause before next trade"
            cooldown_started = start_cooldown(user_id, reason, settings["cooldown_minutes"])
            log_violation(user_id, "LOSS_STREAK", f"{loss_streak} consecutive losses")

    # 7. Save trade
    trade_id = save_trade(
        user_id=user_id,
        account_size=account_size,
        lot_size=req.lot_size,
        stop_loss_pips=req.stop_loss_pips,
        actual_risk=result.actual_risk,
        status=result.status,
        followed_rules=req.followed_rules,
        revenge_trading=req.revenge_trading,
        valid_setup=req.valid_setup,
        emotion_score=req.emotion_score
    )

    # Update violation trade_id references if needed
    if violations_logged:
        from database import get_connection
        conn = get_connection()
        conn.execute("""
            UPDATE rule_violations SET trade_id = ?
            WHERE user_id = ? AND trade_id IS NULL
              AND violated_at >= datetime('now', '-5 seconds')
        """, (trade_id, user_id))
        conn.commit()
        conn.close()

    return {
        "status": result.status,
        "reason": result.reason,
        "actual_risk": result.actual_risk,
        "allowed_risk": result.allowed_risk,
        "risk_percent_used": result.risk_percent_used,
        "behaviour_warnings": warnings,
        "checklist_flags": checklist_flags,
        "cooldown": cooldown_started,
        "today": today_stats,
        "trade_id": trade_id
    }


@app.post("/close-trade")
def close_trade_endpoint(req: CloseTradeRequest):
    close_trade(req.trade_id, req.result, req.pnl)
    return {"status": "ok", "trade_id": req.trade_id}


@app.post("/complete-cooldown")
def complete_cooldown_endpoint(req: CompleteCooldownRequest):
    if not req.reflection or len(req.reflection.strip()) < 5:
        raise HTTPException(400, "Please write a meaningful reflection before continuing.")
    complete_cooldown(req.cooldown_id, req.reflection)
    return {"status": "ok"}


@app.get("/dashboard/{username}")
def get_dashboard(username: str):
    user_id      = get_or_create_user(username)
    settings     = get_settings(user_id)
    today_stats  = get_today_stats(user_id)
    loss_streak  = get_loss_streak(user_id)
    recent       = get_recent_trades(user_id)
    warnings     = behaviour_warnings(today_stats["trade_count"], loss_streak)
    violations   = get_violations_summary(user_id)
    insights     = get_checklist_insights(user_id)
    cooldown     = get_active_cooldown(user_id)

    return {
        "username": username,
        "settings": settings,
        "today": today_stats,
        "loss_streak": loss_streak,
        "behaviour_warnings": warnings,
        "recent_trades": recent,
        "violations": violations,
        "insights": insights,
        "cooldown": cooldown
    }


@app.put("/settings")
def update_settings_endpoint(req: SettingsUpdate):
    user_id = get_or_create_user(req.username)
    updates = req.model_dump(exclude={"username"}, exclude_none=True)
    update_settings(user_id, **updates)
    return {"status": "ok", "updated": updates}
