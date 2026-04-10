"""
server.py — FastAPI slope64 Q&A Chatbot
Port: 8094
"""
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel

import guardrails
from agent import run_agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("slope64-chatbot")

VERSION = "1.1"

MANUAL_PATH = Path(os.environ.get("MANUAL_PATH", "/app/slope64_manual.txt"))
# Fallback: look next to this script
if not MANUAL_PATH.exists():
    MANUAL_PATH = Path(__file__).parent / "slope64_manual.txt"
manual_text: str = ""
MAX_MANUAL_SIZE = 10 * 1024 * 1024  # 10 MB max
TRUSTED_PROXIES = os.environ.get("TRUSTED_PROXIES", "127.0.0.1").split(",")

def _rate_limit_key(request: Request) -> str:
    """Same XFF logic as /chat — only trust X-Forwarded-For from known proxies."""
    client_ip = request.client.host if request.client else "unknown"
    if request.client and request.client.host in TRUSTED_PROXIES:
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            client_ip = xff.split(",")[0].strip()
    return client_ip

limiter = Limiter(key_func=_rate_limit_key)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global manual_text
    if MANUAL_PATH.exists():
        file_size = MANUAL_PATH.stat().st_size
        if file_size > MAX_MANUAL_SIZE:
            log.error(f"Manual file too large: {file_size} bytes > {MAX_MANUAL_SIZE} bytes")
            manual_text = "Manual file too large."
        else:
            manual_text = MANUAL_PATH.read_text(encoding="utf-8")
            log.info(f"Loaded slope64 manual: {len(manual_text)} chars, {len(manual_text.split())} words")
    else:
        log.warning(f"Manual not found at {MANUAL_PATH} — agent will have no reference material")
        manual_text = "No manual loaded."
    yield


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=()"
        return response

app = FastAPI(title="slope64 Q&A Chatbot", version=VERSION, lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SecurityHeadersMiddleware)

# CORS middleware — wildcard + credentials is invalid per CORS spec; restrict instead
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=bool(CORS_ORIGINS),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# ── Chat request schema ────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE.replace("v{{VERSION}}", f"v{VERSION}")

