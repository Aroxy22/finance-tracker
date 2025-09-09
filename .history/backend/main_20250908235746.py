from fastapi import FastAPI, HTTPException
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
GEMINI_API_KEY = "AIzaSyDVmqnKanW8VjDzliX8aGAzeCDeLViBTFo"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

class QuestionRequest(BaseModel):
    question: str
# ----------------------------------------------------------

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
# ----------------------------------------------------------

# ------------------- FastAPI App --------------------------
app = FastAPI()
app.mount("/static", StaticFiles(directory="backend"), name="static")

@app.get("/")
def root():
    return FileResponse("backend/index.html")
# ----------------------------------------------------------

# ------------------- Command Parsing ----------------------
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
# ----------------------------------------------------------

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

@app.get("/api/health")
def health():
    return JSONResponse({"status":"ok","time":datetime.utcnow().isoformat()})

# ------------------- Gemini Finance Q&A -------------------
{"status":"ok","question":"What is compound interest?","answer":{"parts":[{"text":"Compound interest is essentially **interest earned on interest**. It's the concept where the interest you earn on a deposit or investment is added to the original principal, and then the next interest calculation is based on the new, larger total. This creates a snowball effect, where your money grows at an accelerating rate over time.\n\nHere's a breakdown:\n\n*   **Principal:** The initial amount of money you invest or deposit.\n*   **Interest Rate:** The percentage at which your money earns interest (e.g., 5% per year).\n*   **Compounding Period:** How often the interest is calculated and added to the principal (e.g., annually, monthly, daily).\n\n**How it works:**\n\n1.  **Year 1:** You deposit $100 (principal) with an interest rate of 10% compounded annually.  You earn $10 in interest ($100 * 0.10 = $10). Your new balance is $110.\n\n2.  **Year 2:** You now earn interest on $110 (the original principal *plus* the interest from Year 1). You earn $11 in interest ($110 * 0.10 = $11). Your new balance is $121.\n\n3.  **Year 3:** You now earn interest on $121. You earn $12.10 in interest ($121 * 0.10 = $12.10). Your new balance is $133.10.\n\nNotice how the amount of interest you earn each year increases. That's the power of compounding.\n\n**The Formula:**\n\nThe formula for calculating compound interest is:\n\nA = P (1 + r/n)^(nt)\n\nWhere:\n\n*   **A** = the future value of the investment/loan, including interest\n*   **P** = the principal investment amount (the initial deposit or loan amount)\n*   **r** = the annual interest rate (as a decimal)\n*   **n** = the number of times that interest is compounded per year\n*   **t** = the number of years the money is invested or borrowed for\n\n**Example using the formula:**\n\nLet's say you invest $1,000 (P) at an annual interest rate of 5% (r = 0.05) compounded monthly (n = 12) for 10 years (t = 10).\n\nA = 1000 (1 + 0.05/12)^(12*10)\nA = 1000 (1 + 0.00416667)^(120)\nA = 1000 (1.00416667)^(120)\nA ≈ 1000 * 1.647009\nA ≈ $1647.01\n\nAfter 10 years, your investment would be worth approximately $1647.01.\n\n**Key Takeaways:**\n\n*   **Time is your friend:** The longer your money is invested, the more significant the effects of compounding become.\n*   **Higher interest rates accelerate growth:** The higher the interest rate, the faster your money will grow.\n*   **More frequent compounding is better:**  Compounding monthly or daily will generally lead to slightly higher returns than compounding annually (although the difference may not be huge).\n\n**In summary, compound interest is a powerful tool for wealth creation because it allows your money to grow exponentially over time.**  It's important to understand how it works and to use it to your advantage when saving and investing. It's also important to remember that compound interest can work against you when you have debt, as interest charges can accumulate quickly.\n"}],"role":"model"}}(base) Admins-MacBook-Pro-3:speech admin$ 