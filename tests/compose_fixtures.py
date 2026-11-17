"""Exercise production integration tasks with controller-local file ownership."""
import os

import yaml

from helpers import ROLES


def local_tasks(role, filename='main.yml'):
    tasks = []
    for task in yaml.safe_load((ROLES / role / 'tasks' / filename).read_text()):
        source = task.get('ansible.builtin.import_tasks')
        if isinstance(source, str) and set(task) == {'name', 'ansible.builtin.import_tasks'}:
            tasks.extend(local_tasks(role, source))
            continue
        included = task.get('ansible.builtin.import_role',
                            task.get('ansible.builtin.include_role', {})).get('name')
        if included in {'app_compose', 'shared_ingress'}:
            # Expand the real role tasks; only fixture paths/ownership change.
            tasks.extend(local_tasks(included))
            continue
        for action in ('ansible.builtin.template', 'ansible.builtin.copy', 'ansible.builtin.file'):
            arguments = task.get(action)
            if arguments is not None:
                if action == 'ansible.builtin.template':
                    source = arguments['src'].replace('{{ role_path }}', str(ROLES / role))
                    arguments['src'] = str(ROLES / role / 'templates' / source)
                if 'owner' in arguments:
                    arguments['owner'] = str(os.getuid())
                    arguments['group'] = str(os.getgid())
        tasks.append(task)
    return tasks
