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
