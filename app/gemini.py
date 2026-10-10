"""Thin Gemini REST client. MOCK_LLM=1 swaps in canned behaviour so the plumbing runs without a key."""
import json, time, re
import httpx
from app import config

class LLMUnavailable(Exception):
    pass

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

def generate(system: str, history: list, schema: dict = None, temperature: float = 0.3):
    """history: [{'role': 'user'|'model', 'text': str}]. Returns (parsed_json_or_text, usage dict)."""
    if config.MOCK_LLM:
        from app import mock_llm
        return mock_llm.respond(system, history, schema), {"in": 0, "out": 0}
    if not config.GEMINI_API_KEY:
        raise LLMUnavailable("GEMINI_API_KEY is not set. Add it to .env or set MOCK_LLM=1.")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": h["role"], "parts": [{"text": h["text"]}]} for h in history],
        "generationConfig": {"temperature": temperature, "thinkingConfig": {"thinkingBudget": 0}},
    }
    if schema:
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["responseSchema"] = schema
    last = None
    for attempt in range(4):
        try:
            r = httpx.post(URL.format(model=config.GEMINI_MODEL), params={"key": config.GEMINI_API_KEY}, json=body, timeout=40)
            if r.status_code in (429, 500, 503):
                last = r.text[:200].replace(config.GEMINI_API_KEY, "***"); time.sleep(1.5 * (attempt + 1)); continue
            r.raise_for_status()
            data = r.json()
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            u = data.get("usageMetadata", {})
            usage = {"in": u.get("promptTokenCount", 0), "out": u.get("candidatesTokenCount", 0)}
            return (json.loads(text) if schema else text), usage
        except (httpx.HTTPError, KeyError, json.JSONDecodeError) as e:
            last = str(e).replace(config.GEMINI_API_KEY, "***"); time.sleep(1.0 * (attempt + 1))
    raise LLMUnavailable(f"Gemini call failed: {last}")

def cost_inr(usage: dict) -> float:
    usd = usage["in"] / 1e6 * config.GEMINI_IN + usage["out"] / 1e6 * config.GEMINI_OUT
    return usd * config.INR_PER_USD
