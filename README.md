# Swibit Catalog API

[![CI](https://github.com/digitalagentk/swibit-catalog-api/actions/workflows/ci.yml/badge.svg)](https://github.com/digitalagentk/swibit-catalog-api/actions/workflows/ci.yml)

A **Personal Catalog API**: every user owns *lists*, every list contains *items*,
and any list can be exported to a CSV file by a background job.

**Domain flavour (fixed shape of the task, my naming):** a *List* is a **project
board** and an *Item* is a **task**. The database keeps the `goals` / `tasks`
table names from `DESIGN.md`; the mapping is documented there and in
`DESIGN.md`.

Built with **Python 3.11 · FastAPI · PostgreSQL · SQLAlchemy · Alembic · Docker
Compose**, with JWT authentication and bcrypt password hashing.

---

## Quick start

Prerequisites: Docker Desktop (with Compose v2) running. Nothing else is
needed - Python, dependencies and the database all come from the containers.

```bash
git clone https://github.com/digitalagentk/swibit-catalog-api.git
cd Swibit_Catalog
cp .env.example .env          # optional: edit to change ports/secrets
docker compose up --build
```

That single command builds the API image, starts PostgreSQL 16, **applies all
Alembic migrations automatically**, and starts the API.

Verify it is alive:

```bash
curl http://localhost:8000/health
# {"status":"ok","database":"up"}

open http://localhost:8000/docs      # interactive OpenAPI / Swagger UI
```

Shut down (add `-v` to also delete the database and generated exports):

```bash
docker compose down
docker compose down -v
```

| Service | URL | Notes |
|---|---|---|
| API | http://localhost:8000 | interactive docs at `/docs` |
| PostgreSQL | `localhost:5433` → container `5432` | user/db `swibit` / `swibit_catalog` |

> **Why port 5433?** A native PostgreSQL on the host often already owns 5432.
> The container still listens on 5432 internally; only the published host port
> is 5433. Override with `POSTGRES_PORT` in `.env`.

---

## Environment variables

All configuration comes from the environment; no secret is committed. See
`.env.example`.

| Variable | Default (dev) | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://swibit:swibit@db:5432/swibit_catalog` | SQLAlchemy connection string. Compose builds this for you from the `POSTGRES_*` values. |
| `POSTGRES_USER` | `swibit` | PostgreSQL role. |
| `POSTGRES_PASSWORD` | `swibit` | PostgreSQL password. |
| `POSTGRES_DB` | `swibit_catalog` | Database name. |
| `POSTGRES_PORT` | `5433` | **Host** port published for the database. |
| `API_HOST` / `API_PORT` | `0.0.0.0` / `8000` | API bind address. |
| `API_PORT` | `8000` | **Host** port published for the API. |
| `JWT_SECRET` | `dev-only-insecure-secret-change-me-in-production` | HS256 signing key. **Change this in production.** |
| `JWT_EXPIRE_MINUTES` | `60` | Access-token lifetime. |
| `EXPORT_DIR` | `./exports` (`/app/exports` in compose) | Where generated CSVs are written. |
| `TEST_DATABASE_URL` | `postgresql+psycopg://swibit:swibit@localhost:5433/swibit_catalog_test` | Database used by pytest; auto-created if missing. |

Generate a real secret with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## Running the tests

The suite runs against a **separate** database (`swibit_catalog_test`), so it
can never touch your development data. It is created automatically if it does
not exist.

```bash
# Option A - from inside the container (no local Python needed)
docker compose exec api pytest

# Option B - from your machine
docker compose up -d db
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest
```

Expected result: **82 passed**. The modules map 1:1 onto the required
behaviours - see `TESTING.md`.

Static checks run the same way the CI workflow runs them:

```bash
.venv/bin/ruff check .    # lint   (exits 0, no findings)
.venv/bin/python -m compileall -q Main Test
.venv/bin/alembic check   # models and migrations agree
```

---

## API surface

All protected routes need `Authorization: Bearer <token>`.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/` | no | Service banner |
| `GET` | `/health` | no | Liveness + database connectivity (503 if the DB is down) |
| `POST` | `/auth/register` | no | Create an account → `201` |
| `POST` | `/auth/login` | no | Exchange credentials for a token |
| `GET` | `/auth/me` | yes | The authenticated user |
| `GET` | `/goals` | yes | Paginated lists |
| `POST` | `/goals` | yes | Create a list → `201` |
| `GET` | `/goals/{goal_id}` | yes | Read one list |
| `PATCH` | `/goals/{goal_id}` | yes | Rename a list |
| `DELETE` | `/goals/{goal_id}` | yes | Delete a list (cascades to items) → `204` |
| `GET` | `/goals/{goal_id}/tasks` | yes | Paginated items in a list |
| `POST` | `/goals/{goal_id}/tasks` | yes | Create an item → `201` |
| `GET` | `/tasks/{task_id}` | yes | Read one item |
| `PATCH` | `/tasks/{task_id}` | yes | Update an item's title and/or status |
| `DELETE` | `/tasks/{task_id}` | yes | Delete an item → `204` |
| `POST` | `/goals/{goal_id}/exports` | yes | Queue a list export → `202` + job |
| `GET` | `/exports` | yes | Paginated export history (own jobs only) |
| `GET` | `/exports/{job_id}` | yes | Poll a job's status |
| `GET` | `/exports/{job_id}/download` | yes | Download the CSV (only once `completed`) |
| `POST` | `/exports/{job_id}/retry` | yes | Re-run a `failed` job → `202` |

Collection endpoints accept `?limit=` (1-100, default 20) and `?offset=`
(default 0) and return:

```json
{ "items": [ ... ], "total": 42, "limit": 20, "offset": 0 }
```

`tasks.status` is one of `todo`, `in_progress`, `done`.
`export_jobs.status` is one of `pending`, `running`, `completed`, `failed`.

---

## Example requests

```bash
API=http://localhost:8000
```

**1. Register an account**

```bash
curl -s -X POST $API/auth/register -H 'Content-Type: application/json' \
  -d '{"user_name":"Alice","email":"alice@example.com","password":"Sup3rSecret!"}'
```
```json
{"user_id":1,"user_name":"Alice","email":"alice@example.com",
 "user_privilege":"user","created_at":"2026-09-30T15:08:09.130442Z"}
```

**2. Log in and keep the token**

```bash
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"alice@example.com","password":"Sup3rSecret!"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

AUTH="Authorization: Bearer $TOKEN"
```

**3. Create a list (a project board) and add items (tasks)**

```bash
curl -s -X POST $API/goals -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"goal_name":"Ship Swibit Catalog"}'

curl -s -X POST $API/goals/1/tasks -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"task_name":"Write the TDD tests","status":"in_progress"}'

