from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
from dateutil import parser as dateparser
import re

# Database setup
Base = declarative_base()

class Transaction(Base):
    __tablename__ = 'transactions'
    id = Column(Integer, primary_key=True)
    amount = Column(Float, nullable=False)
    category = Column(String, nullable=True)
    description = Column(String, nullable=True)
    ts = Column(DateTime, default=datetime.utcnow)
    is_expense = Column(Boolean, default=True)

DATABASE_URL = 'sqlite:///./transactions.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(bind=engine)

# FastAPI app
app = FastAPI()

CATEGORY_KEYWORDS = {
    'food': ['restaurant','lunch','dinner','breakfast','cafe','coffee','pizza','burger','meal','eat'],
    'transport': ['uber','taxi','bus','train','metro','flight','cab','petrol','gas'],
    'groceries': ['supermarket','grocery','groceries','market'],
    'rent': ['rent','apartment'],
    'salary': ['salary','pay','income','received'],
    'entertainment': ['movie','netflix','concert','game','games','spotify'],
    'utilities': ['electricity','water','internet','bill','phone']
}

def extract_amount(text: str):
    m = re.search(r"(?P<amt>\d{1,3}(?:[,\\d]*)(?:\.\d+)?)", text)
    if m:
        s = m.group('amt').replace(',', '')
        try:
            return float(s)
        except:
            return None
    return None

def extract_date(text: str):
    try:
        if 'today' in text.lower():
            return datetime.utcnow()
        if 'yesterday' in text.lower():
            return datetime.utcnow() - timedelta(days=1)
        return dateparser.parse(text, fuzzy=True)
    except Exception:
        return datetime.utcnow()

def guess_category(text: str):
    text_l = text.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_l:
                return cat
    return 'other'

def parse_command(text: str):
    t = text.lower()
    intent = 'unknown'
    if any(k in t for k in ['add','spent','pay','paid','bought','purchase']):
        intent = 'add'
    if any(k in t for k in ['income','received','got paid','salary']):
        intent = 'add_income'
    if any(k in t for k in ['show','list','summary','balance']):
        if 'summary' in t or 'balance' in t:
            intent = 'summary'
        else:
            intent = 'list'
    if any(k in t for k in ['delete','remove']):
        intent = 'delete'

    return {
        'intent': intent,
        'amount': extract_amount(text),
        'category': guess_category(text),
        'date': extract_date(text),
        'description': text
    }

@app.post('/api/command')
def handle_command(payload: dict):
    text = payload.get('text')
    if not text:
        raise HTTPException(status_code=400, detail='text required')

    parsed = parse_command(text)
    sess = SessionLocal()

    if parsed['intent'] in ('add', 'add_income'):
        amt = parsed['amount']
        if amt is None:
            raise HTTPException(status_code=400, detail='amount not found')
        is_expense = parsed['intent'] == 'add'
        tx = Transaction(
            amount=amt,
            category=parsed['category'],
            description=parsed['description'],
            ts=parsed['date'],
            is_expense=is_expense
        )
        sess.add(tx)
        sess.commit()
        sess.refresh(tx)
        return JSONResponse({
            'status': 'ok',
            'action': 'added',
            'id': tx.id,
            'amount': tx.amount,
            'category': tx.category,
            'is_expense': tx.is_expense
        })

    if parsed['intent'] == 'summary':
        rows = sess.query(Transaction).all()
        total_income = sum(r.amount for r in rows if not r.is_expense)
        total_expense = sum(r.amount for r in rows if r.is_expense)
        balance = total_income - total_expense
        return JSONResponse({'status':'ok','income': total_income, 'expense': total_expense, 'balance': balance})

    if parsed['intent'] == 'list':
        rows = sess.query(Transaction).order_by(Transaction.ts.desc()).limit(50).all()
        out = []
        for r in rows:
            out.append({'id': r.id, 'amount': r.amount, 'category': r.category, 'desc': r.description, 'ts': r.ts.isoformat(), 'is_expense': r.is_expense})
        return JSONResponse({'status':'ok','transactions': out})

    if parsed['intent'] == 'delete':
        m = re.search(r"\\b(\\d{1,6})\\b", text)
        if not m:
            raise HTTPException(status_code=400, detail='no id found to delete')
        txid = int(m.group(1))
        tx = sess.query(Transaction).filter(Transaction.id==txid).first()
        if not tx:
            raise HTTPException(status_code=404, detail='transaction not found')
        sess.delete(tx)
        sess.commit()
        return JSONResponse({'status':'ok','deleted_id': txid})

    return JSONResponse({'status':'error','message':'Could not understand intent','parsed': parsed})

@app.get('/api/health')
def health():
    return JSONResponse({'status':'ok','time': datetime.utcnow().isoformat()})

# serve frontend
app.mount("/", StaticFiles(directory=".", html=True), name="frontend")
