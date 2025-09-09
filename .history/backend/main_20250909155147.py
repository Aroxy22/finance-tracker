from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Enum, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta, date
import re
import requests
from pydantic import BaseModel
import enum
import calendar

# ------------------- Gemini API ---------------------------
GEMINI_API_KEY = "AIzaSyDVmqnKanW8VjDzliX8aGAzeCDeLViBTFo"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# ------------------- Database Setup -----------------------
DATABASE_URL = "sqlite:///./backend/finance.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ------------------- Models -------------------------------
class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    amount = Column(Float, nullable=False)
    category = Column(String, default="general")
    description = Column(String, default="")
    ts = Column(DateTime, default=datetime.utcnow)
    is_expense = Column(Boolean, default=True)
    recurring_id = Column(Integer, nullable=True)  # optional link to a recurring rule

class Budget(Base):
    __tablename__ = "budgets"
    id = Column(Integer, primary_key=True, index=True)
    category = Column(String, unique=True, index=True)
    monthly_amount = Column(Float, nullable=False)
    currency = Column(String, default="USD")

class Goal(Base):
    __tablename__ = "goals"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    target_amount = Column(Float, nullable=False)
    start_date = Column(Date, default=date.today)
    end_date = Column(Date, nullable=True)  # optional deadline

class RecurrenceIntervalEnum(str, enum.Enum):
    monthly = "monthly"
    weekly = "weekly"

class RecurringTransaction(Base):
    __tablename__ = "recurring"
    id = Column(Integer, primary_key=True, index=True)
    description = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    category = Column(String, default="general")
    is_expense = Column(Boolean, default=True)
    interval = Column(String, default=RecurrenceIntervalEnum.monthly.value)  # 'monthly' or 'weekly'
    next_run = Column(Date, nullable=True)

Base.metadata.create_all(bind=engine)

# ------------------- FastAPI App --------------------------
app = FastAPI()
app.mount("/static", StaticFiles(directory="backend"), name="static")

@app.get("/")
def root():
    return FileResponse("backend/index.html")

# ------------------- Helpers -------------------------------
def parse_command(text: str):
    text = (text or "").lower()
    if "spent" in text or "bought" in text or "pay" in text or "purchase" in text:
        amt_match = re.search(r"(\d+(\.\d{1,2})?)", text)
        # optional category after 'on' or 'for'
        cat_match = re.search(r"(?:on|for)\s+([a-zA-Z0-9 _-]+)", text)
        category = cat_match.group(1).strip() if cat_match else "general"
        return {"intent": "add", "amount": float(amt_match.group()) if amt_match else None,
                "category": category, "description": text, "date": datetime.utcnow()}
    if "earned" in text or "salary" in text or "received" in text:
        amt_match = re.search(r"(\d+(\.\d{1,2})?)", text)
        cat_match = re.search(r"(?:from|for)\s+([a-zA-Z0-9 _-]+)", text)
        category = cat_match.group(1).strip() if cat_match else "income"
        return {"intent": "add_income", "amount": float(amt_match.group()) if amt_match else None,
                "category": category, "description": text, "date": datetime.utcnow()}
    if "summary" in text or "balance" in text:
        return {"intent": "summary"}
    if "list" in text or "transactions" in text:
        return {"intent": "list"}
    if "delete" in text or "remove" in text:
        return {"intent": "delete"}
    if text.strip().isdigit() and len(text.strip())<7:
        return {"intent": "get_tx", "txid": int(text.strip())}
    return {"intent": "unknown"}

