"""Render the real inventory profile and shared identity manifest for fixtures."""
import copy
import json

from jinja2 import Environment, StrictUndefined

from helpers import DEFAULTS, ROLES


def identity_names(environment='test'):
    return {'environment': environment,
            'staff': {'client_id': 'fixture-staff-' + environment,
                      'application_name': 'Fixture Staff ' + environment},
            'role_groups': {role: 'fixture-staff-' + role for role in ('reader', 'editor', 'admin')},
            'scope_mapping_names': {scope: 'Fixture ' + scope + ' ' + environment
                                    for scope in ('profile', 'groups', 'audience')}}


def identity_profile(environment='test', private=True):
    values = {**DEFAULTS, 'primary_domain': 'example.invalid'}
    template = Environment(undefined=StrictUndefined).from_string(
        json.dumps(DEFAULTS['authentik_identity_profiles'][environment]))
    profile = json.loads(template.render(**values))
    if private:
        names = identity_names(environment)
        profile['staff'].update(names['staff'])
        profile['role_groups'] = names['role_groups']
        profile['scope_mapping_names'] = names['scope_mapping_names']
    return profile


def identity_manifest(environment='test', google=False, profile=None):
    profile = copy.deepcopy(profile if profile is not None else identity_profile(environment))
    names = identity_names(environment)
    names['staff'] = {key: profile['staff'][key] for key in ('client_id', 'application_name')}
    names['role_groups'] = profile['role_groups']
    names['scope_mapping_names'] = profile['scope_mapping_names']
    env = Environment(undefined=StrictUndefined)
    env.filters['to_json'] = json.dumps
    return json.loads(env.from_string((ROLES / 'authentik/templates/bootstrap.json.j2').read_text()).render(
        authentik_environment=environment, authentik_identity_profiles={environment: profile},
        authentik_staff_users=[], authentik_google_enabled=google,
        authentik_decrypted={'IDENTITY_NAMES': names, 'GOOGLE_OAUTH_CLIENT_ID': 'fixture.apps.googleusercontent.com',
                             'GOOGLE_ALLOWED_EMAILS': ['allowed@example.invalid']}))
