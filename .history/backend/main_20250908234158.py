from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
from transformers import pipeline

# ---------------------------
# Database setup
# ---------------------------
engine = create_engine("sqlite:///finance.db")
Session = sessionmaker(bind=engine)
Base = declarative_base()

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    amount = Column(Float, nullable=False)
    category = Column(String, nullable=False)
    description = Column(String, nullable=True)
    ts = Column(DateTime, default=datetime.utcnow)
    is_expense = Column(Boolean, default=True)

Base.metadata.create_all(engine)

# ---------------------------
# FastAPI setup
# ---------------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/", StaticFiles(directory="backend", html=True), name="frontend")

# ---------------------------
# Hugging Face local pipeline
# ---------------------------
print("Loading local Hugging Face model... (this may take a while)")
nlp = pipeline("text-generation", model="gpt2")

# ---------------------------
# API models
# ---------------------------
class CommandRequest(BaseModel):
    text: str

class AskRequest(BaseModel):
    question: str

# ---------------------------
# Endpoints
# ---------------------------
@app.post("/api/command")
def handle_command(req: CommandRequest):
    sess = Session()
    text = req.text.lower()

    if "spent" in text or "expense" in text:
        words = text.split()
        try:
            amount = float([w for w in words if w.replace(".", "", 1).isdigit()][0])
            category = "expense"
            t = Transaction(amount=amount, category=category, description=text, is_expense=True)
            sess.add(t)
            sess.commit()
            return {"message": f"Added expense: {amount}"}
        except Exception as e:
            return {"error": f"Could not parse amount. {str(e)}"}

    elif "earned" in text or "income" in text:
        words = text.split()
        try:
            amount = float([w for w in words if w.replace(".", "", 1).isdigit()][0])
            category = "income"
            t = Transaction(amount=amount, category=category, description=text, is_expense=False)
            sess.add(t)
            sess.commit()
            return {"message": f"Added income: {amount}"}
        except Exception as e:
            return {"error": f"Could not parse amount. {str(e)}"}

    elif "show summary" in text:
        income = sess.query(Transaction).filter_by(is_expense=False).all()
        expenses = sess.query(Transaction).filter_by(is_expense=True).all()
        total_income = sum(t.amount for t in income)
        total_expense = sum(t.amount for t in expenses)
        balance = total_income - total_expense
        return {
            "income": total_income,
            "expenses": total_expense,
            "balance": balance,
        }

    elif "list" in text:
        rows = sess.query(Transaction).order_by(Transaction.ts.desc()).limit(50).all()
        return [
            {
                "id": r.id,
                "amount": r.amount,
                "category": r.category,
                "description": r.description,
                "timestamp": r.ts.isoformat(),
                "is_expense": r.is_expense,
            }
            for r in rows
        ]

    return {"message": "Command not recognized."}

@app.delete("/api/transaction/{txn_id}")
def delete_transaction(txn_id: int):
    sess = Session()
    txn = sess.query(Transaction).filter_by(id=txn_id).first()
    if txn:
        sess.delete(txn)
        sess.commit()
        return {"message": f"Transaction {txn_id} deleted."}
    return {"error": "Transaction not found."}

@app.post("/api/ask")
def ask_question(req: AskRequest):
    """Use local Hugging Face model to answer finance questions"""
    try:
        result = nlp(
            req.question,
            max_new_tokens=80,
            num_return_sequences=1,
            do_sample=True,
        )
        answer = result[0]["generated_text"]
        return {"answer": answer}
    except Exception as e:
        return {"error": str(e)}

@app.get("/health")
def health():
    return {"status": "ok"}
