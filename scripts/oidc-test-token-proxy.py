#!/usr/bin/env python3
"""Ephemeral isolated-test token fault proxy; never publish its listening port.

Forward token responses unchanged except for a one-shot, explicitly code-hash
armed signature fault. Receipts record claim shape/relative times, never tokens,
client secrets, subjects, cookies, authorization codes or nonce values. Mount a
0700 control directory; remove the proxy and restore ingress after each drill.
"""

import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request


def decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def serve(host, client_id, state):
    assert "-test." in host and client_id.endswith("-test"), (
        "isolated test client required"
    )
    state = Path(state)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200 if self.path == "/health" else 404)
            self.end_headers()

        def do_POST(self):
            if self.path != "/application/o/token/":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length < 65536:
                self.send_error(413)
                return
            body = self.rfile.read(length)
            fields = urllib.parse.parse_qs(body.decode())
            code = fields.get("code", [""])[0]
            code_hash = hashlib.sha256(code.encode()).hexdigest()
            request = urllib.request.Request(
                "http://authentik-server-test:9000" + self.path,
                data=body,
                headers={
                    "Content-Type": self.headers.get("Content-Type", ""),
                    "Host": host,
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": host,
                    **(
                        {"Authorization": self.headers["Authorization"]}
                        if self.headers.get("Authorization")
                        else {}
                    ),
                },
            )
            try:
                response = urllib.request.urlopen(request, timeout=20)
            except urllib.error.HTTPError as exc:
                response = exc
            payload = response.read()
            receipt = {
                "upstream_status": response.status,
                "signature_fault_applied": False,
            }
            if response.status == 200 and fields.get("client_id") == [client_id]:
                token_response = json.loads(payload)
                token = token_response.get("id_token")
                if token:
                    parts = token.split(".")
                    header, claims = (json.loads(decode(p)) for p in parts[:2])
                    now = time.time()
                    receipt.update(
                        {
                            "alg": header.get("alg"),
                            "kid": header.get("kid"),
                            "iss": claims.get("iss"),
                            "aud": claims.get("aud"),
                            "azp": claims.get("azp"),
                            "groups_present": "groups" in claims,
                            "groups": claims.get("groups"),
                            "time_offsets": {
                                k: round(claims[k] - now)
                                if isinstance(claims.get(k), (int, float))
                                else None
                                for k in ("exp", "iat", "nbf")
                            },
                        }
                    )
                    control = json.loads((state / "control.json").read_text())
                    if control == {"code_sha256": code_hash, "mode": "bad_signature"}:
                        signature = bytearray(decode(parts[2]))
                        signature[0] ^= 1
                        parts[2] = (
                            base64.urlsafe_b64encode(signature).decode().rstrip("=")
                        )
                        token_response["id_token"] = ".".join(parts)
                        payload = json.dumps(token_response).encode()
                        receipt["signature_fault_applied"] = True
                        (state / "control.json").write_text("{}")
            if code and fields.get("client_id") == [client_id]:
                (state / (code_hash + ".json")).write_text(json.dumps(receipt))
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

    ThreadingHTTPServer(("0.0.0.0", 9080), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    serve(args.host, args.client_id, args.state)
