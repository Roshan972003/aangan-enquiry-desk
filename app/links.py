"""Signed one-click links for designer emails (Accept lead, Listen to call). No login needed, the signature proves the link came from us."""
import hashlib, hmac
from app import config

def _secret():
    return (config.env("LINK_SECRET") or config.GEMINI_API_KEY or "dev-secret") + "|aangan-links"

def sign(cid, action):
    return hmac.new(_secret().encode(), f"{cid}:{action}".encode(), hashlib.sha256).hexdigest()[:24]

def ok(cid, action, token):
    return hmac.compare_digest(sign(cid, action), token or "")

def url(cid, action):
    return f"{config.PUBLIC_URL}/api/lead/{cid}/{action}?t={sign(cid, action)}"
