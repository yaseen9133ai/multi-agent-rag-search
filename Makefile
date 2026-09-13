# Local Docker workflow + observability + eval.
#
# All GCP commands live in docs/gcp_deployment.md so you can read what each one
# does before you run it. This Makefile wraps only the things you run on your
# laptop: the local docker-compose stack, local tracing, and the eval suite.
#
# The flow you probably want:
#   make up           — start the whole stack WITH tracing on (Phoenix UI at :6006)
#   make eval         — then score the running stack (fast deterministic suite)
#   make eval-judge   — LLM-judge suite (needs judge auth — see eval/README.md)
#   make gate / make gate-judge — the regression gates

# Auto-load .env (if present) so targets pick up secrets like GOOGLE_API_KEY without
# needing `set -a; source .env; set +a` first. `export` propagates them into recipe
# shells. Values with spaces/quotes/`#`/`$` would need quoting — keep .env to plain KEY=value.
ifneq (,$(wildcard .env))
include .env
export
endif

MCP_LOCAL ?= http://localhost:3000/mcp

# "visual" (page-image search, default) or "hybrid" (chunked-text dense+BM25 search). Defaulted
# here (not just in .env.example) so `make configure-service` never substitutes an empty value
# into service.yaml for users who haven't added RETRIEVAL_MODE to an existing .env.
RETRIEVAL_MODE ?= visual

# Client-side LLM rate limit for the eval suite. Cohere *trial* keys cap at 20 req/min and the
# evaluator fans out concurrent agent runs, so we pace calls below the cap to avoid 429s.
# On a paid/production key, raise it or set LLM_MAX_RPM=0 to disable (e.g. `make eval-judge LLM_MAX_RPM=0`).
LLM_MAX_RPM ?= 18

# Which judge dataset the LLM-judge suite scores. One set: report_qa.evalset.json — 4 happy-path
# cases scored by final_response_match_v2 ONLY by default, so the run is reliably green (per run,
# num_runs=1: 8 Gemini judge calls + ~12-20 Cohere agent calls). Groundedness (hallucinations_v1) is
# opt-in — it's flaky (returns NOT_EVALUATED on a hiccup = hard fail); see fixtures_judge/test_config.json.
# Override with JUDGE_DATASET=... to point at your own evalset.
JUDGE_DATASET ?= report_qa.evalset.json

# Eval runs on the HOST (not in the agent image). To dodge PEP 668 ("externally-managed-environment"),
# the "which pip?" interpreter mismatch, and wrong-venv surprises, eval uses a DEDICATED uv-managed
# virtualenv at ./.venv, and every eval target invokes that interpreter explicitly — so `make eval*`
# works no matter what you have activated. Override the location with VENV=... if you like.
VENV    ?= .venv
VENV_PY := $(VENV)/bin/python

help:
	@echo "Local docker-compose stack (tracing is ON by default → Phoenix UI at http://localhost:6006):"
	@echo "  make up           — start everything (agent + mcp + qdrant + postgres + Phoenix) and ingest"
	@echo "  make logs         — tail logs from the running stack"
	@echo "  make down         — stop ALL containers incl. Phoenix (volumes survive)"
	@echo "  make nuke         — stop everything + wipe Qdrant + Postgres volumes"
	@echo "  make ingest       — re-run the ingestion job into the local Qdrant"
	@echo "                      (RETRIEVAL_MODE=visual|hybrid in .env picks the retrieval strategy)"
	@echo "  make trace        — alias for 'make up' (kept for muscle memory); traces at http://localhost:6006"
	@echo ""
	@echo "Evaluation (start the stack with 'make up' first, and 'make eval-deps' once):"
	@echo "  make eval         — fast deterministic suite (free criteria)"
	@echo "  make eval-judge   — LLM-judge suite (needs judge auth — see eval/README.md)"
	@echo "  make gate         — baseline regression gate (deterministic)"
	@echo "  make gate-judge   — baseline regression gate incl. judge datasets"
	@echo "  make eval-deps    — create ./.venv (uv) and install host eval deps (./agent[eval])"
	@echo ""
	@echo "GCP deployment commands live in docs/gcp_deployment.md."
	@echo "  make configure-service — generate service.yaml from service.yaml.template + .env"

# ─── docker-compose lifecycle ────────────────────────────────────────────────
# `--wait` blocks until qdrant + postgres are healthy, the ingestion job has
# completed, and mcp + agent are running — so the stack is actually usable when
# the command returns.
up:
	docker compose up --build -d --wait
	@echo ""
	@echo "✅ Stack is ready (tracing ON). Open http://localhost:8080/dev-ui"
	@echo "   Ask a question, then watch the trace at http://localhost:6006 (Phoenix)."
	@echo "   • make eval — score the running system   • make down — stop the stack"

