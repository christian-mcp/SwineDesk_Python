# SwineDesk Python

This is a Python program for SwineDesk, ported from the Node.js MVP to a self-contained tool-based architecture.

The MVP remains in this repository (`index.js`, `claude.js`, `broker.js`, etc.). The new Python implementation lives under `swinedesk/`.

## What it includes

- FastAPI webhook app with:
  - `GET /` health check
  - `POST /sms` Twilio SMS webhook
  - `POST /docs/health-cert` health certificate webhook
- Two separate role-specific agents (`producer` and `broker`), selected from backend role lookup
- One-session-per-phone in-memory session store (same model as current MVP)
- Local agent wiring with filesystem-discovered tools and per-role tool allowlists
- Tool stubs grouped by domain (`orders`, `clients`, `vaccines`, `barns`, `loads`, `notify`)
- Backend API client wrapper for future integration

## Architecture

```mermaid
flowchart TD
    smsWebhook["POST /sms"] --> resolveRole["resolve_phone_role(phone)"]
    resolveRole --> routeAgent["Route to producer_agent or broker_agent"]
    routeAgent --> runAgent["Selected pydantic-ai agent run()"]
    runAgent --> executeTool["execute_tool"]
    executeTool --> ordersTools["/tools/orders/*"]
    executeTool --> clientsTools["/tools/clients/*"]
    executeTool --> vaccinesTools["/tools/vaccines/*"]
    executeTool --> barnsTools["/tools/barns/*"]
    executeTool --> loadsTools["/tools/loads/*"]
    executeTool --> notifyTools["/tools/notify/*"]
    ordersTools --> backendApi["External backend API"]
    clientsTools --> backendApi
    vaccinesTools --> backendApi
    barnsTools --> backendApi
    loadsTools --> backendApi
    runAgent --> twiml["TwiML SMS response"]
```

## Project layout

- `pyproject.toml` - Python package/dependencies
- `.env.example` - environment variables template
- `swinedesk/app.py` - FastAPI app and endpoints
- `swinedesk/agent.py` - role-routed pydantic-ai setup and runner function
- `swinedesk/prompts.py` - producer and broker prompts
- `swinedesk/state.py` - minimal structured state model
- `swinedesk/session.py` - in-memory per-phone session management
- `swinedesk/backend_client.py` - external backend API wrapper
- `swinedesk/tools/...` - tool stubs by domain

## Session behavior (same as current MVP)

This skeleton intentionally keeps session semantics close to `conversation.js`:

- key: phone number
- one active session per phone
- inactivity timeout: 30 minutes (configurable)
- max history: 30 messages (configurable)
- cleanup loop: every 10 minutes
- store: in-memory only

Tradeoff: sessions are lost on process restart (same as current behavior).

## Tool conventions

Every tool is a `Tool` subclass with:

- `TOOL_PATH` (required for stable routing)
- `DESCRIPTION`
- `ARGUMENTS`
- `async run(arguments, state) -> dict`

All current tool files are stubs with `TODO` markers for backend integration.

### Adding a new tool

1. Create a new folder under `swinedesk/tools/<category>/<tool_name>/`.
2. Add `tool.py` with a `Tool` subclass.
3. Set `TOOL_PATH` to `/tools/<category>/<tool_name>`.
4. Add argument schema with `Arg(...)`.
5. Implement the backend API call in `run(...)`.

The loader discovers tools from filesystem path conventions, so no central registry edit is required.

The project includes local implementations for:
- `Tool` / `Arg` base abstractions
- filesystem tool discovery
- `execute_tool` dispatcher bridge for pydantic-ai

No `expert_agents` dependency is required.

## Role-based tool isolation

The runtime initializes two independent agents with different custom tool registries:

- Producer agent: sell/buy order creation, onboarding, vaccine order flow, barn listing/finding, load detail.
- Broker agent: operational tooling like listings/matches, load lists, freight assignment, client notes, and notifications.

This prevents the model from seeing or calling role-inappropriate custom tools in normal operation.

## Local setup

### 1) Install dependencies

```bash
cd /data/swinedesk
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2) Configure environment

```bash
cp .env.example .env
```

Update `.env` with:

- `ANTHROPIC_API_KEY`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_PHONE_NUMBER`
- `BACKEND_API_URL`
- `BACKEND_API_TOKEN`

### 3) Run server

```bash
uvicorn swinedesk.app:app --reload --port 3000
```

## Twilio wiring

- Point Twilio incoming message webhook to:
  - `https://<your-domain>/sms`
- Health cert automation webhook (Make/Zapier/email parser) to:
  - `https://<your-domain>/docs/health-cert`

## Implementation TODOs for next engineer

1. Finalize backend API contracts and endpoint paths in:
   - `swinedesk/backend_client.py`
   - all tool stub `run(...)` methods
2. Replace role lookup endpoint stub:
   - `BackendClient.resolve_phone_role(...)`
3. Add real Twilio send implementation in:
   - `swinedesk/tools/notify/send_sms/tool.py`
4. Add real SMTP send implementation in:
   - `swinedesk/tools/notify/send_email/tool.py`
5. Add tests:
   - session timeout/capping behavior
   - webhook happy paths
   - each tool stub contract and error handling
6. Add observability/logging and production error reporting.

## Notes on migration from current Node MVP

- Node completion tags (`<<<ACTION:...>>>`) were removed.
- In Python, tool-calling should drive create/update actions directly.
- Producer/Broker role routing remains, but now role is resolved via backend API instead of env phone list.
- SMS chunking behavior remains for long responses.

