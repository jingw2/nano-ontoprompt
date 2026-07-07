# nano-ontoprompt

**[中文文档](./README_zh.md)**

A lightweight, Palantir Foundry-inspired platform for building domain ontologies from raw data. Connect your data sources, run them through a visual transform pipeline, map curated datasets to entity types, and explore the resulting knowledge graph — complete with entities, relations, logic rules, and executable actions.

Two build paths are supported:

- **Pipeline Mapping** (v2) — full data-integration chain: `Data Connection → Raw Storage → Transform → Curated Dataset → Ontology Mapping`
- **Simple LLM Extraction** (v1) — upload documents, pick a prompt and model, and extract a knowledge graph in one shot

---

## What is an Ontology?

An ontology is a formal representation of knowledge in a specific domain — a shared vocabulary of concepts and the relationships between them. Think of it as the structured backbone that turns raw data into machine-readable, queryable knowledge.

In nano-ontoprompt, every ontology is made of these building blocks:

| Building Block | What it captures | Example |
|---|---|---|
| **Entity (Object Type)** | A key concept mapped from a curated dataset, one node per data row | `Supplier`, `PurchaseOrder` |
| **Relation (Link Type)** | An edge between entities, inferred from foreign keys and cross-dataset value overlap | `PurchaseOrder -[HAS_SUPPLIER]-> Supplier` |
| **Logic Rule** | The rule layer: mapping / validation / state / inference / automation rules discovered from schema, quality reports and relations | `amount > 0`, state machine on `库存状态` |
| **Action** | The executable behavior layer: CRUD, state-transition and link actions generated from object types and relations, with submission criteria and audit snapshots | `Approve Record`, `Link Order to Supplier` |

**Typical use cases:** supply chain modeling, clinical concept extraction, financial compliance, legal document structuring — any domain where you need to turn heterogeneous data into structured knowledge.

---

## Features

### Pipelines (v2)
- **Visual pipeline builder** — connector / storage / transform / output nodes on a canvas, with per-node status and data preview
- **Three transform routes** — A: structured (CSV/Excel, schema inference + cleansing), B: semi-structured (JSON flatten / XML parse), C: unstructured (document → Markdown → LLM or rule-based structured extraction)
- **Connectors** — file upload, MySQL/PostgreSQL, MongoDB, REST API (with incremental sync)
- **Curated datasets** — quality scoring, human review (admin approval), versioning

### Ontology (v2)
- **Auto mapping engine** — dataset → entity type, column → property, FK → link type, with cardinality inference
- **Cross-dataset link inference** — exact FK matching, value normalization (`SUP-001` ↔ `SUP001`), alternate-key matching (e.g. document mentions of company names linking to Supplier entities), optional LLM-assisted semantic linking (`ENABLE_LLM_FK_DETECTION=1`)
- **Logic & Action discovery** — rules and actions are discovered from mappings, schema constraints, state fields and relations, then go through draft → review → publish
- **Knowledge graph** — interactive Cytoscape.js mesh view with isolated-node toggle; Neo4j-backed when available, SQLite fallback otherwise
- **Search** — keyword search (SQL fallback when ChromaDB is down) and semantic search (ChromaDB)

### Quality Audit (ReAct Agent)
- **LLM-driven multi-step review** — an AI agent systematically checks ontology quality: isolated entities, broken references, missing relations, low-coverage entity types
- **Tool-calling architecture** — 8 built-in inspection tools (summary, coverage, ref-check, pattern inference) that the agent can chain together
- **Findings report** — severity-classified issues with actionable fix suggestions, persisted as audit tasks

### Platform
- **LLM extraction** — any OpenAI, Anthropic, or OpenAI-compatible model; defense-in-depth against fuzzy relation types
- **LiteLLM proxy** — optional LiteLLM integration for unified API-key management and cost tracking across multiple LLM providers
- **Prompt management** — versioned domain prompts with one-click template generation
- **Data management** — structured data browser with curated dataset detail panel, row-level editing, and review workflow
- **Export** — JSON, YAML, CSV, Turtle (RDF), HTML
- **Graceful degradation** — Neo4j / MinIO / ChromaDB / Redis are all optional; the system falls back to SQLite + local file storage + synchronous runs
- **Multi-language UI** — English / Chinese toggle
- **User management** — JWT auth, admin/editor roles; curated approval is admin-only

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, Cytoscape.js |
| Backend | FastAPI, SQLAlchemy, Alembic |
| Metadata DB | SQLite (dev) / PostgreSQL (prod) |
| Object storage | MinIO (optional, local-file fallback) |
| Graph DB | Neo4j (optional, SQLite fallback) |
| Vector DB | ChromaDB (optional) |
| Task queue | Celery + Redis (optional, synchronous fallback) |
| LLM clients | OpenAI SDK, Anthropic SDK |
| LLM proxy | LiteLLM (optional) |

---

## Architecture Guide

For a deep dive into the Ontology-as-a-Service architecture — including Object/Link/Function/Governance design patterns, multi-tenant isolation, clinical screening workflows, and production deployment checklists — see **[ONTOLOGY.md](./ONTOLOGY.md)** (2727 lines, in Chinese).

---

## Quick Start

### Option 1 — Docker Compose (full v2 stack)

```bash
git clone https://github.com/jingw2/nano-ontoprompt.git
cd nano-ontoprompt
cp .env.example .env          # edit secrets before production use
docker compose -f docker-compose.v2.yml up --build
```

This starts PostgreSQL, Redis, Neo4j, MinIO, ChromaDB, backend and frontend. For the lightweight v1 stack use `docker-compose.yml` instead.

