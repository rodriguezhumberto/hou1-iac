#!/usr/bin/env python3
"""Environment-bound synthetic staff login; emit only numeric monitoring state."""
import argparse
import http.client
import http.cookiejar
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

BODY_LIMIT = 262144
CALLBACK = '/api/staff/oidc/callback'
READ = '/api/staff/cms/pets'


def endpoint(value):
    url = urllib.parse.urlsplit(value)
    if (not isinstance(value, str) or re.search(r'[\s\x00-\x1f\x7f\\]', value)
            or url.scheme != 'https' or url.port not in (None, 443)
            or url.username or url.password or not url.hostname or url.fragment
            or not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*', url.hostname)):
        raise ValueError('Invalid HTTPS endpoint')
    return 'https://' + url.hostname


def validate(config):
    if config['environment'] not in ('test', 'production'):
        raise ValueError('Explicit environment required')
    auth, staff = [config[key] for key in ('auth_origin', 'staff_origin')]
    if endpoint(auth) != auth or endpoint(staff) != staff or auth == staff:
        raise ValueError('Separate canonical service origins required')
    issuer = urllib.parse.urlsplit(config['issuer'])
    if (endpoint(config['issuer']) != auth or issuer.query
            or not re.fullmatch(r'/application/o/[a-zA-Z0-9_-]+/', issuer.path)):
        raise ValueError('Exact Authentik issuer required')
    for key in ('client_id', 'flow', 'session_cookie'):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', config[key]):
            raise ValueError('Invalid client or flow contract')
    subnet = ipaddress.IPv4Network(config['netbird_subnet'], strict=True)
    address = ipaddress.IPv4Address(config['server_ip'])
    if (not subnet.subnet_of(ipaddress.IPv4Network('100.64.0.0/10')) or address not in subnet
            or address in (subnet.network_address, subnet.broadcast_address)):
        raise ValueError('Pin the selected HOU1 VPN address')
    return config


def credentials(values, environment):
    if (values.get('environment') != environment or values.get('kind') != 'synthetic-reader'
            or not isinstance(values.get('username'), str) or not values['username']
            or not isinstance(values.get('password'), str) or len(values['password']) < 24
            or any(c in values['username'] + values['password'] for c in '\r\n\x00')):
        raise ValueError('Require separate environment-bound synthetic reader credentials')
    return values


class Transport(urllib.request.HTTPSHandler):
    """Connect to the declared VPN IP while preserving hostname/SNI/CA verification."""
    def __init__(self, server_ip, context):
        super().__init__(context=context)
        self.server_ip = server_ip

    def https_open(self, request):
        def connection(host, **kwargs):
            client = http.client.HTTPSConnection(host, **kwargs)
            client._create_connection = lambda address, timeout, source_address=None: socket.create_connection(
                (self.server_ip, 443), timeout, source_address)
            return client
        return self.do_open(connection, request, context=self._context)


class Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, config):
        self.config = config
        self.authorization = False
        self.callback = False
        self.state = None

    def allowed(self, url):
        if endpoint(url) not in (self.config['auth_origin'], self.config['staff_origin']):
            raise ValueError('Unexpected probe destination')

    def redirect_request(self, request, fp, code, message, headers, url):
        self.allowed(url)
        target = urllib.parse.urlsplit(url)
        if endpoint(url) == self.config['auth_origin'] and target.path == '/application/o/authorize/':
            query = urllib.parse.parse_qs(target.query, keep_blank_values=True)
            expected = dict(client_id=self.config['client_id'], response_type='code',
                            redirect_uri=self.config['staff_origin'] + CALLBACK, code_challenge_method='S256')
            if (any(query.get(key) != [value] for key, value in expected.items())
                    or any(len(query.get(key, [])) != 1 or not query[key][0] for key in ('state', 'nonce', 'code_challenge'))
                    or not re.fullmatch(r'[A-Za-z0-9_-]{43}', query['code_challenge'][0])):
                raise ValueError('Unexpected authorization/PKCE contract')
            if self.state is not None and self.state != query['state'][0]:
                raise ValueError('Authorization state changed')
            self.state = query['state'][0]
            self.authorization = True
        if endpoint(url) == self.config['staff_origin'] and target.path == CALLBACK:
            query = urllib.parse.parse_qs(target.query, keep_blank_values=True)
            if not self.authorization or any(len(query.get(key, [])) != 1 or not query[key][0] for key in ('code', 'state')):
                raise ValueError('Unexpected authorization callback')
            if query['state'][0] != self.state: raise ValueError('Callback state differs')
            self.callback = True
        if request.data is not None and endpoint(request.full_url) != endpoint(url):
            raise ValueError('Never redirect a credential submission to another origin')
        redirected = super().redirect_request(request, fp, code, message, headers, url)
        if redirected and endpoint(request.full_url) != endpoint(url):
            for header in list(redirected.headers) + list(redirected.unredirected_hdrs):
                if header.lower() in ('origin', 'referer', 'x-csrftoken', 'cookie', 'authorization'):
                    redirected.remove_header(header)
        return redirected


