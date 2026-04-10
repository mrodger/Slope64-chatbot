"""
agent.py — GPT-5.4-mini chat loop for slope64 Q&A
Provides: search_manual(query), explain_concept(term)
Streams responses as SSE.
"""
import os
import json
import logging
import re
from openai import OpenAI, APIError, APIConnectionError, RateLimitError

log = logging.getLogger("slope64-chatbot.agent")

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

SYSTEM_PROMPT = (
    "You are a slope64 finite element analysis expert with full access to the slope64 user manual. "
    "Help users understand how to use slope64 for slope stability analysis. "
    "You can answer questions about:\n"
    "• Input file format and parameters (.dat files)\n"
    "• Material properties (friction angle, cohesion, Young's modulus, etc.)\n"
    "• Setting up geometries and mesh density\n"
    "• Interpreting output and factor of safety (FoS) results\n"
    "• Slope stability concepts and FEM analysis\n"
    "• Examples and best practices for different slope types\n\n"
    "Use the search_manual and explain_concept tools to provide accurate, well-referenced answers "
    "based on the embedded slope64 user manual. Be technical but accessible. "
    "Always cite relevant sections from the manual when providing guidance."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_manual",
            "description": "Semantic search through the slope64 user manual. Returns relevant excerpts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query — keywords or question about slope64."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "explain_concept",
            "description": "Explain a slope64 or slope-stability concept in context of the manual.",
            "parameters": {
                "type": "object",
                "properties": {
                    "concept": {
                        "type": "string",
                        "description": "Technical term or concept to explain."
                    }
                },
                "required": ["concept"]
            }
        }
    }
]


def _search_manual(manual_text: str, query: str) -> str:
    """Keyword-based search through manual sections."""
    keywords = re.findall(r'\w+', query.lower())
    lines = manual_text.split('\n')
    scored: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        lower = line.lower()
        hits = sum(1 for kw in keywords if kw in lower)
        if hits > 0:
            scored.append((hits, line.strip()))
    scored.sort(key=lambda x: -x[0])
    top = [s for _, s in scored[:15] if s]
    if not top:
        return "No relevant sections found for that query."
    return "\n".join(top)


def _explain_concept(manual_text: str, concept: str) -> str:
    """Find manual context around a concept."""
    lines = manual_text.split('\n')
    concept_lower = concept.lower()
    context_lines: list[str] = []
    for i, line in enumerate(lines):
        if concept_lower in line.lower():
            start = max(0, i - 2)
            end = min(len(lines), i + 5)
            context_lines.extend(lines[start:end])
            context_lines.append("---")
    if not context_lines:
        return f"No specific manual entry found for '{concept}'. It may be an implied concept."
    return "\n".join(context_lines[:60])


MAX_HISTORY_MESSAGES = 40  # prevent unbounded token growth

async def run_agent(messages: list[dict], manual_text: str):
    """
    Run up to 10-turn GPT-5.4-mini agentic loop.
    Yields SSE-formatted strings.
    """
    # Cap history to prevent excessively large API calls
    trimmed = list(messages)[-MAX_HISTORY_MESSAGES:]
    history = [{"role": "system", "content": SYSTEM_PROMPT}] + trimmed
    max_turns = 10

    for turn in range(max_turns):
        try:
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=history,
                tools=TOOLS,
                stream=True,
            )
        except RateLimitError:
            log.warning("OpenAI rate limit hit")
            yield f"data: {json.dumps({'type': 'text', 'content': 'Rate limit reached. Please try again shortly.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return
        except APIConnectionError as e:
            log.error("OpenAI connection error: %s", e)
            yield f"data: {json.dumps({'type': 'text', 'content': 'Connection error. Please try again.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return
        except APIError as e:
            log.error("OpenAI API error: %s", e)
            yield f"data: {json.dumps({'type': 'text', 'content': 'Service error. Please try again.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        # Accumulate streamed response
        assistant_message = {"role": "assistant", "content": "", "tool_calls": []}
        current_tool_call: dict | None = None

        for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            # Stream text content
            if delta.content:
                assistant_message["content"] += delta.content
                yield f"data: {json.dumps({'type': 'text', 'content': delta.content})}\n\n"

            # Accumulate tool calls
            if delta.tool_calls:
                for tc_chunk in delta.tool_calls:
                    idx = tc_chunk.index
                    # Extend list if needed
                    while len(assistant_message["tool_calls"]) <= idx:
                        assistant_message["tool_calls"].append({
                            "id": "", "type": "function",
                            "function": {"name": "", "arguments": ""}
                        })
                    if tc_chunk.id:
                        assistant_message["tool_calls"][idx]["id"] = tc_chunk.id
                    if tc_chunk.function.name:
                        assistant_message["tool_calls"][idx]["function"]["name"] += tc_chunk.function.name
                    if tc_chunk.function.arguments:
                        assistant_message["tool_calls"][idx]["function"]["arguments"] += tc_chunk.function.arguments

            finish = chunk.choices[0].finish_reason if chunk.choices else None
            if finish in ("stop", "tool_calls"):
                break

        # No tool calls → we're done
        if not assistant_message["tool_calls"]:
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        # Process tool calls
        history.append({
            "role": "assistant",
            "content": assistant_message["content"] or None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}
                }
                for tc in assistant_message["tool_calls"]
            ]
        })

        for tc in assistant_message["tool_calls"]:
            fn_name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}

            yield f"data: {json.dumps({'type': 'tool_call', 'tool': fn_name, 'args': args})}\n\n"

            if fn_name == "search_manual":
                result = _search_manual(manual_text, args.get("query", ""))
            elif fn_name == "explain_concept":
                result = _explain_concept(manual_text, args.get("concept", ""))
            else:
                result = f"Unknown tool: {fn_name}"

            history.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result
            })

    yield f"data: {json.dumps({'type': 'done'})}\n\n"
