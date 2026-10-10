"""Hard stop on prices. pricing.md says the agent must never say any number, range or per-sq-ft figure."""
import re

DEFLECT = ("Pricing depends on the site, the materials you choose, and the scope. Your designer will walk you "
           "through it in detail at the consultation. I can book that for you right now if you'd like.")

_PATTERNS = [
    r"₹\s?[\d,.]+", r"\brs\.?\s?[\d,.]+", r"[\d,.]+\s?(?:lakhs?|lacs?|crores?|cr\b|k\b)",
    r"\b(?:lakhs?|lacs?|crores?)\b", r"per\s*sq\.?\s*(?:ft|feet|foot)", r"/\s*sq\.?\s*ft",
    r"\b(?:rates?|prices?|cost|costs)\s+(?:start|starts|begin|begins)\b", r"\bstarts?\s+(?:at|from)\b",
    r"\b(?:costs?|charge|charges)\s+(?:around|about|roughly|approximately)\b", r"\btypically\s+(?:rs|₹)",
]
_RX = re.compile("|".join(_PATTERNS), re.I)
_NUM = re.compile(r"\d[\d,.]*")

def violates(reply: str, caller_text: str) -> bool:
    """True if the reply states a price. Repeating a figure the caller already said is allowed."""
    hits = [m for m in _RX.finditer(reply)]
    if not hits:
        return False
    caller_nums = {n.replace(",", "") for n in _NUM.findall(caller_text)}
    for m in hits:
        nums = {n.replace(",", "") for n in _NUM.findall(m.group(0))}
        if not nums or not nums.issubset(caller_nums):
            return True
    return False

def clean(reply: str, caller_text: str):
    if violates(reply, caller_text):
        return DEFLECT, True
    return reply, False
