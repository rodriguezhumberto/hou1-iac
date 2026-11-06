"""Rotate only the isolated staff signing key and assert real login without app restart."""

from controller import *
import urllib.request

p = api("providers/oauth2/1/")
assert p["client_id"] == "pearland-paws-staff-test"
original = p["signing_key"]
generated = None
jwks_url = AUTH + "/application/o/pearland-paws-staff-test/jwks/"


def kids():
    return sorted(
        k["kid"]
        for k in json.load(urllib.request.urlopen(jwks_url, timeout=15))["keys"]
    )


results = []
try:
    before = kids()
    results.append(run("pre_rotation"))
    key = api(
        "crypto/certificatekeypairs/generate/",
        "POST",
        {"common_name": "vps-security-rotation-test", "validity_days": 1, "alg": "rsa"},
    )
    generated = key["pk"]
    (ROOT / "rotation-key.json").write_text(json.dumps({"pk": generated}))
    api("providers/oauth2/1/", "PATCH", {"signing_key": generated})
    after = kids()
    assert before != after and not set(before) & set(after)
    results.append(run("rotated_key"))
    assert results[-1]["receipt"]["kid"] in after
    api("providers/oauth2/1/", "PATCH", {"signing_key": original})
    assert kids() == before
    results.append(run("original_key_restored"))
    assert results[-1]["receipt"]["kid"] in before
    (ROOT / "rotation-report.json").write_text(
        json.dumps(
            {
                "jwks_before": before,
                "jwks_rotated": after,
                "overlap_published": False,
                "results": results,
            },
            indent=2,
        )
    )
finally:
    api("providers/oauth2/1/", "PATCH", {"signing_key": original})
    if generated:
        api("crypto/certificatekeypairs/" + generated + "/", "DELETE")
    print("original signing key restored; temporary rotation key removed", flush=True)