@app.get("/launcher", response_class=HTMLResponse)
async def launcher():
    launcher_path = Path(__file__).parent / "launcher.html"
    if launcher_path.exists():
        return launcher_path.read_text(encoding="utf-8")
    return "<h1>Launcher not found</h1>"

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/chat")
@limiter.limit("30/minute")
async def chat(request: Request, body: ChatRequest):
    client_ip = _rate_limit_key(request)

    # Validate message roles and content
    valid_roles = {"user", "assistant", "system"}
    for msg in body.messages:
        if msg.role not in valid_roles:
            return JSONResponse(
                {"error": f"Invalid role: {msg.role}"},
                status_code=400
            )
        if not isinstance(msg.content, str) or len(msg.content) > 50000:
            return JSONResponse(
                {"error": "Content must be a string under 50k chars"},
                status_code=400
            )

    # Guardrail check on last user message
    user_text = ""
    for msg in reversed(body.messages):
        if msg.role == "user":
            user_text = msg.content
            break

    blocked, reason = guardrails.check(user_text, client_ip)
    if blocked:
        async def blocked_stream():
            import json
            yield f"data: {json.dumps({'type': 'text', 'content': reason})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return StreamingResponse(blocked_stream(), media_type="text/event-stream")

    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    return StreamingResponse(
        run_agent(messages, manual_text),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.get("/api/guardrail-stats")
async def guardrail_stats(request: Request):
    # Require admin secret header for sensitive endpoint
    secret = os.environ.get("ADMIN_SECRET")
    auth = request.headers.get("X-Admin-Secret")
    if not secret or auth != secret:
        return JSONResponse(
            {"error": "Unauthorized"},
            status_code=403
        )
    return JSONResponse(guardrails.get_stats())


# ── Embedded UI ────────────────────────────────────────────────────────────────

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>slope64 Q&A</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0f1117; color: #e0e0e0; height: 100vh; display: flex; flex-direction: column; }
    header { background: #1a1d27; padding: 16px 24px; border-bottom: 1px solid #2d3045; display: flex; justify-content: space-between; align-items: flex-start; }
    .header-left h1 { font-size: 1.25rem; color: #7eb3ff; }
    .header-left p { font-size: 0.8rem; color: #888; margin-top: 2px; }
    .header-right { text-align: right; }
    .version { font-size: 0.75rem; color: #666; background: #0f1117; padding: 4px 8px; border-radius: 4px; border: 1px solid #2d3045; }
    #chat { flex: 1; overflow-y: auto; padding: 20px 24px; display: flex; flex-direction: column; gap: 12px; }
    .msg { max-width: 760px; padding: 12px 16px; border-radius: 10px; line-height: 1.55; font-size: 0.93rem; }
    .msg.user { background: #1e3a5f; align-self: flex-end; color: #c8dfff; }
    .msg.assistant { background: #1a1d27; align-self: flex-start; border: 1px solid #2d3045; color: #e0e0e0; }
    .msg.tool { background: #12251a; align-self: flex-start; border: 1px solid #1e4a2a; color: #6fcf97; font-size: 0.8rem; font-family: monospace; }
    #input-area { padding: 16px 24px; background: #1a1d27; border-top: 1px solid #2d3045; display: flex; gap: 10px; }
    #input { flex: 1; background: #0f1117; border: 1px solid #3d4060; border-radius: 8px; padding: 10px 14px; color: #e0e0e0; font-size: 0.93rem; resize: none; height: 48px; }
    #input:focus { outline: none; border-color: #7eb3ff; }
    #send { background: #3d6dbf; color: white; border: none; border-radius: 8px; padding: 0 20px; font-size: 0.93rem; cursor: pointer; }
    #send:hover { background: #5285d4; }
    #send:disabled { opacity: 0.4; cursor: not-allowed; }
  </style>
</head>
<body>
  <header>
    <div class="header-left">
      <h1>slope64 Q&amp;A Assistant</h1>
      <p>Ask questions about slope64 finite element analysis — input format, output interpretation, boundary conditions, and more.</p>
    </div>
    <div class="header-right">
      <div class="version">v{{VERSION}}</div>
    </div>
  </header>
  <div id="chat">
    <div class="msg assistant">Hello! I'm your slope64 FEA expert. Ask me anything about slope64 — input parameters, material definitions, boundary conditions, output interpretation, or slope stability concepts.</div>
  </div>
  <div id="input-area">
    <textarea id="input" placeholder="Ask about slope64..." rows="1"></textarea>
    <button id="send" onclick="sendMsg()">Send</button>
  </div>
  <script>
    const chat = document.getElementById('chat');
    const input = document.getElementById('input');
    const btn = document.getElementById('send');
    let history = [];

    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMsg(); }
    });

    function addMsg(role, text) {
      const div = document.createElement('div');
      div.className = 'msg ' + role;
      div.textContent = text;
      chat.appendChild(div);
      chat.scrollTop = chat.scrollHeight;
      return div;
    }

    async function sendMsg() {
      const text = input.value.trim();
      if (!text) return;
      input.value = '';
      btn.disabled = true;

      addMsg('user', text);
      history.push({ role: 'user', content: text });

      const resp = fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: history })
      });

      let assistantDiv = null;
      let assistantText = '';

      const reader = (await resp).body.getReader();
      const dec = new TextDecoder();
      let buf = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split('\\n\\n');
        buf = parts.pop();
        for (const part of parts) {
          if (!part.startsWith('data: ')) continue;
          const ev = JSON.parse(part.slice(6));
          if (ev.type === 'text') {
            if (!assistantDiv) assistantDiv = addMsg('assistant', '');
            assistantText += ev.content;
            assistantDiv.textContent = assistantText;
            chat.scrollTop = chat.scrollHeight;
          } else if (ev.type === 'tool_call') {
            addMsg('tool', `🔧 ${ev.tool}(${JSON.stringify(ev.args)})`);
          }
        }
      }

      if (assistantText) history.push({ role: 'assistant', content: assistantText });
      btn.disabled = false;
      input.focus();
    }
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8094, log_level="info")
