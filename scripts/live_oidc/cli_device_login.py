"""Complete a fresh test CLI device-code flow through external Authentik sign-in."""

from controller import *
import re, html.parser, http.cookiejar, urllib.request, urllib.parse

url = re.search(r"https://[^\s]+", (ROOT / "cli-login-private.log").read_text())[0]
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def request(url, data=None):
    h = {"Accept": "application/json", "Origin": AUTH, "Referer": AUTH + "/"}
    if isinstance(data, bytes):
        h["Content-Type"] = "application/x-www-form-urlencoded"
    if isinstance(data, dict):
        h["Content-Type"] = "application/json"
        csrf = next(
            (c.value for c in jar if c.name in ("authentik_csrf", "csrftoken")), None
        )
        if csrf:
            h["X-CSRFToken"] = csrf
        data = json.dumps(data).encode()
    return op.open(urllib.request.Request(url, data=data, headers=h), timeout=25)


class Inspect(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.forms = []
        self.inputs = []
        self.buttons = []

    def handle_starttag(self, t, a):
        d = dict(a)
        if t == "a":
            self.links.append(d.get("href", ""))
        if t == "form":
            self.forms.append(d)
        if t == "input":
            self.inputs.append(d)
        if t == "button":
            self.buttons.append(d)


r = request(url)
body = r.read().decode()
i = Inspect()
i.feed(body)
if i.forms and i.forms[0].get("action") == "/oauth2/device/auth/verify_code":
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["user_code"][0]
    r = request(
        urllib.parse.urljoin(url, i.forms[0]["action"]),
        urllib.parse.urlencode({"user_code": code}).encode(),
    )
    body = r.read().decode()
    i = Inspect()
    i.feed(body)
links = [l for l in i.links if "authentik-" in l]
print(
    "device_initial",
    r.status,
    "forms",
    i.forms,
    "input_names",
    [v.get("name") for v in i.inputs],
    "external_provider_links",
    len(links),
    flush=True,
)
if links:
    r = request(urllib.parse.urljoin(url, links[0]))
    target = r.geturl()
    apiurl = (
        AUTH
        + "/api/v3/flows/executor/default-authentication-flow/?"
        + urllib.parse.urlencode({"query": urllib.parse.urlsplit(target).query})
    )
    f = json.load(request(apiurl))
    for _ in range(10):
        c = f.get("component")
        if f.get("type") == "redirect" or c == "xak-flow-redirect":
            r = request(urllib.parse.urljoin(AUTH, f["to"]))
            break
        if c == "ak-stage-identification":
            f = json.load(
                request(
                    apiurl,
                    {
                        "uid_field": "vps-staff-admin",
                        "password": secrets()["TEST_ADMIN_PASSWORD"],
                    },
                )
            )
        elif c == "ak-stage-password":
            f = json.load(
                request(apiurl, {"password": secrets()["TEST_ADMIN_PASSWORD"]})
            )
        elif c == "ak-stage-consent":
            f = json.load(request(apiurl, {"consent": True}))
        else:
            raise AssertionError("unexpected Auth stage " + str(c))
    body = r.read().decode()
    i = Inspect()
    i.feed(body)
    print(
        "device_post_auth",
        r.status,
        "path",
        urllib.parse.urlsplit(r.geturl()).path,
        "forms",
        i.forms,
        "input_names",
        [v.get("name") for v in i.inputs],
        "buttons",
        i.buttons,
        flush=True,
    )
    assert urllib.parse.urlsplit(r.geturl()).path == "/oauth2/device/callback"
    print(
        "PASS CLI device authorization returned through external Authentik provider",
        flush=True,
    )
