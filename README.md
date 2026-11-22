# Work Hub

Internal work management: deliverables, scorecards, goals and documents. Flask, PostgreSQL, server-rendered pages.

## Run locally
    pip install -r requirements.txt
    export DATABASE_URL=postgresql://... JWT_SECRET_KEY=... STORAGE_DIR=instance/uploads
    python -c "from app.db import init_db; init_db()"
    HUB_CEO_EMAIL=you@example.com HUB_CEO_PASSWORD='A-long-Passw0rd' python seed.py seeds/homerr.json
    flask --app wsgi run
    python -m pytest            # needs DATABASE_URL pointing at a throwaway database: the tests drop its schema

## Deploy (Render + Neon)
1. Create the Neon database and copy its connection string (keep `sslmode=require`).
2. Push this folder to GitHub, then create a Render Blueprint from `render.yaml`. Paste the connection string as `DATABASE_URL`.
3. The first deploy creates the tables. Open a Render shell once and run the seed command above with your CEO email and a strong password.
4. Sign in, open **People** to add emails and passwords for the leads and members, and **Settings** to adjust the organisation.

Notes: uploads need the persistent disk (a paid Render feature) or a storage adapter in `app/storage.py`. Login rate limits are kept per worker.
