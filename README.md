# PolicyPilot

A governed regulatory/policy Q&A copilot — the first of three portfolio projects
demonstrating agentic AI patterns on Databricks + Azure (Q&A, AI reviewer, generation).
PolicyPilot answers questions about SEC 10-K filings and cites the filing every claim
came from, refusing rather than guessing when it can't ground an answer.

> **Credit.** PolicyPilot was created by [yashprogrammer](https://github.com/yashprogrammer)
> ([original repository](https://github.com/yashprogrammer/azure-databricks-agentic-qa)):
> the 10-K agent, the Databricks + Azure deployment, the Unity Gateway setup and the eval
> harness are their work. This fork adds **College mode** (below): the same governed Q&A
> pattern over a college's policies, joined to the student's live record in
> [College ERP](https://github.com/sanket801036/College-ERP-master).

## Status

**Milestone 1 done: local prototype.** Real SEC EDGAR filings, a local Chroma vector
store, Llama (via Groq) as the LLM, and a Streamlit UI — all runnable on a laptop with
no cloud account.

**Milestone 2 done: deployed and verified on real Azure Databricks.** Workspace, Unity
Catalog, Key Vault, GitHub OIDC federation, and a live Databricks App were all stood up
and end-to-end tested — a real deployed question got a grounded, cited answer straight
from Apple's actual 10-K, served via a Databricks-hosted Vector Search index. The
Vector Search endpoint and app compute were torn down afterward to stop billing (they
bill by the hour; everything else — workspace, UC catalog/table, Key Vault, OIDC setup —
stays up at near-zero cost). See **[docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md)**
for the full step-by-step (including every error hit and how to avoid it) to redo this
end to end.

**Milestone 3 in progress: guardrails, broader data, CI-gated eval** (see
[docs/UPGRADE_PLAN.md](docs/UPGRADE_PLAN.md)). Code-complete: ~20-ticker ingestion, LLM-based
ticker/company detection in `plan_node`, a Databricks Agent Evaluation harness gated in
`ci.yml`, and Unity Gateway in front of Groq (`resources/ai_gateway.yml`: a model provider
service + model service, deployed by the bundle along with their grants). Only the
guardrails are a manual one-time UI step — see [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md)
Parts 6–7.

The code is structured so that swapping backends is a config change, not a rewrite (see
[Architecture](#architecture) below).

## Architecture

| Layer | Local | Databricks/Azure |
|---|---|---|
| Vector store | `retrieval/local_chroma.py` (Chroma + sentence-transformers) | `retrieval/databricks_vector_search.py` — Vector Search over `policypilot_dev.filings.chunks` (implemented, endpoint/index not created yet) |
| LLM | `agent/llm.py` `GroqLLMClient`, key from `.env` — guardrail-free by design (dev sandbox) | `DatabricksGatewayLLMClient` calling the `<catalog>.filings.gpt-oss` Unity Gateway model service (`resources/ai_gateway.yml`), which routes to Groq through a provider service holding the key, with guardrails — no raw Groq key in the App's env anymore, see [docs/UPGRADE_PLAN.md](docs/UPGRADE_PLAN.md) |
| Structured lookup | `ingestion/manifest.py` (local JSON) | UC Function over a Delta table (not built yet) |
| UI | `streamlit run` locally | Databricks App (`resources/apps.yml` + `app.yaml`, deploys the whole repo since the app imports the full `policypilot` package) |
| Ingestion | `python -m policypilot.ingestion.pipeline` | `notebooks/seed_chunks_table.py` (manual, self-contained) |

Switch backends via `PP_ENV` (`local` or `databricks`). The agent itself
(`agent/graph.py`, a LangGraph plan → retrieve → answer → verify loop) never changes —
it only talks to the `VectorStore` and `LLMClient` protocols in `retrieval/base.py` and
`agent/llm.py`.

### Deferred: not Mosaic AI yet

Databricks' "Mosaic AI" branding covers Agent Framework (log an agent to Unity Catalog
via MLflow, deploy behind Model Serving), Agent Evaluation, and Agent Bricks. This
project uses Agent Evaluation (CI-gated, see below) and Unity AI Gateway (governance for
the Groq call) but not the rest — the agent itself still runs as plain LangGraph, not
logged to UC or served behind Model Serving. Full Foundation Model migration (swapping
Groq for a Databricks-native model) was deliberately reversed — see
[docs/UPGRADE_PLAN.md](docs/UPGRADE_PLAN.md) item 3 — since a Unity Gateway model service in
front of Groq provides the same guardrails without a provider migration.

The agent is deliberately not a bare RAG chain: `verify_node` is a hard citation gate —
an answer with no `[n]` citation back to retrieved context is replaced with a refusal
before it reaches the user.

## Local setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv sync --extra dev
cp .env.example .env
# edit .env and set GROQ_API_KEY
```

### 1. Ingest filings

Pulls the latest 10-K for ~20 large-cap tickers (`DEFAULT_TICKERS` in `config.py`) from
SEC EDGAR (public, no auth), chunks them, embeds them locally, and stores them in
`data/chroma/`.

```bash
uv run python -m policypilot.ingestion.pipeline
```

### 2. Run the chat UI

```bash
uv run streamlit run src/policypilot/app/streamlit_app.py
```

### 3. Run the eval harness

Runs the golden question set (~47 questions across all ingested tickers, plus one
deliberately off-corpus question to check the refusal gate) through the agent via
Databricks Agent Evaluation (`mlflow.genai.evaluate`, `mlflow[databricks]>=3.1`). The
hosted judges (safety, relevance, retrieval groundedness) need Databricks workspace auth;
the citation-presence check runs locally either way.

```bash
uv sync --extra dev --extra eval
uv run python -m policypilot.eval.agent_eval
```

### Tests

```bash
uv run pytest
uv run ruff check .
```

## College mode

The same pattern - plan, retrieve, answer, verify, refuse rather than guess - pointed at a
college instead of SEC filings, with two things the 10-K agent does not need:

- **The student's own record as a tool.** When a question is about the student ("can I
  sit the DBMS exam?") rather than the rules ("what is the attendance rule?"), the plan
  step routes through College ERP's REST API for their per-course attendance. The ERP
  already works out the classes needed to get back to 75%, so those numbers go into the
  context as their own numbered source, and an answer has to cite both the rule and the
  record it applied it to. The model is never asked to do the arithmetic.
- **A way out of a refusal.** A refused answer offers to send the question to the
  student's teachers through the ERP's `/api/v1/doubts/` endpoint, together with what
  the assistant already said, so a teacher can correct it instead of repeating it. It
  lands in the same queue as questions from the ERP's own assistant page.

The verify step is also stricter here: every `[n]` must point at an item that was in the
context, so a citation to a source that was never given counts as no citation.

```bash
uv run python -m policypilot.college.ingest      # index data/college_policies/*.md
uv run streamlit run src/policypilot/app/college_app.py
```

Sign in from the sidebar with an ERP student account - the public demo's is
`teststud` / `testpass123`, test data only, also printed on the ERP's sign-in page.
`ERP_BASE_URL` points at a different ERP instance. The policies in
`data/college_policies/` are a sample handbook written to match the rules the ERP
enforces, not a real institution's.

Local only so far: on Databricks the policies would get their own UC table and
Delta-Sync index exactly as the filings do.

## Next steps

Done: Azure Databricks workspace (Central India, Premium, Serverless), Unity Catalog
(`policypilot_dev.filings.chunks`), Key Vault + AKV-backed secret scope holding the
Groq key. `databricks.yml`'s `dev` target already points at the real workspace host.

Remaining, in order — the last three are deliberately batched into one short session
since Vector Search/Model Serving endpoints cost money while running:

1. **GitHub OIDC federation**: an Entra ID app registration + federated credential
   trusting this repo + a role assignment on `policypilot-rg`, then
   `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID` / `DATABRICKS_HOST`
   as GitHub environment secrets. No ongoing cost.
2. **Seed real data**: run `notebooks/seed_chunks_table.py` in the workspace to populate
   `policypilot_dev.filings.chunks`.
3. **Create a Vector Search endpoint** and a Delta-Sync index over
   `policypilot_dev.filings.chunks` (self-managed embeddings, `embedding` column) —
   `DatabricksVectorSearchStore` is already implemented against it.
4. **Deploy**: trigger `.github/workflows/cd.yml` (GitHub Actions "Run workflow" button
   — runs `databricks bundle deploy` on GitHub's runner, no local CLI needed) to push
   the Databricks App.
5. **Test end-to-end**, then delete the Vector Search endpoint and stop the App to stop
   the meter.

## Repo layout

```
src/policypilot/
├── config.py               # env-driven settings, local vs. databricks backend selection
├── ingestion/               # SEC EDGAR fetch -> chunk -> embed -> store
├── retrieval/                # VectorStore protocol + local/Databricks implementations
├── agent/                    # LLM client protocol, tools, LangGraph agent
├── college/                  # College mode: policy loader, ERP client, agent, ingest
├── eval/                     # golden dataset + Databricks Agent Evaluation harness
└── app/                       # Streamlit chat UIs: 10-K (Databricks App) and college
data/college_policies/         # Sample college handbook for College mode
notebooks/seed_chunks_table.py # Self-contained: fetch+chunk+embed+write into UC, run in-workspace
scripts/setup_unity_catalog.py # UC provisioning script (dev catalog was created by hand instead)
resources/, databricks.yml     # Databricks Asset Bundle (deploy config)
app.yaml, requirements.txt     # Databricks App runtime config (repo root, since app.yml deploys the whole repo)
.github/workflows/              # CI (active) + CD (manual-dispatch, until GitHub OIDC is wired up)
```
