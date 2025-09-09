from fastapi import FastAPI, HTTPException
@app.post('/api/command')
def handle_command(payload: dict):
"""Accepts JSON: {"text": "user transcript..."}
Returns JSON response indicating action/result.
"""
text = payload.get('text')
if not text:
raise HTTPException(status_code=400, detail='text required')

parsed = parse_command(text)
sess = SessionLocal()

if parsed['intent'] in ('add', 'add_income'):
amt = parsed['amount']
if amt is None:
raise HTTPException(status_code=400, detail='amount not found in sentence')
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
# naive: look for id in text
m = re.search(r"\b(\d{1,6})\b", text)
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


# lightweight health endpoint
@app.get('/api/health')
def health():
return JSONResponse({'status':'ok','time': datetime.utcnow().isoformat()})