# ------------------- API Endpoints ------------------------
@app.post("/api/command")
def handle_command(payload: dict):
    text = payload.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    parsed = parse_command(text)
    sess = SessionLocal()

    if parsed["intent"] in ("add", "add_income"):
        amt = parsed["amount"]
        if amt is None:
            return JSONResponse({"status": "need_amount", "message": "Please provide amount", 
                                 "description": parsed["description"], "category": parsed["category"]})
        is_expense = parsed["intent"] == "add"
        tx = Transaction(amount=amt, category=parsed["category"], description=parsed["description"], 
                         ts=parsed["date"], is_expense=is_expense)
        sess.add(tx)
        sess.commit()
        sess.refresh(tx)
        # After adding, check budgets
        # compute this month's spending for that category
        now = datetime.utcnow()
        month_start = datetime(now.year, now.month, 1)
        spent = sum(r.amount for r in sess.query(Transaction).filter(
            Transaction.category==tx.category,
            Transaction.is_expense==True,
            Transaction.ts >= month_start
        ).all())
        budget = sess.query(Budget).filter(Budget.category==tx.category).first()
        warning = None
        if budget:
            pct = (spent / budget.monthly_amount) if budget.monthly_amount>0 else 0
            if pct >= 1.0:
                warning = f"Budget exceeded for {tx.category}: ${spent:.2f} / ${budget.monthly_amount:.2f}"
            elif pct >= 0.85:
                warning = f"Budget near limit for {tx.category}: ${spent:.2f} / ${budget.monthly_amount:.2f}"

        out = {"status":"ok","action":"added","id":tx.id,"amount":tx.amount,
                             "category":tx.category,"is_expense":tx.is_expense}
        if warning:
            out["budget_warning"] = warning
        return JSONResponse(out)

    if parsed["intent"] == "summary":
        rows = sess.query(Transaction).all()
        total_income = sum(r.amount for r in rows if not r.is_expense)
        total_expense = sum(r.amount for r in rows if r.is_expense)
        balance = total_income - total_expense
        return JSONResponse({"status":"ok","income":total_income,"expense":total_expense,"balance":balance})

    if parsed["intent"] == "list":
        rows = sess.query(Transaction).order_by(Transaction.ts.desc()).limit(200).all()
        out = [{"id": r.id, "amount": r.amount, "category": r.category, "desc": r.description, 
                "ts": r.ts.isoformat(), "is_expense": r.is_expense} for r in rows]
        return JSONResponse({"status":"ok","transactions":out})

    if parsed["intent"] == "get_tx":
        tx = sess.query(Transaction).filter(Transaction.id==parsed["txid"]).first()
        if not tx:
            raise HTTPException(status_code=404, detail="transaction not found")
        return JSONResponse({"status":"ok","transaction":{
            "id":tx.id,"amount":tx.amount,"category":tx.category,"desc":tx.description,"ts":tx.ts.isoformat(),"is_expense":tx.is_expense
        }})

    if parsed["intent"] == "delete":
        m = re.search(r"\b(\d{1,6})\b", text)
        if not m:
            raise HTTPException(status_code=400, detail="no id found to delete")
        txid = int(m.group(1))
        tx = sess.query(Transaction).filter(Transaction.id==txid).first()
        if not tx:
            raise HTTPException(status_code=404, detail="transaction not found")
        sess.delete(tx)
        sess.commit()
        return JSONResponse({"status":"ok","deleted_id":txid})

    return JSONResponse({"status":"error","message":"Could not understand intent","parsed":parsed})

@app.get("/api/health")
def health():
    return JSONResponse({"status":"ok","time":datetime.utcnow().isoformat()})

# ------------------- Budget Endpoints ---------------------
class BudgetIn(BaseModel):
    category: str
    monthly_amount: float
    currency: str = "USD"

@app.post("/api/budget")
def create_budget(b: BudgetIn):
    sess = SessionLocal()
    existing = sess.query(Budget).filter(Budget.category==b.category).first()
    if existing:
        existing.monthly_amount = b.monthly_amount
        existing.currency = b.currency
        sess.commit()
        return {"status":"ok","action":"updated","budget_id":existing.id}
    nb = Budget(category=b.category, monthly_amount=b.monthly_amount, currency=b.currency)
    sess.add(nb)
    sess.commit()
    sess.refresh(nb)
    return {"status":"ok","action":"created","budget_id":nb.id}

