from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import re
import requests
from pydantic import BaseModel

# ------------------- Gemini API ---------------------------
GEMINI_API_KEY = "YOUR_API_KEY_HERE"  # ⚠️ replace with your actual key
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# ------------------- Database Setup -----------------------
DATABASE_URL = "sqlite:///./backend/finance.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    amount = Column(Float, nullable=False)
    category = Column(String, default="general")
    description = Column(String, default="")
    ts = Column(DateTime, default=datetime.utcnow)
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


# ------------------- Gemini Finance Q&A -------------------
@app.post("/api/ask")
async def ask_question(payload: AskRequest):
    question = payload.question or payload.text
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }
    data = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": question}],
            }
        ]
    }

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


# --- add at the top with other imports ---
from collections import defaultdict
from fastapi import Body

# ------------------- Extra Models -----------------------
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


Base.metadata.create_all(bind=engine)

# ========================================================
# API ENDPOINTS TO MATCH FRONTEND
# ========================================================

# ------------------- Budgets ----------------------------
@app.get("/api/budget")
def list_budgets():
    sess = SessionLocal()
    budgets = sess.query(Budget).all()
    return {"budgets": [dict(id=b.id, category=b.category, monthly_amount=b.monthly_amount) for b in budgets]}

@app.post("/api/budget")
def create_budget(b: dict = Body(...)):
    sess = SessionLocal()
    budget = Budget(category=b["category"], monthly_amount=b["monthly_amount"])
    sess.add(budget)
    sess.commit()
    sess.refresh(budget)
    return {"id": budget.id, "category": budget.category, "monthly_amount": budget.monthly_amount}

@app.delete("/api/budget/{bid}")
def delete_budget(bid: int):
    sess = SessionLocal()
    b = sess.query(Budget).filter(Budget.id == bid).first()
    if not b:
        raise HTTPException(404, "budget not found")
    sess.delete(b)
    sess.commit()
    return {"status": "ok", "deleted": bid}

# ------------------- Goals ------------------------------
@app.get("/api/goal")
def list_goals():
    sess = SessionLocal()
    goals = sess.query(Goal).all()
    return {"goals": [dict(id=g.id, title=g.title, target_amount=g.target_amount,
                           start_date=g.start_date.isoformat(),
                           end_date=g.end_date.isoformat() if g.end_date else None)
                      for g in goals]}

@app.post("/api/goal")
def create_goal(g: dict = Body(...)):
    sess = SessionLocal()
    goal = Goal(title=g["title"], target_amount=g["target_amount"],
                end_date=datetime.fromisoformat(g["end_date"]) if g.get("end_date") else None)
    sess.add(goal)
    sess.commit()
    sess.refresh(goal)
    return {"id": goal.id, "title": goal.title, "target_amount": goal.target_amount}

@app.delete("/api/goal/{gid}")
def delete_goal(gid: int):
    sess = SessionLocal()
    g = sess.query(Goal).filter(Goal.id == gid).first()
    if not g:
        raise HTTPException(404, "goal not found")
    sess.delete(g)
    sess.commit()
    return {"status": "ok", "deleted": gid}

# ------------------- Recurring --------------------------
@app.get("/api/recurring")
def list_recurring():
    sess = SessionLocal()
    recs = sess.query(Recurring).all()
    return {"recurring": [dict(id=r.id, description=r.description, amount=r.amount,
                               category=r.category, interval=r.interval,
                               next_run=r.next_run.isoformat())
                          for r in recs]}

@app.post("/api/recurring")
def create_recurring(r: dict = Body(...)):
    sess = SessionLocal()
    next_date = datetime.fromisoformat(r["next_run"])
    rec = Recurring(description=r["description"], amount=r["amount"],
                    category=r.get("category", "general"),
                    interval=r.get("interval", "monthly"),
                    next_run=next_date)
    sess.add(rec)
    sess.commit()
    sess.refresh(rec)
    return {"id": rec.id}

@app.delete("/api/recurring/{rid}")
def delete_recurring(rid: int):
    sess = SessionLocal()
    r = sess.query(Recurring).filter(Recurring.id == rid).first()
    if not r:
        raise HTTPException(404, "recurring not found")
    sess.delete(r)
    sess.commit()
    return {"status": "ok", "deleted": rid}

@app.post("/api/recurring/run_due")
def run_due():
    sess = SessionLocal()
    now = datetime.utcnow()
    created = []
    recs = sess.query(Recurring).all()
    for r in recs:
        if r.next_run <= now:
            tx = Transaction(amount=r.amount, category=r.category,
                             description=r.description, ts=now,
                             is_expense=True)
            sess.add(tx)
            created.append(tx)
            # move next_run forward (simple monthly/weekly logic)
            if r.interval == "monthly":
                r.next_run = datetime(r.next_run.year, r.next_run.month, 1)  # naive
            elif r.interval == "weekly":
                r.next_run = r.next_run + timedelta(days=7)
    sess.commit()
    return {"created": [dict(id=t.id, desc=t.description) for t in created]}

# ------------------- Trends ------------------------------
@app.get("/api/trends")
def get_trends():
    sess = SessionLocal()
    rows = sess.query(Transaction).all()

    # dummy 6 months
    labels = []
    income_by_month = []
    expense_by_month = []
    now = datetime.utcnow()

    for i in range(5, -1, -1):
        month = (now.month - i - 1) % 12 + 1
        year = now.year if now.month - i > 0 else now.year - 1
        label = f"{year}-{month:02d}"
        labels.append(label)

        inc = sum(r.amount for r in rows if not r.is_expense and r.ts.strftime("%Y-%m") == f"{year}-{month:02d}")
        exp = sum(r.amount for r in rows if r.is_expense and r.ts.strftime("%Y-%m") == f"{year}-{month:02d}")

        income_by_month.append(inc)
        expense_by_month.append(exp)

    # category breakdown (last month)
    last_month = labels[-1]
    cat_breakdown = defaultdict(float)
    for r in rows:
        if r.is_expense and r.ts.strftime("%Y-%m") == last_month:
            cat_breakdown[r.category] += r.amount

    return {
        "labels": labels,
        "income": income_by_month,
        "expense": expense_by_month,
        "category_breakdown": cat_breakdown
    }

# ------------------- Analyze (AI wrapper) ----------------
@app.post("/api/analyze")
def analyze(payload: dict = Body(...)):
    question = payload.get("text", "")
    if not question:
        raise HTTPException(400, "text is required")

    # simply reuse Gemini call
    return ask_question(AskRequest(text=question))