curl -s -X POST $API/goals/1/tasks -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"task_name":"Fill in TESTING.md","status":"todo"}'
```

**4. Read a page of a list's items**

```bash
curl -s "$API/goals/1/tasks?limit=10&offset=0" -H "$AUTH"
```
```json
{"items":[{"task_id":1,"task_name":"Write the TDD tests","status":"in_progress",
           "goal_id":1,"created_at":"..."}],
 "total":2,"limit":10,"offset":0}
```

**5. Export the list in the background**

```bash
# Returns 202 immediately with a `pending` job.
curl -s -X POST $API/goals/1/exports -H "$AUTH"
```
```json
{"job_id":1,"goal_id":1,"user_id":1,"status":"pending",
 "file_path":null,"error":null,"created_at":"...","updated_at":"..."}
```

```bash
# Poll, then download.
curl -s $API/exports/1 -H "$AUTH"
# {"job_id":1,...,"status":"completed","file_path":"/app/exports/goal_1_job_1.csv",...}

curl -s $API/exports/1/download -H "$AUTH"
```
```csv
goal_id,goal_name,task_id,task_name,status
1,Ship Swibit Catalog,1,Write the TDD tests,in_progress
1,Ship Swibit Catalog,2,Fill in TESTING.md,todo
```

**6. Cross-user isolation is enforced**

```bash
# As Bob, who never created list 1:
curl -s $API/goals/1 -H "Authorization: Bearer $BOB_TOKEN"
```
```json
{"error":{"code":"forbidden","message":"That list belongs to another user."}}
```

---

## Error format

Every failure - validation, auth, missing resource, conflict, unexpected - uses
one envelope, so a client needs a single parser:

```json
{"error": {"code": "<machine readable>", "message": "<human readable>"}}
```

Input-validation failures add per-field `details`:

```json
{"error":{"code":"invalid_input","message":"Request validation failed.",
          "details":[{"field":"body.goal_name",
                      "message":"String should have at least 1 character"}]}}
