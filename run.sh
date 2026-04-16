#!/bin/bash
# run.sh — Drone demo: Claude builds a slope64 Q&A chatbot, GPT-5.4-mini peer-reviews

set -euo pipefail

DRONE_URL="${DRONE_URL:-http://127.0.0.1:3010}"
DEMO_DIR="$(cd "$(dirname "$0")" && pwd)"

source ~/.secrets.env 2>/dev/null

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "ERROR: OPENAI_API_KEY not set in ~/.secrets.env"
  exit 1
fi

echo "Submitting slope64 Q&A chatbot demo to Drone..."
echo ""

WORKSPACE_DIR="${DRONE_WORKSPACE:-$HOME/projects/drone/workspace}"
mkdir -p "$WORKSPACE_DIR"

docker exec -i drone-agent tee /workspace/.demo-secrets > /dev/null <<SECRETS
OPENAI_API_KEY=$OPENAI_API_KEY
OPENAI_MODEL=gpt-4o-mini
SECRETS

docker exec drone-agent chmod 600 /workspace/.demo-secrets
echo "Secrets written to /workspace/.demo-secrets"

# Copy slope64 manual into workspace
docker exec drone-agent mkdir -p /workspace/reference
docker cp "$DEMO_DIR/reference/slope64_manual.txt" drone-agent:/workspace/reference/
echo "slope64 manual copied to /workspace/reference/"
echo ""

