"""Offline checks for the guarded lab failure-matrix runner."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

from helpers import ROOT


SPEC = importlib.util.spec_from_file_location('lab_failure_matrix', ROOT / 'scripts/lab-failure-matrix.py')
matrix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(matrix)


def config():
    return {'hosts': {
        'hou1': {'compose': [
            {'directory': '/srv/application', 'services': ['backend']},
            {'directory': '/srv/identity', 'project': 'identity-test', 'file': 'compose.yml'}]},
        'hou4': {'compose': [
            {'directory': '/srv/monitoring', 'project': 'monitoring-test', 'file': 'compose.yml'}]}}}


def declarations():
    return {
        'application': {'host': 'hou1', 'compose_index': 0, 'service': 'backend'},
        'monitoring': {'host': 'hou4', 'compose_index': 0, 'service': 'prometheus'},
        'dependency': {'host': 'hou1', 'compose_index': 1, 'service': 'identity',
                       'probe_query': 'probe_success{job="identity"}',
                       'readiness': {'port': 9090, 'path': '/-/ready'}},
    }


class FailureMatrixTest(unittest.TestCase):
    def test_matrix_requires_the_expected_host_and_project_contract(self):
        matrix.validate_matrix(config(), declarations())
        invalid = declarations()
        invalid['monitoring']['host'] = 'hou1'
        with self.assertRaises(matrix.MatrixError):
            matrix.validate_matrix(config(), invalid)

    def test_matrix_rejects_ambiguous_probe_and_readiness_inputs(self):
        invalid = declarations()
        invalid['dependency']['probe_query'] = 'probe\nvalue'
        with self.assertRaises(matrix.MatrixError):
            matrix.validate_matrix(config(), invalid)
        invalid = declarations()
        invalid['dependency']['readiness']['port'] = 0
        with self.assertRaises(matrix.MatrixError):
            matrix.validate_matrix(config(), invalid)

    def test_compose_shell_keeps_directory_and_arguments_as_separate_values(self):
        project = {'directory': '/srv/test root', 'project': 'monitoring-test', 'file': 'compose.yml'}
        command = matrix.compose_shell(project, ['stop', '--timeout', '30', 'prometheus'])
        self.assertEqual(command, "cd '/srv/test root' && docker compose --project-name monitoring-test "
                         "--file compose.yml stop --timeout 30 prometheus")

    def test_probe_value_requires_one_numeric_series(self):
        self.assertEqual(matrix.probe_value({'data': {'result': [{'value': [1, '0']}]}}), 0)
        for payload in ({'data': {'result': []}}, {'data': {'result': [{}, {}]}}):
            with self.subTest(payload=payload), self.assertRaises(matrix.MatrixError):
                matrix.probe_value(payload)

    def test_scenario_restores_a_stopped_service_after_verification_failure(self):
        runner = object.__new__(matrix.Matrix)
        runner.cleanup = []
        runner.steps = []
        runner.record = Mock()
        runner.stop = Mock()
        runner.restore = Mock()
        with self.assertRaisesRegex(RuntimeError, 'verification'):
            runner.scenario('application', lambda: None, lambda: (_ for _ in ()).throw(RuntimeError('verification')))
        runner.stop.assert_called_once_with('application')
        runner.restore.assert_called_once_with('application')
        self.assertEqual(runner.cleanup, [])


if __name__ == '__main__':
    unittest.main()
