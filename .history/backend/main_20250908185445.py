from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import re

# ---------------------------
# Database setup
# ---------------------------
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

# ---------------------------
# FastAPI setup
# ---------------------------
app = FastAPI()

# Serve static files (CSS/JS/images) if any
app.mount("/static", StaticFiles(directory="backend"), name="static")

# Serve index.html at root
@app.get("/")
def root():
    return FileResponse("backend/index.html")

# ---------------------------
# Intent parser
# ---------------------------
def parse_command(text: str):
    text = text.lower()

    # Expense
    if "spent" in text or "bought" in text or "pay" in text:
        amt_match = re.search(r"(\d+(\.\d{1,2})?)", text)
        return {
            "intent": "add",
            "amount": float(amt_match.group()) if amt_match else None,
            "category": "expense",
            "description": text,
            "date": datetime.utcnow(),
        }

    # Income
    if "earned" in text or "got" in text or "received" in text or "salary" in text:
        amt_match = re.search(r"(\d+(\.\d{1,2})?)", text)
        return {
            "intent": "add_income",
            "amount": float(amt_match.group()) if amt_match else None,
            "category": "income",
            "description": text,
            "date": datetime.utcnow(),
        }

    # Auto-detect adding groceries/items
    if "add" in text or "groceries" in text or "buy" in text:
        return {
            "intent": "add",
            "amount": None,
            "category": "expense",
            "description": text,
            "date": datetime.utcnow(),
        }

    # Summary
    if "summary" in text or "balance" in text or "report" in text:
        return {"intent": "summary"}

    # List
    if "list" in text or "show" in text or "transactions" in text:
        return {"intent": "list"}

    # Delete
    if "delete" in text or "remove" in text:
        return {"intent": "delete"}

    return {"intent": "unknown"}

# ---------------------------
# API routes
# ---------------------------
@app.post("/api/command")
def handle_command(payload: dict):
    text = payload.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="text required")

    parsed = parse_command(text)
    sess = SessionLocal()

    # Add expense or income
    if parsed["intent"] in ("add", "add_income"):
        amt = parsed["amount"]
        if amt is None:
            # Ask frontend for amount
            return JSONResponse({
                "status": "need_amount",
                "message": "Please provide an amount for this transaction.",
                "description": parsed["description"],
                "category": parsed["category"],
            })
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
        return JSONResponse({
            "status": "ok",
            "action": "added",
            "id": tx.id,
            "amount": tx.amount,
            "category": tx.category,
            "is_expense": tx.is_expense
        })

    # Summary
    if parsed["intent"] == "summary":
        rows = sess.query(Transaction).all()
        total_income = sum(r.amount for r in rows if not r.is_expense)
        total_expense = sum(r.amount for r in rows if r.is_expense)
        balance = total_income - total_expense
        return JSONResponse({
            "status": "ok",
            "income": total_income,
            "expense": total_expense,
            "balance": balance
        })

    # List transactions
    if parsed["intent"] == "list":
        rows = sess.query(Transaction).order_by(Transaction.ts.desc()).limit(50).all()
        out = []
        for r in rows:
            out.append({
                "id": r.id,
                "amount": r.amount,
                "category": r.category,
                "desc": r.description,
                "ts": r.ts.isoformat(),
                "is_expense": r.is_expense
            })
        return JSONResponse({"status": "ok", "transactions": out})

    # Delete transaction
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

    return JSONResponse({"status": "error", "message": "Could not understand intent", "parsed": parsed})

# Health check
@app.get("/api/health")
def health():
    return JSONResponse({"status": "ok", "time": datetime.utcnow().isoformat()})
