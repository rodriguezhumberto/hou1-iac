"""Validate explicit website inputs and omit only the optional static-site mounts."""
from copy import deepcopy
import re
from urllib.parse import urlsplit


def hostname(value):
    return (isinstance(value, str) and len(value) <= 253
            and bool(re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', value)))


def site_errors(values):
    errors = []
    secondary = values.get('webstack_secondary_site_enabled', True)
    redirect = values.get('webstack_public_canonical_redirect', False)
    if type(secondary) is not bool or type(redirect) is not bool:
        return ['Website switches must be YAML booleans.']
    try:
        origin = values['public_site_origin']
        parsed = urlsplit(origin)
        canonical = parsed.hostname
        if not hostname(canonical) or origin != 'https://' + canonical:
            raise ValueError()
        public = values.get('public_site_hostnames', [values['primary_domain'], 'www.' + values['primary_domain'], 'test.' + values['primary_domain']])
        staff = values['staff_domain']
        private = {staff}
        if hostname(values.get('netbird_domain')):
            private.add(values['netbird_domain'])
        for profile in values.get('authentik_identity_profiles', {}).values():
            if hostname(profile.get('issuer_host')):
                private.add(profile['issuer_host'])
        if (not isinstance(public, list) or not public or len(set(public)) != len(public)
                or not all(hostname(host) for host in public) or not hostname(staff)
                or private & set(public)):
            raise ValueError()
        if redirect and ('public_site_hostnames' not in values or canonical not in public):
            raise ValueError()
        if secondary:
            domain = values['secondary_domain']
            hosts = values.get('secondary_site_hostnames', [domain, 'www.' + domain, 'test.' + domain])
            if (not hostname(domain) or not isinstance(hosts, list) or not hosts
                    or not all(hostname(host) for host in hosts)
                    or len(set(hosts)) != len(hosts) or private & set(hosts) or set(hosts) & set(public)):
                raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        errors.append('Require distinct canonical website, staff and enabled secondary hostnames; redirects need an explicit public host list.')
    return errors


def nginx_service(service, enabled):
    result = deepcopy(service)
    if not enabled and 'volumes' in result:
        optional = {'/etc/nginx/templates/secondary.conf.template', '/usr/share/nginx/html/landing'}
        result['volumes'] = [mount for mount in result['volumes']
                             if (mount.get('target') if isinstance(mount, dict)
                                 else mount.split(':')[1] if ':' in mount else mount) not in optional]
    return result


class FilterModule:
    def filters(self):
        return {'webstack_site_errors': site_errors, 'webstack_nginx_service': nginx_service}
