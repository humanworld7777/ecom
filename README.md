# Ecom API

A small e-commerce backend built with FastAPI, SQLAlchemy and PostgreSQL.

## Features
- Register and log in (JWT tokens, bcrypt password hashing)
- Admin-only product management
- Per-user shopping cart with stock checks
- Checkout that creates an order and reduces stock
- Order history

## Running locally
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Then open http://127.0.0.1:8000/docs. Without `DATABASE_URL` it uses a local SQLite file.

## Environment variables
| Name | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `SECRET_KEY` | Signs login tokens |
| `ADMIN_EMAIL` | The account with this email becomes an admin |

## Tests
```bash
pip install -r requirements-dev.txt
pytest
```
Tests also run on every push through GitHub Actions.

## Deployment
Deployed on Render. The build command is `pip install -r requirements.txt` and the start command is `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
