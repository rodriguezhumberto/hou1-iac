#!/usr/bin/env python3
"""Validate the private independent-ingress recovery contract."""
import json
from pathlib import Path
import re
import sys


PROJECT = re.compile(r'^[a-z0-9][a-z0-9_-]*$')
DIRECTORY = re.compile(r'^/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$')


def project(value):
    if not isinstance(value, dict) or set(value) != {'directory', 'project', 'file'}:
        raise ValueError('invalid project selector')
    if not isinstance(value['directory'], str) or not DIRECTORY.fullmatch(value['directory']):
        raise ValueError('invalid project directory')
    if not isinstance(value['project'], str) or (value['project'] and not PROJECT.fullmatch(value['project'])):
        raise ValueError('invalid project name')
    if not isinstance(value['file'], str) or (value['file'] and not re.fullmatch(r'[A-Za-z0-9_.-]+\.ya?ml', value['file'])):
        raise ValueError('invalid project file')


def validate(value):
    if not isinstance(value, dict) or set(value) != {
            'schema', 'mode', 'application_directory', 'application_project',
            'ingress_project', 'backend_network', 'listener_service', 'rollback'}:
        raise ValueError('unsupported recovery manifest')
    if value['schema'] != 1 or value['mode'] != 'independent':
        raise ValueError('unsupported recovery manifest')
    if not isinstance(value['application_directory'], str) or not DIRECTORY.fullmatch(value['application_directory']):
        raise ValueError('invalid application directory')
    project(value['application_project'])
    project(value['ingress_project'])
    if value['application_project']['directory'] != value['application_directory']:
        raise ValueError('application selector does not match its directory')
    if not isinstance(value['backend_network'], str) or not PROJECT.fullmatch(value['backend_network']):
        raise ValueError('invalid backend network')
    if value['listener_service'] != 'nginx':
        raise ValueError('unsupported listener service')
    rollback = value['rollback']
    if not isinstance(rollback, dict) or set(rollback) != {'stop_independent', 'start_legacy'}:
        raise ValueError('invalid rollback contract')
    ingress = ['docker', 'compose', '--project-directory', value['ingress_project']['directory']]
    if value['ingress_project']['project']:
        ingress += ['--project-name', value['ingress_project']['project']]
    if value['ingress_project']['file']:
        ingress += ['--file', value['ingress_project']['directory'] + '/' + value['ingress_project']['file']]
    if rollback['stop_independent'] != ingress + ['stop', 'nginx']:
        raise ValueError('invalid independent rollback command')
    legacy = ['docker', 'compose', '--project-directory', value['application_directory']]
    if value['application_project']['project']:
        legacy += ['--project-name', value['application_project']['project']]
    if value['application_project']['file']:
        legacy += ['--file', value['application_directory'] + '/' + value['application_project']['file']]
    if rollback['start_legacy'] != legacy + ['--profile', 'integrated-ingress-disabled',
                                             'up', '-d', '--no-deps', 'nginx']:
        raise ValueError('invalid legacy rollback command')
    return value


def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError('manifest path required')
        validate(json.loads(Path(sys.argv[1]).read_text()))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