@app.get("/api/budget")
def list_budgets():
    sess = SessionLocal()
    rows = sess.query(Budget).all()
    out = []
    for r in rows:
        out.append({"id":r.id,"category":r.category,"monthly_amount":r.monthly_amount,"currency":r.currency})
    return {"status":"ok","budgets":out}

@app.delete("/api/budget/{budget_id}")
def delete_budget(budget_id: int):
    sess = SessionLocal()
    b = sess.query(Budget).filter(Budget.id==budget_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="budget not found")
    sess.delete(b)
    sess.commit()
    return {"status":"ok","deleted_id":budget_id}

# ------------------- Goals Endpoints -----------------------
class GoalIn(BaseModel):
    title: str
    target_amount: float
    start_date: date = None
    end_date: date = None

@app.post("/api/goal")
def create_goal(g: GoalIn):
    sess = SessionLocal()
    start = g.start_date or date.today()
    ng = Goal(title=g.title, target_amount=g.target_amount, start_date=start, end_date=g.end_date)
    sess.add(ng)
    sess.commit()
    sess.refresh(ng)
    return {"status":"ok","action":"created","goal_id":ng.id}

@app.get("/api/goal")
def list_goals():
    sess = SessionLocal()
    rows = sess.query(Goal).all()
    out = []
    for r in rows:
        out.append({"id":r.id,"title":r.title,"target_amount":r.target_amount,
                    "start_date":r.start_date.isoformat() if r.start_date else None,
                    "end_date":r.end_date.isoformat() if r.end_date else None})
    return {"status":"ok","goals":out}

@app.delete("/api/goal/{goal_id}")
def delete_goal(goal_id: int):
    sess = SessionLocal()
    g = sess.query(Goal).filter(Goal.id==goal_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="goal not found")
    sess.delete(g)
    sess.commit()
    return {"status":"ok","deleted_id":goal_id}

# ------------------- Recurring Endpoints --------------------
class RecurringIn(BaseModel):
    description: str
    amount: float
    category: str = "general"
    is_expense: bool = True
    interval: RecurrenceIntervalEnum = RecurrenceIntervalEnum.monthly
    next_run: date = None

@app.post("/api/recurring")
def create_recurring(r: RecurringIn):
    sess = SessionLocal()
    nr = r.next_run or (date.today() + timedelta(days=1))  # default next run tomorrow
    rr = RecurringTransaction(description=r.description, amount=r.amount, category=r.category,
                              is_expense=r.is_expense, interval=r.interval.value, next_run=nr)
    sess.add(rr)
    sess.commit()
    sess.refresh(rr)
    return {"status":"ok","action":"created","recurring_id":rr.id}

@app.get("/api/recurring")
def list_recurring():
    sess = SessionLocal()
    rows = sess.query(RecurringTransaction).all()
    out = []
    for r in rows:
        out.append({"id":r.id,"description":r.description,"amount":r.amount,"category":r.category,
                    "is_expense":r.is_expense,"interval":r.interval,"next_run":r.next_run.isoformat() if r.next_run else None})
    return {"status":"ok","recurring":out}

@app.delete("/api/recurring/{rid}")
def delete_recurring(rid: int):
    sess = SessionLocal()
    r = sess.query(RecurringTransaction).filter(RecurringTransaction.id==rid).first()
    if not r:
        raise HTTPException(status_code=404, detail="recurring rule not found")
    sess.delete(r)
    sess.commit()
    return {"status":"ok","deleted_id":rid}

