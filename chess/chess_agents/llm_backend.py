"""
LLM backend for the chess players.

Backends are selectable via config.LLM_BACKEND:
  "openai"  -> OpenAI Chat Completions (uses OPENAI_API_KEY). Default, so this
               system can run on the project's existing OpenAI key with no
               separate Gemini key.
  "gemini"  -> original google-genai path (kept intact).

Both paths share the same prompt, signature, and strict UCI parsing — the rest
of the system is unchanged.
"""
import re
import config

_UCI_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")

_PROMPT = """\
You are playing chess as {color}.
Current position (FEN): {fen}
Move history (SAN): {history}
You MUST choose exactly one move from this list of LEGAL moves:
{legal_moves_uci}
Respond with ONLY the move in UCI format (e.g. e2e4). No other text."""

_openai_client = None
_gemini_client = None


def _openai():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        _openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _openai_client


def _gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _gemini_client


def get_move(color: str, fen: str, san_history: list[str], legal_uci: list[str]) -> str | None:
    prompt = _PROMPT.format(
        color=color,
        fen=fen,
        history=san_history,
        legal_moves_uci=" ".join(legal_uci),
    )

    if config.LLM_BACKEND == "gemini":
        response = _gemini().models.generate_content(model=config.MODEL, contents=prompt)
        raw = response.text.strip().lower()
    else:  # "openai" (default)
        resp = _openai().chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw = (resp.choices[0].message.content or "").strip().lower()

    return raw if _UCI_RE.match(raw) else None
