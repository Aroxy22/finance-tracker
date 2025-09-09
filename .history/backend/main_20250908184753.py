from fastapi import FastAPI
from fastapi.responses import FileResponse

app = FastAPI()

@app.get("/")
def root():
    # Adjust the path if needed
    return FileResponse("backend/index.html")