```

| Status | `code` | When |
|---|---|---|
| 401 | `unauthenticated` | Missing, malformed, expired, or wrongly-signed token |
| 403 | `forbidden` | Authenticated, but the resource belongs to someone else |
| 404 | `not_found` | The id does not exist (or the generated file is missing) |
| 409 | `conflict` / `email_already_registered` / `invalid_state_transition` / `export_not_ready` | Uniqueness or state constraint violated |
| 422 | `invalid_input` | Request failed schema or business validation |
| 503 | `http_error` | `/health` only: the database is unreachable |

---

## Project layout

```
Swibit_Catalog/
├── Main/
│   ├── Entry.py             # app wiring, lifespan, health, server bootstrap
│   ├── Authentication.py    # register / login / JWT / get_current_user
│   ├── Data_Exposure.py     # list (goal) + item (task) CRUD, pagination
│   ├── Export_Manager.py    # export job creation, background worker, download
│   ├── Database_Manager.py  # engine, session, ORM models, DB readiness
│   └── Errors.py            # one error envelope + FastAPI exception handlers
├── Test/                    # pytest suite (see TESTING.md)
│   ├── conftest.py          # isolated test database + fixtures
│   └── *.py                 # one module per required behaviour
├── alembic/                 # migration environment + versions
├── Dockerfile               # single-stage image, runs migrations on start
├── docker-compose.yml       # api + db + volumes
├── entrypoint.sh            # alembic upgrade head, then exec the app
├── requirements.txt
├── .env.example
├── DESIGN.md                # architecture, data model, trade-offs
└── TESTING.md               # manual cases + observed outputs
```

Reasoning for the layout is in `DESIGN.md` § Project structure.

---

## Known limitations

1. **In-process export worker.** Exports run through FastAPI `BackgroundTasks`
   inside the API process. If the process dies mid-export the job stays
   `running` forever, and pending jobs are lost on restart (no broker, no
   persisted queue). Production would use Celery/RQ + Redis with a durable
   queue plus a sweeper that requeues stuck jobs.
2. **Exports are files on one volume.** CSV output is written to `EXPORT_DIR`
   (a named Docker volume) - single-instance only. A multi-instance
   deployment should write to object storage.
3. **Tokens cannot be revoked.** JWTs are stateless, so a leaked token stays
   valid until it expires. There is no refresh rotation or logout denylist.
4. **No rate limiting** on `/auth/login` and `/auth/register`.
5. **Strict email validation.** `EmailStr` rejects special-use domains such as
   `user@localhost` and `user@foo.test`. Deliberate, but stricter than some
   deployments need.
6. **403 rather than 404 for cross-user access.** A 403 confirms the id
   exists; 404 would prevent enumeration. I chose 403 because the requirement
   names this case "Forbidden access" - see `DESIGN.md` § Trade-offs.
7. **Offset pagination.** `limit`/`offset` is fine at this scale but degrades
   on very large tables and is not stable across inserts; keyset/cursor
   pagination would be better for large listings.
8. **Migrations run on container start.** Convenient for one container, but a
   rolling deployment would run them as a separate release step.
9. **No TLS in the dev stack** - terminated upstream in production.
10. **Tests are integration-style**, exercising the real app against a real
    PostgreSQL. There are no isolated unit tests of individual functions.

## Bonus

### CI/CD - GitHub Actions

`.github/workflows/ci.yml` runs on every push and pull request to `main`:

| Step | Catches |
|---|---|
| `ruff check .` | unused imports, undefined names, import order, bug-prone constructs |
| `python -m compileall -q Main Test` | syntax errors |
| `alembic upgrade head` | a migration that does not apply to a clean database |
| `alembic check` | a model edited **without** a matching migration |
| `pytest` | all 82 tests, against a real PostgreSQL 16 service container |

Design points:

- **A real database, not a mock or SQLite.** The service container is
  `postgres:16-alpine` with a `pg_isready` healthcheck, so the suite exercises
  the same engine, cascades, and CHECK constraints as production.
- **`alembic check` is the step I care most about.** It autogenerates against
  the live schema and fails if anything differs, so a model change cannot reach
  `main` without a migration to go with it.
- **Pinned tooling.** `requirements.txt` pins `ruff` alongside the test deps, so
  CI, the container, and a local checkout all lint identically.
- **Superseded runs are cancelled** via `concurrency`, so a rapid series of
  pushes does not queue up runners.
- The workflow requests only `contents: read`; it has no write scopes and no
  secrets beyond a throwaway `JWT_SECRET` scoped to the job.

Ruff is configured in `pyproject.toml` rather than run with defaults, with the
two deliberate exclusions documented there: FastAPI's `Depends()`/`Query()`
defaults (`B008`, the framework's idiom) and Alembic's generated migration
files, which are not hand-edited.

## AI Tools Used

- **Cline** (an AI coding agent running inside VS Code) was used to write most
  of the Python, the Docker/Compose setup, the Alembic migration and the pytest
  suite, working from the task description. The design decisions - domain
  flavour, keeping the `goals`/`tasks` table names, JWT + bcrypt, and the
  in-process export worker - were mine, and are justified in `DESIGN.md`.
- No other AI tools were used for this submission.

