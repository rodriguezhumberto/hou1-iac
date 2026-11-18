"""Real Git bundle imports for isolated test release updates and rollback."""

import grp
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import unittest

import yaml

from helpers import ROLES, run_play


class TestApplicationSourceTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = self.root / 'source'
        self.remote = self.root / 'installed'
        self.bundle = self.root / 'selected.bundle'
        self.git('init', '-q', '--initial-branch=release', str(self.source))
        (self.source / 'content').write_text('first')
        self.git('-C', str(self.source), 'add', '.')
        self.first = self.commit('first')
        self.bundle_revision()
        tasks = (ROLES / 'test_application_source/tasks/main.yml').read_text()
        self.tasks = yaml.safe_load(tasks.replace('/opt/hou1-test-source', str(self.remote)))
        for task in self.tasks:
            for module in ('ansible.builtin.file', 'ansible.builtin.copy'):
                if module in task:
                    task[module].update(owner=pwd.getpwuid(os.getuid()).pw_name,
                                        group=grp.getgrgid(os.getgid()).gr_name)
        self.variables = dict(cms_environment='test', netbird_subnet='100.91.0.0/16',
            app_repo_pat=str(self.remote / 'staff.git'), rehearsal_staff_bundle=str(self.bundle),
            rehearsal_staff_bundle_ref='refs/heads/release', rehearsal_staff_ref=self.first)

    def git(self, *args):
        return subprocess.check_output(['git', *args], text=True, stderr=subprocess.DEVNULL).strip()

    def commit(self, message):
        self.git('-C', str(self.source), '-c', 'user.name=Fixture', '-c',
                 'user.email=fixture@example.invalid', 'commit', '-qam', message)
        return self.git('-C', str(self.source), 'rev-parse', 'HEAD')

    def bundle_revision(self):
        self.bundle.unlink(missing_ok=True)
        self.git('-C', str(self.source), 'bundle', 'create', str(self.bundle), 'refs/heads/release')

    def apply(self, variables=None, group='test_env'):
        inventory = {'all': {'children': {group: {'hosts': {'localhost': {}}}}}}
        return run_play({**self.variables, **(variables or {})}, self.tasks, inventory_source=inventory)

    def selected(self):
        return self.git('--git-dir=' + str(self.remote / 'staff.git'), 'rev-parse', 'refs/heads/rehearsal')

    def test_initial_import_updated_release_noop_and_explicit_rollback(self):
        first = self.apply()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(self.selected(), self.first)
        repeated = self.apply()
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
        self.assertRegex(repeated.stdout, r'changed=0\s')
        (self.source / 'content').write_text('second')
        second = self.commit('second'); self.bundle_revision()
        updated = self.apply({'rehearsal_staff_ref': second})
        self.assertEqual(updated.returncode, 0, updated.stdout + updated.stderr)
        self.assertEqual(self.selected(), second)
        self.git('-C', str(self.source), 'reset', '--hard', self.first); self.bundle_revision()
        rollback = self.apply()
        self.assertEqual(rollback.returncode, 0, rollback.stdout + rollback.stderr)
        self.assertEqual(self.selected(), self.first)

    def test_wrong_revision_never_changes_the_selected_ref(self):
        initial = self.apply()
        self.assertEqual(initial.returncode, 0, initial.stdout + initial.stderr)
        (self.source / 'content').write_text('second')
        self.commit('second'); self.bundle_revision()
        rejected = self.apply()
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(self.selected(), self.first)

    def test_target_and_input_guards_run_before_source_files_are_written(self):
        for changes, group in [({'cms_environment': 'production'}, 'test_env'), ({}, 'prod_env'),
                               ({'netbird_subnet': '100.90.0.0/16'}, 'test_env'),
                               ({'app_repo_pat': str(self.root / 'other.git')}, 'test_env'),
                               ({'rehearsal_staff_bundle_ref': '--all'}, 'test_env')]:
            with self.subTest(changes=changes, group=group):
                rejected = self.apply(changes, group)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertFalse(self.remote.exists())

    def test_bootstrap_and_webstack_share_the_source_owner_before_checkout(self):
        application = yaml.safe_load((ROLES / 'webstack/tasks/application.yml').read_text())
        owner = next(i for i, task in enumerate(application)
                     if task.get('ansible.builtin.include_role', {}).get('name') == 'test_application_source')
        checkout = next(i for i, task in enumerate(application) if 'git' in task)
        self.assertLess(owner, checkout)
        self.assertIn("rehearsal_staff_bundle | default('')", application[owner]['when'])
        bootstrap = yaml.safe_load((ROLES.parent / 'rehearse-vps.yml').read_text())[0]
        self.assertTrue(any(task.get('ansible.builtin.include_role', {}).get('name') == 'test_application_source'
                            for task in bootstrap['pre_tasks']))
