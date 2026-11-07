"""An interrupted isolated restore must preserve a cold original and recover it."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('recovery', Path(__file__).resolve().parents[1] / 'scripts/live_oidc/recovery_worker.py')
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class RecoveryWorkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root, self.project, self.data = (self.base / n for n in ('recovery', 'project', 'data'))
        for p in (self.root, self.project, self.data): p.mkdir(mode=0o700)
        for name in worker.FILES: (self.project / name).write_text('original-' + name)
        (self.data / 'database').write_text('consistent cold database')
        for name, value in [('ROOT',self.root),('PROJECT',self.project),('DATA',self.data)]:
            p = patch.object(worker,name,value); p.start(); self.addCleanup(p.stop)

    def test_failed_fresh_restore_recovers_original_without_discarding_failed_state(self):
        def run(command, **kwargs):
            if command[0] == str(self.project / 'restore.sh'):
                self.data.mkdir()
                (self.data / 'database').write_text('failed fresh database')
                raise RuntimeError('controlled restore failure')
            return ''
        with patch.object(worker,'run',side_effect=run), patch.object(worker.subprocess,'run'):
            with self.assertRaisesRegex(RuntimeError,'controlled restore failure'):
                worker.restore()
        self.assertEqual((self.data / 'database').read_text(),'consistent cold database')
        self.assertEqual((self.root / 'failed-restore-data/database').read_text(),'failed fresh database')
        for name in worker.FILES:
            self.assertEqual((self.project / name).read_text(),'original-' + name)
        self.assertTrue((self.root / 'recovered').exists())

    def test_retry_recovers_config_after_original_database_was_already_moved_back(self):
        saved = self.root / 'original-config'; saved.mkdir()
        for name in worker.FILES:
            (saved / name).write_text('saved-' + name)
            (self.project / name).unlink()
        with patch.object(worker,'run',return_value=''):
            worker.recover()
        self.assertEqual((self.data / 'database').read_text(),'consistent cold database')
        self.assertEqual((self.project / 'authentik.env').read_text(),'saved-authentik.env')

    def test_completed_restore_is_never_overwritten_by_late_timer(self):
        (self.root / 'completed').touch()
        with patch.object(worker,'run') as run:
            worker.recover()
        run.assert_not_called()

    def test_environment_guard_rejects_production_before_mutations(self):
        import json
        app = [{'Config':{'Env':['STAFF_ENVIRONMENT=production','STAFF_OIDC_ISSUER=https://auth.example.invalid/']}}]
        with patch.object(worker.os,'geteuid',return_value=0), patch.object(worker,'run',return_value=json.dumps(app)):
            with self.assertRaisesRegex(RuntimeError,'Isolated test deployment'):
                worker.guard()
        self.assertFalse((self.root / 'original-config').exists())


if __name__ == '__main__': unittest.main()
