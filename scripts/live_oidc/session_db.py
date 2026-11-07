"""Private root-side test-session invalidation. Accept only session hashes, never print session IDs or subjects."""

import hashlib, json, sqlite3, sys, time
from pathlib import Path

p = Path("/opt/pearlandpaws-test/database/forms.db")
assert p.is_file()
a = json.load(sys.stdin)
db = sqlite3.connect(str(p))
db.row_factory = sqlite3.Row
rows = [
    r
    for r in db.execute("SELECT * FROM staff_sessions")
    if hashlib.sha256(r["id"].encode()).hexdigest() == a["session_sha256"]
]
assert len(rows) == 1
row = rows[0]
now = int(time.time() * 1000)
with db:
    if a["action"] == "revoke_subject":
        db.execute(
            "INSERT INTO staff_session_revocations(sub,revoked_at) VALUES (?,?) ON CONFLICT(sub) DO UPDATE SET revoked_at=excluded.revoked_at",
            (row["sub"], now),
        )
        changed = db.execute(
            "UPDATE staff_sessions SET revoked_at=? WHERE sub=? AND revoked_at IS NULL",
            (now, row["sub"]),
        ).rowcount
    elif a["action"] == "idle_expire":
        changed = db.execute(
            "UPDATE staff_sessions SET last_seen_at=? WHERE id=?",
            (now - 31 * 60 * 1000, row["id"]),
        ).rowcount
    elif a["action"] == "absolute_expire":
        changed = db.execute(
            "UPDATE staff_sessions SET expires_at=? WHERE id=?", (now - 1000, row["id"])
        ).rowcount
    else:
        raise AssertionError("unknown test action")
print(json.dumps({"matched": len(rows), "changed": changed, "action": a["action"]}))
