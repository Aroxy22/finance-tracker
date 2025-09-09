from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import re, requests

# ---------------- DATABASE ---------------- #
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
    recurring = Column(Boolean, default=False)  # new column

Base.metadata.create_all(bind=engine)

# ---------------- FASTAPI APP ---------------- #
app = FastAPI()
app.mount("/static", StaticFiles(directory="backend"), name="static")

@app.get("/")
def root():
    return FileResponse("backend/index.html")

# ---------------- PARSE COMMAND ---------------- #
def parse_command(text: str):
    text = text.lower()
    if "spent" in text or "bought" in text or "pay" in text:
        amt_match = re.search(r"(\d+(\.\d{1,2})?)", text)
        return {"intent": "add", "amount": float(amt_match.group()) if amt_match else None,
                "category": "expense", "description": text, "date": datetime.utcnow()}
    if "earned" in text or "salary" in text or "received" in text:
        amt_match = re.search(r"(\d+(\.\d{1,2})?)", text)
        return {"intent": "add_income", "amount": float(amt_match.group()) if amt_match else None,
                "category": "income", "description": text, "date": datetime.utcnow()}
    if "summary" in text or "balance" in text:
        return {"intent": "summary"}
    if "list" in text or "transactions" in text:
        return {"intent": "list"}
    if "delete" in text or "remove" in text:
        return {"intent": "delete"}
    return {"intent": "unknown"}

# ---------------- HANDLE COMMAND ---------------- #
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
        return JSONResponse({"status":"ok","action":"added","id":tx.id,"amount":tx.amount,
                             "category":tx.category,"is_expense":tx.is_expense})
    if parsed["intent"] == "summary":
        rows = sess.query(Transaction).all()
        total_income = sum(r.amount for r in rows if not r.is_expense)
        total_expense = sum(r.amount for r in rows if r.is_expense)
        balance = total_income - total_expense
        return JSONResponse({"status":"ok","income":total_income,"expense":total_expense,"balance":balance})
    if parsed["intent"] == "list":
        rows = sess.query(Transaction).order_by(Transaction.ts.desc()).limit(50).all()
        out = [{"id": r.id, "amount": r.amount, "category": r.category, "desc": r.description, 
                "ts": r.ts.isoformat(), "is_expense": r.is_expense} for r in rows]
        return JSONResponse({"status":"ok","transactions":out})
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

# ---------------- HEALTH CHECK ---------------- #
@app.get("/api/health")
def health():
    return JSONResponse({"status":"ok","time":datetime.utcnow().isoformat()})

# ---------------- HUGGING FACE FINANCE Q&A ---------------- #
HF_API_URL = "https://api-inference.huggingface.co/models/Rustamshry/Qwen3-1.7B-finance-reasoning"
HF_HEADERS = {"Authorization": "Bearer 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"}  # <-- replace if regenerated

@app.post("/api/ask")
def ask_finance(payload: dict):
    question = payload.get("question")
    if not question:
        raise HTTPException(status_code=400, detail="question required")
    try:
        response = requests.post(
            HF_API_URL,
            headers=HF_HEADERS,
            json={
                "inputs": question,   # main input
                "parameters": {"max_new_tokens": 200}  # control response length
            },
            timeout=60,
        )
        response.raise_for_status()
        output = response.json()

        # Some models return [{"generated_text": "..."}]
        if isinstance(output, list) and "generated_text" in output[0]:
            answer = output[0]["generated_text"]
        else:
            answer = output

        return {"status": "ok", "answer": answer}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