def probe(config, secret, context=None):
    validate(config); credentials(secret, config['environment'])
    context = context or ssl.create_default_context()
    jar = http.cookiejar.CookieJar()
    redirects = Redirects(config)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), Transport(config['server_ip'], context),
                                        urllib.request.HTTPCookieProcessor(jar), redirects)
    deadline = time.monotonic() + 50

    def request(url, data=None, method=None, cookie=None):
        redirects.allowed(url)
        origin = endpoint(url)
        headers = {'Accept': 'application/json', 'Origin': origin, 'Referer': origin + '/'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
            if origin == config['auth_origin']:
                csrf = next((c.value for c in jar if c.name in ('authentik_csrf', 'csrftoken')
                             and c.domain.lstrip('.') == urllib.parse.urlsplit(origin).hostname), None)
                if csrf: headers['X-CSRFToken'] = csrf
        selected = opener
        if cookie is not None:
            headers['Cookie'] = config['session_cookie'] + '=' + cookie
            selected = urllib.request.build_opener(urllib.request.ProxyHandler({}), Transport(config['server_ip'], context), NoRedirect())
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise TimeoutError('Probe deadline exceeded')
        return selected.open(urllib.request.Request(url, headers=headers, method=method,
                             data=None if data is None else json.dumps(data).encode()), timeout=min(8, remaining))

    def json_response(response):
        with response:
            value = response.read(BODY_LIMIT + 1)
            if len(value) > BODY_LIMIT: raise ValueError('Oversized probe response')
            return json.loads(value)

    staff = config['staff_origin']
    session = None
    try:
        discovery = json_response(request(config['issuer'] + '.well-known/openid-configuration'))
        if (discovery.get('issuer') != config['issuer']
                or discovery.get('authorization_endpoint') != config['auth_origin'] + '/application/o/authorize/'):
            raise ValueError('Issuer discovery differs from selected environment')
        with request(staff + '/api/staff/login') as initial:
            target = initial.geturl()
        if (endpoint(target) != config['auth_origin'] or not redirects.authorization
                or urllib.parse.urlsplit(target).path != '/if/flow/' + config['flow'] + '/'):
            raise ValueError('Login did not reach the configured authentication flow')
        api = config['auth_origin'] + '/api/v3/flows/executor/' + config['flow'] + '/?' + urllib.parse.urlencode({
            'query': urllib.parse.urlsplit(target).query})
        response = json_response(request(api))
        for _ in range(8):
            component = response.get('component')
            if response.get('type') == 'redirect' or component == 'xak-flow-redirect':
                destination = urllib.parse.urljoin(config['auth_origin'], response['to'])
                with request(destination) as final:
                    if (final.status != 200 or endpoint(final.geturl()) != staff
                            or urllib.parse.urlsplit(final.geturl()).path in ('/login', '/staff/login')):
                        raise ValueError('Callback did not reach staff application')
                break
            if component == 'ak-stage-identification':
                data = {'uid_field': secret['username'], 'password': secret['password']}
            elif component == 'ak-stage-password': data = {'password': secret['password']}
            elif component == 'ak-stage-consent': data = {'consent': True}
            else: raise ValueError('Unexpected synthetic authentication stage')
            response = json_response(request(api, data))
        else: raise ValueError('Authentication flow did not finish')
        cookie = next((c for c in jar if c.name == config['session_cookie'] and c.secure
                       and c.domain.lstrip('.') == urllib.parse.urlsplit(staff).hostname), None)
        if not redirects.callback or cookie is None: raise ValueError('Staff session missing')
        session = cookie.value
        with request(staff + READ) as read:
            if read.status != 200: raise ValueError('Protected read failed')
        # No id is supplied. If role enforcement regresses, the application
        # rejects this request before any content update can execute.
        try:
            with request(staff + READ, {}, method='PATCH'): pass
        except urllib.error.HTTPError as error:
            error.close()
            if error.code != 403: raise ValueError('Synthetic identity is not read-only') from None
        else: raise ValueError('Synthetic role boundary failed')
        with request(staff + '/api/staff/logout') as logout:
            if logout.status != 200: raise ValueError('Logout failed')
        try:
            with request(staff + READ, cookie=session): pass
        except urllib.error.HTTPError as error:
            error.close()
            if error.code != 401: raise ValueError('Logged-out session was not rejected') from None
        else: raise ValueError('Logged-out session still works')
        session = None
        return True
    finally:
        if session:
            try:
                with request(staff + '/api/staff/logout'): pass
            except Exception: pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError('Unexpected revocation redirect')


def atomic(path, content, mode):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.probe-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), mode); stream.write(content); stream.flush()
            os.fsync(stream.fileno()); temporary.replace(path)
        finally: temporary.unlink(missing_ok=True)


