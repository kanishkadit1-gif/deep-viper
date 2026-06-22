import json
import re
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from deep_viper.config import VLMConfig


def build_llm(cfg: VLMConfig) -> ChatOpenAI:
    kwargs = dict(
        model=cfg.model,
        api_key=cfg.api_key,
        temperature=cfg.temperature,
        timeout=cfg.timeout,
        max_tokens=cfg.max_tokens,
        max_retries=2,
    )
    if "openai.com" not in cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return ChatOpenAI(**kwargs)


def call_vlm(llm: ChatOpenAI, prompt: str, image_b64: str | None = None) -> str:
    """Single VLM call. Returns raw text response."""
    if image_b64:
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ]
    else:
        content = prompt

    msg = HumanMessage(content=content)
    response = llm.invoke([msg])

    text = ""
    if hasattr(response, "content") and response.content:
        text = response.content
    elif hasattr(response, "additional_kwargs"):
        rc = response.additional_kwargs.get("reasoning_content", "")
        if rc:
            text = rc

    return text.strip()


def extract_json(text: str, llm: ChatOpenAI | None = None) -> dict | list:
    """
    Parse JSON from a VLM response.
    If parsing fails and llm is provided, re-prompt once asking for valid JSON only.
    """
    result = _try_parse(text)
    if result is not None:
        return result

    if llm is not None:
        print("  [VLM] JSON parse failed — re-prompting for valid JSON...")
        fix_prompt = (
            "Your previous response contained invalid JSON. "
            "Output ONLY the corrected valid JSON object, nothing else:\n\n"
            + text
        )
        corrected = call_vlm(llm, fix_prompt)
        result = _try_parse(corrected)
        if result is not None:
            return result

    raise ValueError(f"No valid JSON found in VLM response:\n{text[:600]}")


def _try_parse(text: str) -> dict | list | None:
    """Try to extract and parse a JSON object from text. Returns None on failure."""
    # Try fenced code blocks first
    blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text)
    for raw in reversed(blocks):
        try:
            return json.loads(raw.strip())
        except json.JSONDecodeError:
            pass

    # Find the outermost JSON object via balanced brace scan
    start = text.find("{")
    if start != -1:
        depth = 0
        for k in range(start, len(text)):
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start: k + 1])
                    except json.JSONDecodeError:
                        break

    return None
