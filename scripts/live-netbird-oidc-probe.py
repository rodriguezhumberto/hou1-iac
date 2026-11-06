#!/usr/bin/env python3
"""Live external-provider PKCE/API probe; isolated credentials via JSON stdin only."""

import base64, hashlib, html.parser, http.cookiejar, json, os, secrets, subprocess, urllib.request, urllib.parse, urllib.error, sys

s = json.load(sys.stdin)
AUTH = s["auth_url"].rstrip("/")
NB = s["netbird_url"].rstrip("/")
assert all(
    urllib.parse.urlsplit(u).scheme == "https"
    and "-test." in urllib.parse.urlsplit(u).hostname
    for u in (AUTH, NB)
), "isolated test hosts required"


class CaptureCallback(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if newurl.startswith(NB + "/nb-auth?"):
            self.callback = newurl
            return None
        return super().redirect_request(request, fp, code, msg, headers, newurl)


redirect = CaptureCallback()
redirect.callback = None
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), redirect)


def req(url, data=None, headers=None):
    h = headers or {"Accept": "application/json", "Referer": AUTH + "/", "Origin": AUTH}
    if data is not None and isinstance(data, dict):
        h["Content-Type"] = "application/json"
        csrf = next(
            (c.value for c in jar if c.name in ["authentik_csrf", "csrftoken"]), None
        )
        if csrf:
            h["X-CSRFToken"] = csrf
        data = json.dumps(data).encode()
    return op.open(urllib.request.Request(url, data=data, headers=h), timeout=20)


d = json.load(req(NB + "/oauth2/.well-known/openid-configuration"))
assert d["issuer"] == NB + "/oauth2"
v = secrets.token_urlsafe(48)
state = secrets.token_urlsafe(24)
q = {
    "client_id": "netbird-dashboard",
    "redirect_uri": NB + "/nb-auth",
    "response_type": "code",
    "scope": "openid profile email groups",
    "state": state,
    "nonce": secrets.token_urlsafe(24),
    "code_challenge_method": "S256",
    "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest())
    .decode()
    .rstrip("="),
}
body = (
    req(d["authorization_endpoint"] + "?" + urllib.parse.urlencode(q)).read().decode()
)
links = []


class Links(html.parser.HTMLParser):
    def handle_starttag(self, t, attrs):
        a = dict(attrs)
        if t == "a" and "authentik-" in a.get("href", ""):
            links.append(a["href"])


Links().feed(body)
assert len(links) == 1
r = req(urllib.parse.urljoin(NB, links[0]))
target = r.geturl()
assert urllib.parse.urlsplit(target).netloc == urllib.parse.urlsplit(AUTH).netloc
api = (
    AUTH
    + "/api/v3/flows/executor/default-authentication-flow/?"
    + urllib.parse.urlencode({"query": urllib.parse.urlsplit(target).query})
)
f = json.load(req(api))
for _ in range(8):
    c = f.get("component")
    kind = f.get("type")
    if kind == "redirect" or c == "xak-flow-redirect":
        try:
            final = req(urllib.parse.urljoin(AUTH, f["to"]))
        except urllib.error.HTTPError as e:
            if not redirect.callback:
                raise
        break
    if c == "ak-stage-identification":
        f = json.load(req(api, {"uid_field": s["username"], "password": s["password"]}))
    elif c == "ak-stage-password":
        f = json.load(req(api, {"password": s["password"]}))
    elif c == "ak-stage-consent":
        f = json.load(req(api, {"consent": True}))
    else:
        raise RuntimeError("unexpected stage " + str(c))
else:
    raise RuntimeError("authentication flow did not finish")
u = urllib.parse.urlsplit(redirect.callback or final.geturl())
params = urllib.parse.parse_qs(u.query)
assert u.netloc == urllib.parse.urlsplit(NB).netloc and u.path == "/nb-auth", (
    "external provider did not return through dashboard callback"
)
assert params.get("state") == [state] and params.get("code"), (
    "missing code or incorrect state"
)
code = params["code"][0]
body = urllib.parse.urlencode(
    {
        "grant_type": "authorization_code",
        "client_id": "netbird-dashboard",
        "redirect_uri": NB + "/nb-auth",
        "code": code,
        "code_verifier": "deliberately-wrong-verifier"
        if s.get("mode") == "pkce"
        else v,
    }
).encode()
if s.get("mode") == "pkce":
    try:
        req(
            d["token_endpoint"],
            body,
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
    except urllib.error.HTTPError as e:
        assert e.code == 400
        print(
            json.dumps(
                {"case": "netbird_pkce_mismatch", "status": 400, "assertion": "passed"}
            )
        )
        sys.exit(0)
    raise AssertionError("NetBird accepted incorrect PKCE verifier")
tokens = json.load(
    req(
        d["token_endpoint"], body, {"Content-Type": "application/x-www-form-urlencoded"}
    )
)
assert tokens.get("access_token") and tokens.get("id_token"), "token exchange failed"
print(
    "PASS Authentik -> NetBird embedded IdP -> dashboard callback; PKCE token exchange"
)
with req(
    NB + "/api/users", headers={"Authorization": "Bearer " + tokens["access_token"]}
) as r:
    assert r.status == 200
    print("PASS bearer-authenticated NetBird API HTTP", r.status)
try:
    req(
        d["token_endpoint"], body, {"Content-Type": "application/x-www-form-urlencoded"}
    )
except urllib.error.HTTPError as e:
    assert e.code in [400, 401]
    print("PASS replayed authorization code rejected")
else:
    raise AssertionError("replayed authorization code accepted")

parts = tokens["access_token"].split(".")
assert len(parts) == 3
sig = bytearray(base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4)))
sig[0] ^= 1
parts[2] = base64.urlsafe_b64encode(sig).decode().rstrip("=")
for name, bearer in [
    ("malformed", "invalid-test-token"),
    ("invalid_signature", ".".join(parts)),
]:
    try:
        req(NB + "/api/users", headers={"Authorization": "Bearer " + bearer})
    except urllib.error.HTTPError as e:
        assert e.code == 401
        print(
            json.dumps(
                {"case": "netbird_" + name, "status": e.code, "assertion": "passed"}
            )
        )
    else:
        raise AssertionError("NetBird accepted invalid bearer")
