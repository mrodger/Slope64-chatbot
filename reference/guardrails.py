import re
from collections import OrderedDict

# Credential/code exfiltration
BLOCK_PATTERNS = [
    r'\bapi[\s_-]?key\b', r'\bsecret\b', r'\bpassword\b', r'\btoken\b',
    r'\bsk-[a-z0-9]', r'\bshow.*code\b', r'\bgive.*source\b',
    r'\bignore.{0,30}\b(previous|instruction)', r'\byou\s+are\s+now\b',
    r'\bjailbreak\b', r'\bsystem\s+prompt\b', r'\bdisregard\b',
]

ALL_PATTERNS = [re.compile(p, re.IGNORECASE) for p in BLOCK_PATTERNS]
# OrderedDict used as a bounded LRU: cap at 10000 IPs to prevent memory leak
_violations: OrderedDict = OrderedDict()
LOCKOUT_THRESHOLD = 3
MAX_TRACKED_IPS = 10000

def _record_violation(client_ip: str) -> int:
    count = _violations.get(client_ip, 0) + 1
    _violations[client_ip] = count
    _violations.move_to_end(client_ip)
    # Evict oldest entries once over the cap
    while len(_violations) > MAX_TRACKED_IPS:
        _violations.popitem(last=False)
    return count

def check(text: str, client_ip: str) -> tuple[bool, str]:
    if _violations.get(client_ip, 0) >= LOCKOUT_THRESHOLD:
        return True, "SESSION LOCKED. Contact administrator."
    for pat in ALL_PATTERNS:
        if pat.search(text):
            count = _record_violation(client_ip)
            remaining = LOCKOUT_THRESHOLD - count
            if remaining <= 0:
                return True, "SESSION LOCKED. Contact administrator."
            return True, f"I can only help with slope64 questions. {remaining} attempt(s) remaining."
    return False, ""

def get_stats() -> dict:
    return {"violations_by_ip": dict(_violations), "lockout_threshold": LOCKOUT_THRESHOLD}
