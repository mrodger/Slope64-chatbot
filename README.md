# slope64 Q&A Chatbot

An autonomous multi-agent demo: Claude Sonnet builds a slope64 finite element analysis chatbot with the user manual embedded, then GPT-5.4-mini peer-reviews the code.

**Runtime**: ~10 minutes | **Cost**: ~$1.50

---

## What it does

```
You run ./run.sh
     │
     ▼
Drone (Claude Sonnet) receives the task
     │
     ├── Phase 1: Generates Python code, builds FastAPI container
     │           with slope64 manual embedded in system prompt
     │
     ├── Phase 2: Builds GPT-5.4-mini code reviewer (async background)
     │
     └── Phase 3: Collects findings, generates report
```

The chatbot is live on `http://localhost:8094` immediately after Phase 1 completes.

---

## Prerequisites

- Drone running at `http://127.0.0.1:3010`
- `OPENAI_API_KEY` in `~/.secrets.env`
- Docker socket accessible from Drone container

---

## Run

```bash
./run.sh
```

Watch progress:
- Terminal: streaming output
- Browser: `http://localhost:3010/stream-view` (live turns)

Chatbot appears at `http://localhost:8094` once Phase 1 completes (~5 min).

---

## What gets built

A FastAPI server with:
- `/` — HTML chat page
- `POST /chat` — SSE-streamed GPT-5.4-mini Q&A
  - Tools: semantic search + concept explanation
  - System prompt references slope64_user_manual.txt
- Rate limiting: 30 req/min per IP
- Guardrails: deterministic regex blocks for injection/exfiltration

---

## Example queries

```bash
curl -X POST http://localhost:8094/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "What are the input file requirements for slope64?"
      }
    ]
  }'
```

---

## Repo structure

```
slope64-chatbot/
├── run.sh             # Demo launcher
├── README.md          # This file
├── .gitignore
└── reference/
    └── slope64_manual.txt  # Embedded in chatbot system prompt
```

---

## How the manual is used

The slope64 user manual (~1,900 tokens) is:
1. Copied into the Drone workspace at runtime
2. Mounted read-only into the chatbot container
3. Loaded by the FastAPI server on startup
4. Embedded in the agent's system prompt as reference material
5. Available to the agent's `search_manual()` tool for semantic search

---

## Peer review

GPT-5.4-mini reviews the generated code for:
- Security issues (injection, credential leaks, SSRF)
- Code quality and error handling
- API design

Review runs asynchronously in Phase 2 while the chatbot is live.

---

## License

MIT
