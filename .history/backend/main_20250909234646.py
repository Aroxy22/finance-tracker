from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime, timedelta
import re
import requests
from pydantic import BaseModel

# ------------------- Gemini API ---------------------------
GEMINI_API_KEY = "YOUR_GEMINI_KEY"  # replace with your real key
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


class Budget(Base):
    __tablename__ = "budgets"
    id = Column(Integer, primary_key=True, index=True)
    category = Column(String, nullable=False)
    monthly_amount = Column(Float, nullable=False)


class Goal(Base):
    __tablename__ = "goals"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    target_amount = Column(Float, nullable=False)
    start_date = Column(DateTime, default=datetime.utcnow)
    end_date = Column(DateTime, nullable=True)


class Recurring(Base):
    __tablename__ = "recurring"
    id = Column(Integer, primary_key=True, index=True)
    description = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    category = Column(String, default="general")
    interval = Column(String, default="monthly")  # monthly/weekly
    next_run = Column(DateTime, nullable=False)
    is_expense = Column(Boolean, default=True)


Base.metadata.create_all(bind=engine)

# ------------------- FastAPI App --------------------------
app = FastAPI()
app.mount("/static", StaticFiles(directory="backend"), name="static")


@app.get("/")
def root():
    return FileResponse("backend/index.html")


# ------------------- Request Models -----------------------
class CommandRequest(BaseModel):
    text: str


class EditRequest(BaseModel):
    id: int
    description: str
    amount: float
    is_expense: bool


class AskRequest(BaseModel):
    text: str | None = None
    question: str | None = None


class BudgetRequest(BaseModel):
    category: str
    monthly_amount: float


class GoalRequest(BaseModel):
    title: str
    target_amount: float
    end_date: str | None = None


class RecurringRequest(BaseModel):
    description: str
    amount: float
    category: str
    interval: str
    next_run: str
    is_expense: bool = True


# ------------------- Command Parsing ----------------------
def parse_command(text: str):
    text = text.lower()
    if "spent" in text or "bought" in text or "pay" in text:
        amt_match = re.search(r"(\d+(\.\d{1,2})?)", text)
        return {
            "intent": "add",
            "amount": float(amt_match.group()) if amt_match else None,
            "category": "expense",
            "description": text,
            "date": datetime.utcnow(),
        }
    if "earned" in text or "salary" in text or "received" in text:
        amt_match = re.search(r"(\d+(\.\d{1,2})?)", text)
        return {
            "intent": "add_income",
            "amount": float(amt_match.group()) if amt_match else None,
            "category": "income",
            "description": text,
            "date": datetime.utcnow(),
        }
    if "summary" in text or "balance" in text:
        return {"intent": "summary"}
    if "list" in text or "transactions" in text:
        return {"intent": "list"}
    if "delete" in text or "remove" in text:
        return {"intent": "delete"}
    return {"intent": "unknown"}


# ------------------- API Endpoints ------------------------
@app.post("/api/command")
def handle_command(payload: CommandRequest):
    text = payload.text
    parsed = parse_command(text)
    sess = SessionLocal()

    if parsed["intent"] in ("add", "add_income"):
        amt = parsed["amount"]
        if amt is None:
            return JSONResponse(
                {
                    "status": "need_amount",
                    "message": "Please provide amount",
                    "description": parsed["description"],
                    "category": parsed["category"],
                }
            )
        is_expense = parsed["intent"] == "add"
        tx = Transaction(
            amount=amt,
            category=parsed["category"],
            description=parsed["description"],
            ts=parsed["date"],
            is_expense=is_expense,
        )
        sess.add(tx)
        sess.commit()
        sess.refresh(tx)
        return JSONResponse(
            {
                "status": "ok",
                "action": "added",
                "id": tx.id,
                "amount": tx.amount,
                "category": tx.category,
                "is_expense": tx.is_expense,
            }
        )

    if parsed["intent"] == "summary":
        rows = sess.query(Transaction).all()
        total_income = sum(r.amount for r in rows if not r.is_expense)
        total_expense = sum(r.amount for r in rows if r.is_expense)
        balance = total_income - total_expense
        return JSONResponse(
            {
                "status": "ok",
                "income": total_income,
                "expense": total_expense,
                "balance": balance,
            }
        )

    if parsed["intent"] == "list":
        rows = sess.query(Transaction).order_by(Transaction.ts.desc()).limit(50).all()
        out = [
            {
                "id": r.id,
                "amount": r.amount,
                "category": r.category,
                "desc": r.description,
                "ts": r.ts.isoformat(),
                "is_expense": r.is_expense,
            }
            for r in rows
        ]
        return JSONResponse({"status": "ok", "transactions": out})

    if parsed["intent"] == "delete":
        m = re.search(r"\b(\d{1,6})\b", text)
        if not m:
            raise HTTPException(status_code=400, detail="no id found to delete")
        txid = int(m.group(1))
        tx = sess.query(Transaction).filter(Transaction.id == txid).first()
        if not tx:
            raise HTTPException(status_code=404, detail="transaction not found")
        sess.delete(tx)
        sess.commit()
        return JSONResponse({"status": "ok", "deleted_id": txid})

    return JSONResponse(
        {"status": "error", "message": "Could not understand intent", "parsed": parsed}
    )