INSTRUCTIONS=$(cat <<'INST'
You are Claude Sonnet orchestrating an autonomous slope64 Q&A chatbot demo. You will:
1. Generate Python code directly (Phase 1)
2. Orchestrate container builds and runtime (Phases 0-3)
3. Use Claude API to generate code, GPT-5.4-mini for chat and peer review

Execute these three phases in order.

## HEARTBEAT — every turn

At the start of every turn, write `/workspace/<taskId>/STATUS.md` with:
```
Phase: <current phase name>
Turn: <approximate turn number>
Last: <one-line summary of last action>
Next: <one-line summary of next action>
Blockers: <none | description>
```

---

## PHASE 0 — Prepare

No database setup needed. Verify the slope64 manual is accessible:

```bash
test -f /workspace/reference/slope64_manual.txt && echo "Manual: OK" || echo "ERROR: Manual missing"
```

---

## PHASE 1 — Build slope64 Q&A Chatbot

Build a FastAPI server that answers questions about slope64 finite element analysis using the embedded manual.

Create `slope64-chatbot/` in your workspace with these 5 files:

**guardrails.py** — Deterministic prompt injection guard:

```python
import re

# Credential/code exfiltration
BLOCK_PATTERNS = [
    r'\bapi[\s_-]?key\b', r'\bsecret\b', r'\bpassword\b', r'\btoken\b',
    r'\bsk-[a-z0-9]', r'\bshow.*code\b', r'\bgive.*source\b',
    r'\bignore.{0,30}\b(previous|instruction)', r'\byou\s+are\s+now\b',
    r'\bjailbreak\b', r'\bsystem\s+prompt\b', r'\bdisregard\b',
]

ALL_PATTERNS = [re.compile(p, re.IGNORECASE) for p in BLOCK_PATTERNS]
_violations: dict[str, int] = {}
LOCKOUT_THRESHOLD = 3

def check(text: str, client_ip: str) -> tuple[bool, str]:
    if _violations.get(client_ip, 0) >= LOCKOUT_THRESHOLD:
        return True, "SESSION LOCKED. Contact administrator."
    for pat in ALL_PATTERNS:
        if pat.search(text):
            _violations[client_ip] = _violations.get(client_ip, 0) + 1
            remaining = LOCKOUT_THRESHOLD - _violations[client_ip]
            if remaining <= 0:
                return True, "SESSION LOCKED. Contact administrator."
            return True, f"I can only help with slope64 questions. {remaining} attempt(s) remaining."
    return False, ""

def get_stats() -> dict:
    return {"violations_by_ip": _violations, "lockout_threshold": LOCKOUT_THRESHOLD}
```

**server.py** — FastAPI (port 8094):
- Read `/workspace/reference/slope64_manual.txt` on startup
- `GET /` → HTML chat page
- `POST /chat` → SSE stream (guardrails check first, no API call if blocked)
  - Client IP from `X-Forwarded-For` or `request.client.host`
  - Agent tools: `search_manual(query)`, `explain_concept(concept)`
- `GET /api/guardrail-stats` → guardrails.get_stats()
- Rate limit: 30 requests/minute per IP via slowapi

**agent.py** — GPT-5.4-mini chat loop:
- System prompt: "You are a slope64 finite element analysis expert. Users ask questions about the slope64 program, its usage, input data format, output interpretation, and slope stability analysis concepts. Use the search_manual and explain_concept tools to provide accurate answers based on the embedded slope64 user manual. Be technical but clear."
- Tools: `search_manual(query)` → semantic search through manual, `explain_concept(term)` → explain in context
- Max 10 turns, SSE streaming
- Model from env: OPENAI_MODEL (default gpt-4o-mini)

**requirements.txt**:
```
fastapi>=0.104.0
uvicorn>=0.24.0
openai>=1.50.0
httpx>=0.25.0
slowapi>=0.1.9
```

**Dockerfile**:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8094
CMD ["python", "server.py"]
```

### Build and run

```bash
cd /workspace/<taskId>/slope64-chatbot
docker build -t slope64-chatbot:latest .
docker network create slope64-demo-net 2>/dev/null || true
docker run -d --name slope64-chatbot \
  -p 8094:8094 \
  --network slope64-demo-net \
  --env-file /workspace/.demo-secrets \
  -v /workspace/reference/slope64_manual.txt:/app/slope64_manual.txt:ro \
  slope64-chatbot:latest

# Verify
curl -s http://localhost:8094/health
```

### Test

```bash
curl -s -X POST http://localhost:8094/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"What input parameters does slope64 require?"}]}'
```

---

## PHASE 2 — Peer Review

Build a GPT-5.4-mini code reviewer. Create `peer-review/` in workspace.

**review_agent.py** — Light security review:
- Model: GPT-5.4-mini (from env)
- System prompt: "You are a Python code reviewer. Examine the slope64-chatbot source for security issues (injection, credential leaks, SSRF), code quality, error handling, and API design. Cite file names and line numbers. The chatbot service is reachable at http://slope64-chatbot:8094 on the slope64-demo-net Docker network."
- Tools: `read_file(path)`, `list_files(directory)`, `curl_endpoint(url, method)`, `write_finding(severity, title, description, file, line, recommendation)`
- 15 turn max, append findings to findings.json
- On completion: generate REVIEW.md sorted by severity

**Dockerfile** and **requirements.txt**: same pattern as Phase 1

### Build and run (async)

```bash
cd /workspace/<taskId>/peer-review
docker build -t slope64-peer-review:latest .
docker run -d --name slope64-peer-review \
  --network slope64-demo-net \
  --env-file /workspace/.demo-secrets \
  -v /workspace/<taskId>/slope64-chatbot:/audit:ro \
  slope64-peer-review:latest

echo "Peer review running in background. Chatbot live at http://localhost:8094"
```

Do NOT wait — review takes ~4 minutes.

---

## PHASE 3 — Collect Results

```bash
# Poll for completion
for i in $(seq 1 300); do
  docker ps --format "{{.Names}}" | grep -q "slope64-peer-review" || break
  sleep 1
done

# Copy review
docker cp slope64-peer-review:/app/REVIEW.md /workspace/<taskId>/REVIEW.md 2>/dev/null || true
docker logs slope64-peer-review > /workspace/<taskId>/peer-review.log

# Write summary
cat > /workspace/<taskId>/REPORT.md <<'REPORT'
# slope64 Q&A Chatbot — Build Report

## What was built

A FastAPI chatbot with the slope64 finite element analysis manual embedded in the system prompt.
Users ask questions via SSE-streamed GPT-5.4-mini agent with tools for semantic search and concept explanation.

## Architecture

- **Server**: FastAPI (8094)
- **Agent**: GPT-5.4-mini function-calling loop
- **Manual**: slope64_user_manual.txt (1,423 words, ~1,900 tokens)
- **Guardrails**: Deterministic regex blocks for injection/exfiltration
- **Rate limit**: 30 req/min per IP
- **Review**: GPT-5.4-mini peer code review (async)

## How to use

```bash
# Ask about slope64
curl -X POST http://localhost:8094/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"How do I specify boundary conditions?"}]}'
```

## Peer review findings

See REVIEW.md for detailed findings.

REPORT

echo "Done."
```

---
INST
)

TASK_JSON=$(jq -n \
  --arg desc "slope64 Q&A Chatbot — Build + Peer Review" \
  --arg inst "$INSTRUCTIONS" \
  '{
    description: $desc,
    instructions: $inst,
    persona: "developer",
    model: "sonnet",
    maxTurns: 60,
    mcpServers: []
  }')

RESPONSE=$(curl -s -X POST "$DRONE_URL/task" \
  -H "Content-Type: application/json" \
  -d "$TASK_JSON")

TASK_ID=$(echo "$RESPONSE" | jq -r '.taskId')

if [ "$TASK_ID" = "null" ] || [ -z "$TASK_ID" ]; then
  echo "ERROR: Failed to submit task"
  echo "$RESPONSE" | jq .
  exit 1
fi

echo "Task ID:  $TASK_ID"
echo "Stream:   curl -s $DRONE_URL/stream/$TASK_ID"
echo ""
echo "Streaming output..."
echo "─────────────────────────────────────────"

curl -s "$DRONE_URL/stream/$TASK_ID" | while IFS= read -r line; do
  if [[ $line == data:* ]]; then
    json="${line#data: }"
    event=$(echo "$json" | jq -r '.event' 2>/dev/null)

    if [ "$event" = "output" ]; then
      data=$(echo "$json" | jq -r '.data' 2>/dev/null)
      echo "$data"
    elif [ "$event" = "status" ]; then
      echo "[STATUS] $(echo "$json" | jq -r '.status')"
    elif [ "$event" = "complete" ]; then
      echo ""
      echo "─────────────────────────────────────────"
      echo "Status:  $(echo "$json" | jq -r '.status')"
      echo "Cost:    \$$(echo "$json" | jq -r '.costUsd // 0')"
      echo "Turns:   $(echo "$json" | jq -r '.numTurns // "N/A"')"
      echo ""
      break
    fi
  fi
done

echo "Chatbot: http://localhost:8094"
echo "Done."
