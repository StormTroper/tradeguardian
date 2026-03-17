# TradeGuardian AI — MVP

A real-time system that prevents traders from breaking their own rules.

---

## Project Structure

```
tradeguardian/
├── backend/
│   ├── checker.py        ← core rule logic (no framework dependency)
│   ├── database.py       ← all SQLite queries
│   ├── main.py           ← FastAPI routes
│   └── requirements.txt
├── frontend/
│   └── index.html        ← single-page dashboard (open in browser)
└── README.md
```

---

## Setup (5 minutes)

### 1. Install Python dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Start the backend

```bash
uvicorn main:app --reload
```

Backend will run at: http://localhost:8000  
Auto-docs at: http://localhost:8000/docs

### 3. Open the frontend

Just open `frontend/index.html` in your browser. No build step needed.

---

## How It Works

### Trade Checker
1. Enter your lot size and stop loss (pips)
2. Click **CHECK TRADE**
3. System calculates: `actual_risk = lot_size × stop_loss_pips × pip_value`
4. Compares against your max risk % of account size
5. Returns: APPROVED / WARNING / REJECTED

### Daily Limits
- Tracks trades per day per user
- Blocks once max trades or max daily loss is hit

### Behaviour Warnings
- Alerts if you're overtrading (10+ trades)
- Alerts on 3+ consecutive losses
- Hard warning on 5+ consecutive losses

---

## API Endpoints

| Method | Route | Purpose |
|--------|-------|---------|
| POST | `/check-trade` | Run all checks on a proposed trade |
| POST | `/close-trade` | Mark trade as WIN/LOSS with P&L |
| GET  | `/dashboard/{username}` | Get all stats for dashboard |
| PUT  | `/settings` | Update user's trading rules |

---

## What's Next (Version 2)

- [ ] MT5 integration — auto-import trade history
- [ ] Custom rule engine ("only trade London session")
- [ ] Sound/popup alerts
- [ ] Email / Telegram alerts on daily limit hit
