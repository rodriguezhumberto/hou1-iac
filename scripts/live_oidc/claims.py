"""Exercise real Authentik-signed claim faults through the deployed staff callback. Always restore the original provider and remove the temporary scope mapping."""

from controller import *
from datetime import datetime, timezone
import time

provider = api("providers/oauth2/1/")
assert provider["client_id"] == "pearland-paws-staff-test"
original = {
    "property_mappings": provider["property_mappings"],
    "signing_key": provider["signing_key"],
}
(ROOT / "provider-restore.json").write_text(json.dumps(original))
(ROOT / "provider-restore.json").chmod(0o600)
scopes = api('propertymappings/provider/scope/?page_size=1000')
assert not scopes.get('next')
group_mappings = [m['pk'] for m in scopes['results']
                  if m['scope_name'] == 'groups' and m['pk'] in original['property_mappings']]
assert len(group_mappings) == 1
old_group = group_mappings[0]
results = []
mapping = None
started = datetime.now(timezone.utc).isoformat()
base = "{'groups': ['pearland-paws-staff-admin']}"
cases = [
    (
        "invalid_issuer",
        "{'groups':['pearland-paws-staff-admin'],'iss':'https://wrong-test.example.invalid/'}",
        "rejected",
    ),
    (
        "invalid_audience",
        "{'groups':['pearland-paws-staff-admin'],'aud':'other-test'}",
        "rejected",
    ),
    (
        "invalid_azp",
        "{'groups':['pearland-paws-staff-admin'],'azp':'other-test'}",
        "rejected",
    ),
    (
        "expired",
        "{'groups':['pearland-paws-staff-admin'],'exp':int(time())-120}",
        "rejected",
    ),
    (
        "future_nbf",
        "{'groups':['pearland-paws-staff-admin'],'nbf':int(time())+120}",
        "rejected",
    ),
    (
        "future_iat",
        "{'groups':['pearland-paws-staff-admin'],'iat':int(time())+120}",
        "rejected",
    ),
    (
        "excess_lifetime",
        "{'groups':['pearland-paws-staff-admin'],'exp':int(time())+1200}",
        "rejected",
    ),
    ("missing_groups", "{}", "rejected"),
    ("empty_groups", "{'groups':[]}", "rejected"),
    ("string_groups", "{'groups':'pearland-paws-staff-admin'}", "rejected"),
    (
        "duplicate_groups",
        "{'groups':['pearland-paws-staff-admin','pearland-paws-staff-admin']}",
        "rejected",
    ),
    ("blank_groups", "{'groups':[' ' ]}", "rejected"),
    ("unknown_groups", "{'groups':['unmapped-test-group']}", "forbidden"),
    (
        "skew_exp_30s",
        "{'groups':['pearland-paws-staff-admin'],'exp':int(time())-30}",
        "accepted",
    ),
    (
        "skew_nbf_30s",
        "{'groups':['pearland-paws-staff-admin'],'nbf':int(time())+30}",
        "accepted",
    ),
    (
        "skew_iat_30s",
        "{'groups':['pearland-paws-staff-admin'],'iat':int(time())+30}",
        "accepted",
    ),
]
try:
    mapping = api(
        "propertymappings/provider/scope/",
        "POST",
        {
            "name": "Issue 26 ephemeral claims test",
            "scope_name": "groups",
            "expression": "return " + base,
        },
    )["pk"]
    api(
        "providers/oauth2/1/",
        "PATCH",
        {
            "property_mappings": [
                mapping if v == old_group else v for v in original["property_mappings"]
            ]
        },
    )
    for name, expression, expect in cases:
        api(
            "propertymappings/provider/scope/" + mapping + "/",
            "PATCH",
            {"expression": "from time import time\nreturn " + expression},
        )
        r = run(name, expect)
        receipt = r["receipt"]
        assert (
            receipt["upstream_status"] == 200 and not receipt["signature_fault_applied"]
        )
        # Verify the intended fault actually reached the real callback, rather than an unrelated error.
        if name == "invalid_issuer":
            assert receipt["iss"] == "https://wrong-test.example.invalid/"
        if name == "invalid_audience":
            assert receipt["aud"] == "other-test"
        if name == "invalid_azp":
            assert receipt["azp"] == "other-test"
        if name == "missing_groups":
            assert not receipt["groups_present"]
        if name == "expired":
            assert receipt["time_offsets"]["exp"] < -100
        if name == "future_nbf":
            assert receipt["time_offsets"]["nbf"] > 100
        if name == "future_iat":
            assert receipt["time_offsets"]["iat"] > 100
        results.append(r)
        (ROOT / "claims-report.json").write_text(
            json.dumps(
                {
                    "started_at": started,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "results": results,
                },
                indent=2,
            )
        )
finally:
    api("providers/oauth2/1/", "PATCH", original)
    if mapping:
        api("propertymappings/provider/scope/" + mapping + "/", "DELETE")
    print("original staff provider mappings/signing key restored", flush=True)