def publish(directory, environment, success, now=None):
    if environment not in ('test', 'production'): raise ValueError('Invalid metrics environment')
    directory = Path(directory)
    now = time.time() if now is None else now
    previous = {}
    try:
        previous = json.loads((directory / 'state.json').read_text())
        if (previous['environment'] != environment
                or any(type(previous[key]) not in (int, float) or not math.isfinite(previous[key]) or previous[key] < 0
                       for key in ('last_success', 'failures')) or previous['last_success'] > now):
            previous = {}
    except (ValueError, KeyError, OSError, TypeError): previous = {}
    state = dict(environment=environment, last_success=now if success else previous.get('last_success', 0),
                 failures=0 if success else min(previous.get('failures', 0) + 1, 1000000))
    atomic(directory / 'state.json', json.dumps(state) + '\n', 0o600)
    values = {'success': int(success), 'last_run_timestamp_seconds': now,
              'last_success_timestamp_seconds': state['last_success'], 'consecutive_failures': state['failures']}
    atomic(directory / 'staff-login.prom', ''.join('hou1_staff_probe_' + key + '{probe_environment="' + environment
           + '"} ' + str(value) + '\n' for key, value in values.items()), 0o644)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--credentials')
    parser.add_argument('--output')
    parser.add_argument('--check-config', action='store_true')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    try:
        import sys
        config = validate(json.load(sys.stdin) if args.config == '-' else json.loads(Path(args.config).read_text()))
        if args.check_config:
            print('{"valid":true}'); return 0
        if not args.credentials or not args.output: raise ValueError('Missing private runtime files')
        while True:
            started = time.monotonic(); passed = False
            try:
                secret = credentials(json.loads(Path(args.credentials).read_text()), config['environment'])
                passed = probe(config, secret)
            except Exception: pass
            publish(args.output, config['environment'], passed)
            print(json.dumps({'environment': config['environment'], 'routine_login_probe': 'passed' if passed else 'failed'}), flush=True)
            if args.once: return 0 if passed else 1
            time.sleep(max(1, 60 - (time.monotonic() - started)))
    except Exception:
        print('Staff probe configuration or status publication failed; private diagnostics withheld.', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