@app.post("/api/edit")
def edit_transaction(payload: EditRequest):
    sess = SessionLocal()
    tx = sess.query(Transaction).filter(Transaction.id == payload.id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="transaction not found")

    tx.description = payload.description
    tx.amount = payload.amount
    tx.is_expense = payload.is_expense
    sess.commit()
    sess.refresh(tx)

    return {"status": "ok", "updated_id": tx.id}


@app.get("/api/health")
def health():
    return JSONResponse({"status": "ok", "time": datetime.utcnow().isoformat()})


# ------------------- Budgets -------------------
@app.get("/api/budget")
def get_budgets():
    sess = SessionLocal()
    rows = sess.query(Budget).all()
    return {"budgets": [{"id": b.id, "category": b.category, "monthly_amount": b.monthly_amount} for b in rows]}


@app.post("/api/budget")
def add_budget(req: BudgetRequest):
    sess = SessionLocal()
    b = Budget(category=req.category, monthly_amount=req.monthly_amount)
    sess.add(b)
    sess.commit()
    sess.refresh(b)
    return {"status": "ok", "id": b.id}


@app.delete("/api/budget/{bid}")
def delete_budget(bid: int):
    sess = SessionLocal()
    b = sess.query(Budget).filter(Budget.id == bid).first()
    if not b:
        raise HTTPException(status_code=404, detail="budget not found")
    sess.delete(b)
    sess.commit()
    return {"status": "ok"}


# ------------------- Goals -------------------
@app.get("/api/goal")
def get_goals():
    sess = SessionLocal()
    rows = sess.query(Goal).all()
    return {"goals": [{"id": g.id, "title": g.title, "target_amount": g.target_amount,
                       "start_date": g.start_date.isoformat(), "end_date": g.end_date.isoformat() if g.end_date else None} for g in rows]}


@app.post("/api/goal")
def add_goal(req: GoalRequest):
    sess = SessionLocal()
    end = datetime.fromisoformat(req.end_date) if req.end_date else None
    g = Goal(title=req.title, target_amount=req.target_amount, end_date=end)
    sess.add(g)
    sess.commit()
    sess.refresh(g)
    return {"status": "ok", "id": g.id}


@app.delete("/api/goal/{gid}")
def delete_goal(gid: int):
    sess = SessionLocal()
    g = sess.query(Goal).filter(Goal.id == gid).first()
    if not g:
        raise HTTPException(status_code=404, detail="goal not found")
    sess.delete(g)
    sess.commit()
    return {"status": "ok"}


# ------------------- Recurring -------------------
@app.get("/api/recurring")
def get_recurring():
    sess = SessionLocal()
    rows = sess.query(Recurring).all()
    return {"recurring": [{"id": r.id, "description": r.description, "amount": r.amount,
                           "category": r.category, "interval": r.interval, "next_run": r.next_run.isoformat()} for r in rows]}


