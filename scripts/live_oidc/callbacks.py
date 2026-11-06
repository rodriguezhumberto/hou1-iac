"""Assert live state, nonce, PKCE, signature, redirect URI and reader-role behavior."""

from controller import *

results = []
try:
    for mode in ("state", "missing_cookie", "swapped_cookie", "nonce", "pkce"):
        r = run("callback_" + mode, "rejected", mode)
        if mode == "pkce":
            assert r["receipt"]["upstream_status"] == 400
        results.append(r)
    r = run("bad_signature", "rejected", fault="bad_signature")
    assert r["receipt"]["signature_fault_applied"]
    results.append(r)
    results.append(run("redirect_uri", "rejected", mode="redirect_uri"))
    results.append(run("reader", reader=True))
finally:
    (ROOT / "callback-report.json").write_text(json.dumps(results, indent=2))
