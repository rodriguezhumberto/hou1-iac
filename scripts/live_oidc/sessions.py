"""Retain real HTTP sessions, invalidate test database rows and assert deployed middleware denial."""

from controller import *
import time

results = []


def retained(name):
    a = {
        "run_id": name + "-" + uuid.uuid4().hex[:8],
        "username": "vps-staff-admin",
        "password": secrets()["TEST_ADMIN_PASSWORD"],
    }
    probe({**a, "action": "start"})
    r = probe({**a, "action": "finish", "keep_session": True})
    assert r["protected_status"] == 200 and r["session_created"]
    return a, r["session_sha256"]


def change(action, session_hash):
    r = json.loads(
        shell(
            "sudo -n python3 /root/hou1-test-session-db.py",
            json.dumps({"action": action, "session_sha256": session_hash}),
        )
    )
    assert r["matched"] == 1 and r["changed"] >= 1
    return r


try:
    a, h = retained("revoke_session_a")
    b, hb = retained("revoke_session_b")
    changed = change("revoke_subject", h)
    assert changed["changed"] >= 2
    for v in (a, b):
        assert (
            probe({**v, "action": "logout", "expected_status": 401})["protected_status"]
            == 401
        )
    results.append(
        {
            "case": "subject_revocation_two_sessions",
            "assertion": "passed",
            "revocation_mechanism": "operator SQLite invalidation matching revokeStaffSubject",
            "revoked_rows": changed["changed"],
        }
    )
    # Fresh login issued after revocation must remain possible.
    results.append(run("login_after_subject_revocation"))
    for mode in ("idle_expire", "absolute_expire"):
        a, h = retained(mode)
        change(mode, h)
        assert (
            probe({**a, "action": "logout", "expected_status": 401})["protected_status"]
            == 401
        )
        results.append(
            {
                "case": mode,
                "assertion": "passed",
                "mechanism": "advance only test session DB timestamp; real HTTP authorization",
            }
        )
finally:
    (ROOT / "session-report.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results), flush=True)
