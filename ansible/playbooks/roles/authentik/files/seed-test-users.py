"""Set explicit test passwords on existing non-superusers without changing grants."""
import json
import sys


def seed_users(records, user_model, atomic):
    if (not isinstance(records, list) or not 1 <= len(records) <= 8
            or any(not isinstance(row, dict) or set(row) != {'username', 'password'}
                   or not isinstance(row['username'], str) or not row['username'].strip()
                   or not isinstance(row['password'], str) or not 32 <= len(row['password']) <= 128
                   for row in records)
            or len({row['username'] for row in records}) != len(records)):
        raise ValueError('Invalid explicit test identities')
    changed = False
    with atomic():
        users = [(user_model.objects.select_for_update().get(username=row['username']), row['password'])
                 for row in records]
        if any(not user.is_active or user.is_superuser for user, _ in users):
            raise ValueError('Test seeding requires active non-superusers')
        for user, password in users:
            if not user.check_password(password):
                user.set_password(password)
                user.save()
                changed = True
    return {'changed': changed, 'users': len(users), 'grants_changed': False}


def main():
    from authentik.core.models import User
    from django.db import transaction
    result = seed_users(json.load(sys.stdin), User, transaction.atomic)
    print('LAB_TEST_IDENTITIES=' + json.dumps(result))
