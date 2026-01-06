# Robô DOU – CORM

## Frontend
- `frontend/index.html`, `frontend/app.js`, `frontend/style.css`

## Backend (FastAPI)
- `backend/api.py`
- `backend/requirements.txt`

### Como rodar backend local
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn api:app --reload --port 8000
```
