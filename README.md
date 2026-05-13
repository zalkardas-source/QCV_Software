# QCV Software

CV parsing and candidate management tool. Uploads PDF/DOCX CVs, extracts structured data via LLM, stores profiles, and exports PowerPoint summaries.

## Requirements

- Python 3.11+
- An [OpenRouter](https://openrouter.ai) API key

## Setup

**1. Clone and install dependencies**
```bash
git clone https://github.com/zalkardas-source/QCV_Software.git
cd QCV_Software
pip install -r requirements.txt
```

**2. Create your `.env` file**
```bash
cp .env.example .env
```
Then open `.env` and fill in:
- `JWT_SECRET` — a long random string (generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`)
- `OPENROUTER_API_KEY` — your OpenRouter key

**3. Create the admin user**
```bash
python scripts/create_admin.py
```
This is a one-time step. The server never creates users automatically.

## Running

```bash
python -m uvicorn backend.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

## Tests

```bash
python -m pytest tests/ -v
```

## Project structure

```
backend/        FastAPI app, models, auth, CV parsing logic
frontend/       Static HTML/JS frontend
scripts/        One-off admin tools (create_admin.py, view_db.py, ...)
tests/          Automated tests
.env.example    Template for environment variables
```