Open [http://localhost:5173](http://localhost:5173). Default credentials: `admin / admin123`.

### Option 2 — Manual setup (minimal, no external services)

**Prerequisites:** Python 3.11+, Node.js 18+

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head                                  # or rely on auto create_all in dev
uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Neo4j / MinIO / ChromaDB / Redis are optional — without them the app uses SQLite graph fallback, local file storage and synchronous pipeline runs.

---

## Usage (Pipeline Mapping path)

1. **Add a model** — *Models → Add Model*: provider, API key, base URL. Tag usage (extraction / VLM / FK detection).
2. **Create a pipeline** — *Pipelines → New*: drop connector / storage / transform / output nodes on the canvas, attach your data file, pick a transform route, then **Run**.
3. **Review curated data** — *Pipelines → Curated*: inspect quality score and preview, then approve (admin).
4. **Create an ontology** — *Ontologies → New*, build mode **Pipeline Mapping**: select approved curated datasets and map each to an entity type with a primary key.
5. **Build** — relations are inferred across datasets automatically; logic rules and actions are discovered as drafts.
6. **Explore** — *Graph* tab for the mesh view, *Entities / Logic / Actions* tabs for details and review, then publish logic/actions.
7. **Export** — JSON, YAML, CSV, Turtle (RDF), or HTML from the *Info* tab.

For the **Simple LLM Extraction** path: create an ontology in `simple_llm` mode, upload documents in the *Files* tab, pick a prompt + model, and run extraction.

---

## Project Structure

```
nano-ontoprompt/
├── backend/
│   ├── alembic/               # DB migrations (0001_full_baseline covers all tables)
│   ├── app/
│   │   ├── routers/           # v1 + v2 REST API endpoints
│   │   ├── models/            # SQLAlchemy ORM models (v1 + v2)
│   │   ├── services/
│   │   │   ├── connection/    # File / SQL / Mongo / REST connectors
│   │   │   └── v2/
│   │   │       ├── pipeline/  # Transform engine, routes A/B/C, steps
│   │   │       ├── mapping/   # Auto mapper, FK & alternate-key link inference
│   │   │       ├── graph/     # Neo4j service, Cypher validation, analytics
│   │   │       ├── curated/   # Quality scoring, review workflow
│   │   │       └── vector/    # ChromaDB service
│   │   └── tasks/             # Celery tasks (pipeline run, sync, extraction)
│   ├── scripts/               # Maintenance scripts (orphan data cleanup, migration)
│   └── tests/                 # 300+ pytest cases
├── frontend/
│   ├── scripts/                # One-off debug / demo / test scripts
│   └── src/
│       ├── pages/pipelines/    # Pipeline list + canvas builder
│       ├── pages/ontologies/   # Ontology detail: graph / entities / logic / actions / audit
│       ├── pages/data-management/  # Structured data browser + curated detail panel
│       └── api/                # Axios clients (v1 + v2)
├── scripts/
│   └── data/                   # Data import & entity-linking scripts (SNOMED, supply chain)
├── docker-compose.yml          # v1 lightweight stack
├── docker-compose.v2.yml       # Full stack: Postgres + Redis + Neo4j + MinIO + Chroma + LiteLLM
├── litellm_config.yaml         # LiteLLM proxy configuration
├── ONTOLOGY.md                 # Comprehensive architecture guide
└── test_data/                  # Sample datasets and E2E acceptance scripts
```

---

## Environment Variables

See `.env.example` for the full list. Key settings:

```env
ENVIRONMENT=development        # "production" enforces non-default secrets at startup
DATABASE_URL=sqlite:///./ontoprompt.db
SECRET_KEY=change-me
ENCRYPTION_KEY=                # Fernet key for encrypting stored API keys
FIRST_ADMIN_USER=admin
FIRST_ADMIN_PASSWORD=admin123

# Optional services (graceful fallback when absent)
REDIS_URL=redis://localhost:6379/0
NEO4J_URI=bolt://localhost:7687
MINIO_ENDPOINT=localhost:9000
CHROMA_HOST=localhost

# Upload limits
MAX_UPLOAD_MB=200
ALLOWED_UPLOAD_EXTENSIONS=csv,xlsx,xls,json,xml,pdf,docx,doc,pptx,ppt,md,txt

# Optional: LLM-assisted semantic FK detection (needs a configured model)
ENABLE_LLM_FK_DETECTION=0
```

---

## Troubleshooting

**Login fails with `AggregateError [ECONNREFUSED]` in the frontend container.**
Pull the latest code — the Vite proxy now targets `http://backend:8000` inside Docker via `VITE_API_PROXY_TARGET`. Then rebuild: `docker compose up -d --build frontend`.

**Cannot login with `admin / admin123` on an existing deployment.**
The admin user was seeded with the old default password. Reset it:

```bash
# Docker
docker compose exec backend python scripts/reset_admin_password.py

# Manual setup
cd backend && python scripts/reset_admin_password.py
```

Options: `--user <username>` (default `admin`), `--password <new_pwd>` (default `admin123`).

**LLM extraction OOM-killed (macOS / low-memory environments).**
Parallel extraction with multiple LLM calls can exhaust memory on machines with limited RAM. The code now defaults to serial extraction (`max_workers=1`). If you still hit issues, extract one domain at a time, or reduce the number of uploaded files per ontology.

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=jingw2/nano-ontoprompt&type=Date)](https://star-history.com/#jingw2/nano-ontoprompt&Date)

---

## License

MIT