@app.post("/api/recurring")
def add_recurring(req: RecurringRequest):
    sess = SessionLocal()
    r = Recurring(description=req.description, amount=req.amount, category=req.category,
                  interval=req.interval, next_run=datetime.fromisoformat(req.next_run), is_expense=req.is_expense)
    sess.add(r)
    sess.commit()
    sess.refresh(r)
    return {"status": "ok", "id": r.id}


@app.delete("/api/recurring/{rid}")
def delete_recurring(rid: int):
    sess = SessionLocal()
    r = sess.query(Recurring).filter(Recurring.id == rid).first()
    if not r:
        raise HTTPException(status_code=404, detail="recurring not found")
    sess.delete(r)
    sess.commit()
    return {"status": "ok"}


@app.post("/api/recurring/run_due")
def run_due():
    sess = SessionLocal()
    now = datetime.utcnow()
    due = sess.query(Recurring).filter(Recurring.next_run <= now).all()
    created = []
    for r in due:
        tx = Transaction(amount=r.amount, category=r.category, description=r.description,
                         ts=r.next_run, is_expense=r.is_expense)
        sess.add(tx)
        created.append(tx)
        # reschedule
        if r.interval == "monthly":
            r.next_run = r.next_run + timedelta(days=30)
        elif r.interval == "weekly":
            r.next_run = r.next_run + timedelta(days=7)
    sess.commit()
    return {"status": "ok", "created": [c.id for c in created]}


# ------------------- Trends -------------------
@app.get("/api/trends")
def trends():
    sess = SessionLocal()
    now = datetime.utcnow()
    labels, income, expense = [], [], []
    for i in range(5, -1, -1):
        start = datetime(now.year, now.month, 1) - timedelta(days=30 * i)
        end = datetime(start.year, start.month, 28) + timedelta(days=4)
        end = datetime(end.year, end.month, 1)
        rows = sess.query(Transaction).filter(Transaction.ts >= start, Transaction.ts < end).all()
        inc = sum(r.amount for r in rows if not r.is_expense)
        exp = sum(r.amount for r in rows if r.is_expense)
        labels.append(start.strftime("%b %Y"))
        income.append(inc)
        expense.append(exp)

    # last 30 days category breakdown
    month_start = datetime(now.year, now.month, 1)
    rows = sess.query(Transaction).filter(Transaction.ts >= month_start).all()
    cat = {}
    for r in rows:
        if r.is_expense:
            cat[r.category] = cat.get(r.category, 0) + r.amount

    return {"labels": labels, "income": income, "expense": expense, "category_breakdown": cat}


# ------------------- Gemini Finance Q&A -------------------
@app.post("/api/ask")
async def ask_question(payload: AskRequest):
    question = payload.question or payload.text
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    data = {"contents": [{"role": "user", "parts": [{"text": question}]}]}

    try:
        response = requests.post(GEMINI_URL, json=data, headers=headers, timeout=15)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        result = response.json()

        answer_text = "<div style='line-height:1.6; font-family:Segoe UI;'>"
        if "candidates" in result and len(result["candidates"]) > 0:
            parts = result["candidates"][0].get("content", {}).get("parts", [])
            if parts:
                raw_text = parts[0].get("text", "")
                clean_text = re.sub(r"(\*\*|\*|-|\_)", "", raw_text)
                clean_text = clean_text.replace("\n", "<br>")
                answer_text += clean_text
            else:
                answer_text += "No answer returned from Gemini."
        else:
            answer_text += "No answer returned from Gemini."
        answer_text += "</div>"

        return {"status": "ok", "question": question, "answer": answer_text}

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------- AI Analyze (simple stub) -------------------
@app.post("/api/analyze")
def analyze(req: AskRequest):
    text = req.text
    if not text:
        raise HTTPException(status_code=400, detail="text required")

    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    data = {"contents": [{"role": "user", "parts": [{"text": f"Financial analysis request: {text}"}]}]}

    try:
        response = requests.post(GEMINI_URL, json=data, headers=headers, timeout=15)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        result = response.json()

        analysis_text = ""
        if "candidates" in result and len(result["candidates"]) > 0:
            parts = result["candidates"][0].get("content", {}).get("parts", [])
            if parts:
                analysis_text = parts[0].get("text", "")
        return {"status": "ok", "analysis": analysis_text}
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))
