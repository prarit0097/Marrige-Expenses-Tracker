# Marriage Expense Tracker (Flask + SQLite/Postgres)

Modern wedding expense tracker with beautiful UI (Tailwind + DaisyUI), attachments, vendor/monthly reports (CSV + PDF), and Postgres-ready config.

## Local run (SQLite)
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
flask --app app.py init-db
python app.py
# open http://127.0.0.1:5000
```

## Deploy (Render + Neon Postgres)
1. Push this folder to a GitHub repo.
2. Create a Neon free Postgres project → copy connection string (ensure it starts with `postgresql://` or `postgres://`).
3. On Render → New → Web Service → connect the repo:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
   - Add env vars:
     - `SECRET_KEY` = any random string
     - `DATABASE_URL` = your Neon connection string (Render accepts either `postgres://` or `postgresql://`)
4. First-time DB init: run once locally `flask --app app.py init-db` *or* open a Render shell and run the same.
5. Done. Note: file uploads on free Render instances are not guaranteed to persist across deploys. For persistent attachments, integrate Cloudinary/S3.

## CSV format
`date,time,category,subcategory,vendor,description,amount,payment_mode,payment_type,notes,attachment`