logs:
	docker compose logs -f

# `--remove-orphans` sweeps up any leftover containers (e.g. from an older profiled setup).
down:
	docker compose down --remove-orphans

nuke:
	docker compose down --volumes --remove-orphans

# Re-run the one-shot ingestion service against the running local Qdrant.
ingest:
	docker compose run --rm ingestion

# ─── Observability ───────────────────────────────────────────────────────────
# Tracing is ON by default now (see docker-compose.yml: the agent exports to the
# in-network Phoenix service at http://phoenix:6006/v1/traces, which starts with the
# stack). `trace` is kept as an alias for `up` so old muscle memory still works.
trace: up

# ─── Evaluation building blocks ──────────────────────────────────────────────
# Start the stack with `make up` first; these run on the host and reach the MCP sidecar at
# localhost:3000 (conftest skips cleanly if nothing is listening). Install once on the host into a
# dedicated uv venv (the agent image does NOT carry the eval extra).
# This never touches your system/global Python — no PEP 668, no activate step needed.
eval-deps:
	@command -v uv >/dev/null 2>&1 || { \
	  echo "❌ uv not found. Install it once:  curl -LsSf https://astral.sh/uv/install.sh | sh   (or: brew install uv)"; exit 1; }
	uv venv $(VENV)
	uv pip install --python $(VENV) "./agent[eval]"
	@echo "✅ Eval deps installed into $(VENV)/. 'make eval*' use it automatically."

# Fail fast with a clear hint instead of a cryptic "pytest: command not found".
_eval-deps-check:
	@{ test -x "$(VENV_PY)" && "$(VENV_PY)" -c "import pytest"; } 2>/dev/null || { \
	  echo "❌ Eval venv not ready at $(VENV)/. Run:  make eval-deps"; exit 1; }

eval: _eval-deps-check
	MCP_SERVER_URL=$(MCP_LOCAL) LLM_MAX_RPM=$(LLM_MAX_RPM) $(VENV_PY) -m pytest eval/test_fast.py -v

eval-judge: _eval-deps-check
	@test -n "$$GOOGLE_API_KEY" || (echo "Set GOOGLE_API_KEY first (the LLM judge runs on Gemini)"; exit 1)
	MCP_SERVER_URL=$(MCP_LOCAL) LLM_MAX_RPM=$(LLM_MAX_RPM) JUDGE_DATASET=$(JUDGE_DATASET) $(VENV_PY) -m pytest eval/test_judge.py -v

gate: _eval-deps-check
	MCP_SERVER_URL=$(MCP_LOCAL) LLM_MAX_RPM=$(LLM_MAX_RPM) $(VENV_PY) eval/compare_to_baseline.py

gate-judge: _eval-deps-check
	@test -n "$$GOOGLE_API_KEY" || (echo "Set GOOGLE_API_KEY first (judge datasets run on Gemini)"; exit 1)
	MCP_SERVER_URL=$(MCP_LOCAL) LLM_MAX_RPM=$(LLM_MAX_RPM) $(VENV_PY) eval/compare_to_baseline.py --judge

# ─── GCP service manifest ────────────────────────────────────────────────────
# Render service.yaml from the committed service.yaml.template + your .env. Idempotent — re-run
# anytime. Only the project-identity vars below are substituted; the observability block in the
# template is hardcoded prod config (NOT from .env). service.yaml is a generated artifact (gitignored).
configure-service:
	@command -v envsubst >/dev/null 2>&1 || { echo "❌ envsubst not found — install gettext (macOS: brew install gettext)"; exit 1; }
	@test -n "$$PROJECT_ID"       || { echo "❌ PROJECT_ID empty — fill it in .env (the Makefile auto-loads .env)"; exit 1; }
	@test -n "$$CLOUDSQL_INSTANCE" || { echo "❌ CLOUDSQL_INSTANCE not set in .env"; exit 1; }
	@test -n "$$DB_USER"          || { echo "❌ DB_USER not set in .env"; exit 1; }
	@test -n "$$QDRANT_COLLECTION" || { echo "❌ QDRANT_COLLECTION not set in .env"; exit 1; }
	@RETRIEVAL_MODE="$(RETRIEVAL_MODE)" envsubst '$$PROJECT_ID $$CLOUDSQL_INSTANCE $$DB_USER $$QDRANT_COLLECTION $$RETRIEVAL_MODE' \
	    < service.yaml.template > service.yaml
	@echo "✅ Generated service.yaml from service.yaml.template (project=$$PROJECT_ID, collection=$$QDRANT_COLLECTION, retrieval_mode=$(RETRIEVAL_MODE))"

.PHONY: help up logs down nuke ingest trace configure-service \
        eval-deps _eval-deps-check eval eval-judge gate gate-judge
