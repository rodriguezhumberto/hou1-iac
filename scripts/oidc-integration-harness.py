#!/usr/bin/env python3
"""Run a synthetic OIDC model matrix and live discovery/reachability checks.

The local model does not exercise deployed callback/token validation. It cannot
establish security readiness, key rotation, session revocation or drill success.
Use the live security probes and recorded drill assertions for those claims.
"""
import argparse
import json
import os
import time
import urllib.parse
import urllib.request


class Rejected(Exception):
    pass


def validate_claims(claims, expected_issuer, expected_audience, *, signature_valid=True, state_valid=True,
                    nonce_valid=True, pkce_valid=True, groups_valid=True, now=None):
    now = int(time.time()) if now is None else now
    if not signature_valid:
        raise Rejected('signature')
    if claims.get('iss') != expected_issuer:
        raise Rejected('issuer')
    audience = claims.get('aud')
    if audience != expected_audience and audience != [expected_audience]:
        raise Rejected('audience')
    if claims.get('exp', 0) <= now - 60:
        raise Rejected('expiry')
    if claims.get('nbf', now) > now + 60:
        raise Rejected('nbf')
    if claims.get('iat', now) > now + 60:
        raise Rejected('clock_skew')
    if not state_valid:
        raise Rejected('state')
    if not nonce_valid:
        raise Rejected('nonce')
    if not pkce_valid:
        raise Rejected('pkce')
    if not groups_valid or not isinstance(claims.get('groups'), list) or not claims['groups']:
        raise Rejected('groups')
    return True


def failure_matrix(now=None):
    now = int(time.time()) if now is None else now
    base = {'iss': 'https://auth-test.example/application/o/netbird-test/', 'aud': 'netbird-test',
            'exp': now + 300, 'iat': now, 'nbf': now - 1, 'groups': ['pearland-paws-staff-reader']}
    expected = base['iss']
    cases = {
        'invalid_issuer': {'claims': {**base, 'iss': 'https://evil.invalid/'}, 'kwargs': {}},
        'invalid_audience': {'claims': {**base, 'aud': 'wrong'}, 'kwargs': {}},
        'invalid_signature': {'claims': base, 'kwargs': {'signature_valid': False}},
        'expired': {'claims': {**base, 'exp': now - 61}, 'kwargs': {}},
        'not_before': {'claims': {**base, 'nbf': now + 61}, 'kwargs': {}},
        'state_replay': {'claims': base, 'kwargs': {'state_valid': False}},
        'nonce_mismatch': {'claims': base, 'kwargs': {'nonce_valid': False}},
        'pkce_failure': {'claims': base, 'kwargs': {'pkce_valid': False}},
        'jwks_rotation': {'claims': base, 'kwargs': {'signature_valid': True}, 'expect_reject': False},
        'missing_groups': {'claims': {**base, 'groups': []}, 'kwargs': {}},
        'clock_skew': {'claims': {**base, 'iat': now + 61}, 'kwargs': {}},
        'revocation': {'claims': base, 'kwargs': {'state_valid': False}},
    }
    results = {}
    for name, case in cases.items():
        try:
            validate_claims(case['claims'], expected, 'netbird-test', now=now, **case['kwargs'])
        except Rejected:
            results[name] = 'passed' if case.get('expect_reject', True) else 'failed'
        else:
            results[name] = 'failed' if case.get('expect_reject', True) else 'passed'
    return results


def get_json(url):
    request = urllib.request.Request(url, headers={'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


def live_checks(issuer, netbird_url):
    issuer_url = urllib.parse.urlsplit(issuer)
    if issuer_url.scheme != 'https' or not issuer_url.netloc:
        raise ValueError('test issuer must be HTTPS')
    discovery = get_json(f'{issuer}.well-known/openid-configuration')
    if discovery.get('issuer') != issuer or not discovery.get('jwks_uri'):
        raise ValueError('discovery issuer or JWKS is invalid')
    jwks = get_json(discovery['jwks_uri'])
    if not isinstance(jwks.get('keys'), list) or not jwks['keys']:
        raise ValueError('JWKS is empty')
    instance = get_json(netbird_url.rstrip('/') + '/api/instance')
    return {'discovery': 'passed', 'jwks': 'passed', 'netbird_api': 'passed', 'netbird_instance_keys': sorted(instance)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--issuer', required=True)
    parser.add_argument('--audience', required=True)
    parser.add_argument('--netbird-url', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--outage-evidence')
    parser.add_argument('--restore-evidence')
    parser.add_argument('--rollback-evidence')
    args = parser.parse_args()
    if os.environ.get('OIDC_TEST_ENVIRONMENT') != 'test':
        raise SystemExit('OIDC_TEST_ENVIRONMENT must be test')
    for name, value in {'issuer': args.issuer, 'netbird_url': args.netbird_url}.items():
        if 'prod' in value or 'production' in value:
            raise SystemExit(f'production {name} is forbidden in the isolated test harness')
    failures = failure_matrix()
    if any(result != 'passed' for result in failures.values()):
        raise SystemExit('one or more automated failure-mode assertions failed')
    live = {'status': 'skipped'} if args.offline else live_checks(args.issuer, args.netbird_url)
    drills = {}
    for name, path in [('outage', args.outage_evidence), ('restore', args.restore_evidence), ('rollback', args.rollback_evidence)]:
        drills[name] = 'evidence_present_unverified' if path and os.path.isfile(path) and os.path.getsize(path) else 'missing'
    report = {'environment': 'test', 'issuer': args.issuer, 'audience': args.audience,
              'automated_failure_modes': failures, 'failure_mode_target': 'synthetic_local_model',
              'security_readiness_established': False, 'live_checks': live, 'drills': drills,
              'refs': {'netbird_version': '0.82.0', 'test_client': os.environ.get('OIDC_TEST_CLIENT_ID', 'netbird-test'),
                       'certificate_ref': os.environ.get('OIDC_TEST_CERT_REF', 'recorded-out-of-band'),
                       'secret_ref': os.environ.get('OIDC_TEST_SECRET_REF', 'sops-age-test')},
              'recorded_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    with open(args.report, 'w', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write('\n')


if __name__ == '__main__':
    main()
