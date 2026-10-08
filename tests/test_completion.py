"""Completion decisions must not depend on the model's self-reported verdict."""
from __future__ import annotations

import html
import json
import re
import tempfile
import unittest
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from agent_fingerprint.adapters.base_adapter import AgentExecutionError, AgentResult, BaseAgentAdapter
from agent_fingerprint.collection.completion import CompletionResult, CompletionVerifier, VERIFIED_AGENTS
from agent_fingerprint.collection.runner import PipelineRunner


class CompletionVerifierTest(unittest.TestCase):
    def test_unknown_and_pending_are_not_success(self):
        verifier = CompletionVerifier()
        self.assertEqual(verifier.result('run').status, 'unknown')
        token = verifier.begin_document('run', '/index.html')
        verifier.observe('run', token, 1, 'pending')
        self.assertFalse(verifier.result('run').success)
        verifier.observe('run', token, 2, 'passed')
        self.assertTrue(verifier.result('run').success)
        verifier.observe('run', token, 3, 'pending')
        self.assertFalse(verifier.result('run').success)

    def test_retries_navigation_and_runs_cannot_reuse_stale_success(self):
        verifier = CompletionVerifier()
        old = verifier.begin_document('run', '/old.html')
        verifier.observe('run', old, 2, 'passed')
        verifier.observe('run', old, 1, 'pending')
        self.assertTrue(verifier.result('run').success)
        current = verifier.begin_document('run', '/new.html')
        self.assertFalse(verifier.observe('run', old, 3, 'passed'))
        self.assertEqual(verifier.result('run').status, 'unknown')
        self.assertFalse(verifier.observe('other', current, 1, 'passed'))
        self.assertFalse(verifier.result('other').success)
        verifier.observe('run', current, 1, 'passed')
        verifier.clear('run')
        self.assertFalse(verifier.result('run').success)

    def test_zero_exit_without_outcome_is_not_success(self):
        result = AgentResult('fake', (), Path('/tmp'), 0, '', '', 0)
        self.assertTrue(result.execution_success)
        self.assertFalse(result.success)
        result.task_success = True
        self.assertTrue(result.success)
        result.verification = CompletionResult('failed', 'Not submitted')
        self.assertFalse(result.success)
        result.task_success = False
        result.verification = CompletionResult('passed', 'Submitted')
        self.assertTrue(result.success)
        result.returncode = 1
        self.assertFalse(result.success)

    def test_check_raises_for_zero_exit_without_evidence(self):
        class Adapter(BaseAgentAdapter):
            def run_task(self, url, prompt, output_dir):
                return self.execute(self.conda_command(['-c', "print('TASK_COMPLETE')"]),
                                    output_dir=Path(output_dir))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(AgentExecutionError, 'did not report a task outcome'):
                Adapter().run_task('http://example.test', 'finish', directory)


class CompletionPipelineTest(unittest.TestCase):
    def run_case(self, agent, page_status, framework_success, *, returncode=0, root_url=False):
        # Simulate browser transport, not model text: obtain the document token
        # from served HTML and upload its observed completion state over HTTP.
        class Adapter:
            check = True

            def run_task(self, url, prompt, output_dir):
                opener = build_opener(ProxyHandler({}))
                with opener.open(url) as response:
                    document = response.read().decode()
                assert '__AGENT_FINGERPRINT_CONFIG__' not in document
                config = json.loads(html.unescape(re.search(r'data-completion="([^"]+)"', document)[1]))
                if page_status is not None:
                    body = {**config, 'sequence': 1, 'status': page_status}
                    endpoint = url.rsplit('/', 1)[0] + config['endpoint']
                    with opener.open(Request(endpoint, data=json.dumps(body).encode(),
                                             headers={'Content-Type': 'application/json'})) as response:
                        assert json.load(response)['ok']
                path = Path(output_dir)
                path.mkdir(parents=True, exist_ok=True)
                native = {'task_success': framework_success,
                          'task_status': 'completed' if framework_success else 'failed',
                          'output': 'TASK_COMPLETE'}
                (path / 'result.json').write_text(json.dumps(native))
                assert self.check is False
                return AgentResult(agent, (), path, returncode, 'TASK_COMPLETE', '', 0,
                                   task_success=framework_success, task_status=native['task_status'])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'index.html').write_text('<!doctype html><html><head></head><body data-task-status="pending"></body></html>')
            runner = PipelineRunner(output_root=root / 'runs', sandbox_directory=root,
                                    adapter_factory={agent: Adapter}, use_network_probe=False,
                                    inject_monitor=False, collect_l2=False, collect_l3=False,
                                    collect_l4=False, trace_grace_seconds=0)
            result = runner.run_once('/' if root_url else '/index.html', 'Complete the workflow', agent_name=agent)
            manifest = result.manifest
            interactions = json.loads((result.output_dir / manifest['logs']['agent_interactions']).read_text())
            expected = page_status == 'passed'
            self.assertEqual(manifest['outcome']['success'], expected)
            self.assertEqual(manifest['outcome']['framework_success'], framework_success)
            self.assertEqual(result.agent_result.success, expected and returncode == 0)
            self.assertEqual(result.success, expected and returncode == 0)
            if expected:
                self.assertEqual(interactions['final_output']['status'], 'completed')
            else:
                self.assertNotEqual(interactions['final_output']['status'], 'completed')
            self.assertEqual(manifest['execution']['task_success'], framework_success)
            return manifest

    def test_all_five_frameworks_use_page_evidence(self):
        for agent in sorted(VERIFIED_AGENTS):
            for status, claimed in [('passed', False), ('passed', None), ('pending', True), (None, True)]:
                with self.subTest(agent=agent, status=status, claimed=claimed):
                    self.run_case(agent, status, claimed)

    def test_missing_contract_and_execution_failure(self):
        self.run_case('agente', 'unknown', True)
        self.run_case('autogen', 'passed', True, returncode=1)

    def test_directory_index_is_instrumented_without_fingerprint_collection(self):
        self.run_case('webvoyager', 'passed', False, root_url=True)