@app.post("/api/recurring/run_due")
def run_due_recurrences():
    """Create transactions for all recurring rules whose next_run <= today.
    Update next_run accordingly. This endpoint can be called manually or scheduled externally."""
    sess = SessionLocal()
    today = date.today()
    created = []
    rows = sess.query(RecurringTransaction).filter(RecurringTransaction.next_run <= today).all()
    for r in rows:
        # create transaction
        tx = Transaction(amount=r.amount, category=r.category, description=f"[recurring] {r.description}",
                         ts=datetime.combine(r.next_run, datetime.min.time()), is_expense=r.is_expense, recurring_id=r.id)
        sess.add(tx)
        # update next_run
        if r.interval == RecurrenceIntervalEnum.monthly.value:
            # add one month
            y = r.next_run.year + (1 if r.next_run.month == 12 else 0)
            m = 1 if r.next_run.month == 12 else r.next_run.month + 1
            day = min(r.next_run.day, calendar.monthrange(y, m)[1])
            r.next_run = date(y, m, day)
        else:  # weekly
            r.next_run = r.next_run + timedelta(weeks=1)
        created.append({"recurring_id": r.id, "created_amount": r.amount, "category": r.category})
    sess.commit()
    return {"status":"ok","created":created}

# ------------------- Trends & Breakdown --------------------
@app.get("/api/trends")
def get_trends(months: int = 6):
    sess = SessionLocal()
    today = date.today()
    labels = []
    income_series = []
    expense_series = []
    for i in range(months-1, -1, -1):
        # month i months ago
        y = today.year
        m = today.month - i
        while m <= 0:
            m += 12
            y -= 1
        month_start = datetime(y, m, 1)
        last_day = calendar.monthrange(y, m)[1]
        month_end = datetime(y, m, last_day, 23, 59, 59)
        label = month_start.strftime("%b %Y")
        labels.append(label)
        rows = sess.query(Transaction).filter(Transaction.ts >= month_start, Transaction.ts <= month_end).all()
        inc = sum(r.amount for r in rows if not r.is_expense)
        exp = sum(r.amount for r in rows if r.is_expense)
        income_series.append(round(inc,2))
        expense_series.append(round(exp,2))
    # category breakdown for last month
    # use last month
    last_month = today.month-1 or 12
    last_year = today.year if today.month>1 else today.year-1
    ms = datetime(last_year, last_month, 1)
    me = datetime(last_year, last_month, calendar.monthrange(last_year, last_month)[1], 23,59,59)
    rows = sess.query(Transaction).filter(Transaction.ts >= ms, Transaction.ts <= me, Transaction.is_expense==True).all()
    cat = {}
    for r in rows:
        cat[r.category] = cat.get(r.category, 0) + r.amount
    return {"status":"ok","labels":labels,"income":income_series,"expense":expense_series,"category_breakdown":cat}

# ------------------- Analysis (AI) -------------------------
@app.post("/api/analyze")
async def analyze(payload: dict = None, request: Request = None):
    if not payload:
        payload = await request.json()
    text = payload.get("text") or payload.get("question")
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    # Use same Gemini call as /api/ask
    headers = {"Content-Type": "application/json", "X-goog-api-key": GEMINI_API_KEY}
    data = {"contents":[{"parts":[{"text": text}]}]}
    try:
        response = requests.post(GEMINI_URL, json=data, headers=headers, timeout=15)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        result = response.json()
        answer_text = ""
        if "candidates" in result and len(result["candidates"])>0:
            parts = result["candidates"][0].get("content", {}).get("parts", [])
            if parts:
                raw_text = parts[0].get("text", "")
                clean_text = re.sub(r"(\*\*|\*|-|\_)", "", raw_text)
                answer_text = clean_text
            else:
                answer_text = "No answer returned from Gemini."
        else:
            answer_text = "No answer returned from Gemini."
        return {"status":"ok","analysis":answer_text}
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

# ------------------- Legacy / Ask (kept for backwards compatibility) -----------------
@app.post("/api/ask")
async def ask_question(payload: dict = None, request: Request = None):
    return await analyze(payload, request)
