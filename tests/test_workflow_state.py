import hashlib
import json

from support import WorkspaceTest
from tools import workflow_state as ws


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class WorkflowStateTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        source = self.work / 'topic.md'
        source.write_text('synthetic topic', encoding='utf-8')
        self.definition = {
            'schema_version': 1,
            'workflow_type': 'market',
            'workflow_version': '1.0',
            'target': 'synthetic market scope',
            'linked_ids': {'query_id': 'Q-synthetic'},
            'inputs': [{'name': 'topic', 'kind': 'file', 'reference': 'topic.md',
                        'digest': sha(b'synthetic topic')},
                       {'name': 'rubric', 'kind': 'value', 'reference': '1.2.0',
                        'digest': ws.value_digest('1.2.0')}],
            'steps': [
                {'step_id': 'plan', 'depends_on': [], 'input_names': ['topic']},
                {'step_id': 'retrieve', 'depends_on': ['plan'], 'input_names': ['topic']},
                {'step_id': 'merge', 'depends_on': ['retrieve'], 'input_names': ['rubric']},
            ],
            'next_action': 'Start plan',
            'unresolved_questions': [],
        }

    def initialize(self):
        checkpoint, _ = ws.initialize(self.work, self.definition)
        return checkpoint['workflow_id']

    def receipt(self, operation_id, output='plan.json'):
        path = self.work / output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"ok": true}', encoding='utf-8')
        return {'operation_id': operation_id, 'owner': 'market',
                'committed_at': '2026-09-17T12:00:00+00:00',
                'outputs': [{'name': output.replace('.', '-').replace('/', '-'), 'kind': 'file',
                             'reference': output, 'digest': ws.file_digest(path)}]}

    def complete(self, workflow_id, step_id, output):
        running = ws.start_step(self.work, workflow_id, step_id, None)
        ws.complete_step(self.work, workflow_id, step_id,
                         self.receipt(running['operation_id'], output), 'Continue')

    def test_dependency_order_stable_operations_and_status(self):
        workflow_id = self.initialize()
        with self.assertRaisesRegex(ws.StateError, 'incomplete dependencies'):
            ws.start_step(self.work, workflow_id, 'retrieve', None)
        self.complete(workflow_id, 'plan', 'plan.json')
        running = ws.start_step(self.work, workflow_id, 'retrieve', None)
        self.assertEqual(running['operation_id'], f'{workflow_id}:retrieve:1')
        status = ws.summary(ws.read_checkpoint(self.work, workflow_id))
        self.assertEqual(status['steps']['retrieve']['state'], 'running')
        self.assertEqual(status['eligible_steps'], [])

    def test_changed_input_invalidates_only_dependents_and_descendants(self):
        workflow_id = self.initialize()
        self.complete(workflow_id, 'plan', 'plan.json')
        self.complete(workflow_id, 'retrieve', 'retrieval.json')
        self.complete(workflow_id, 'merge', 'merge.json')
        (self.work / 'topic.md').write_text('changed topic', encoding='utf-8')
        result = ws.invalidate_input(self.work, workflow_id, 'topic', sha(b'changed topic'), 'edited')
        self.assertEqual(set(result['affected_steps']), {'plan', 'retrieve', 'merge'})
        checkpoint = ws.read_checkpoint(self.work, workflow_id)
        self.assertTrue(all(step['state'] == 'invalidated' for step in checkpoint['steps'].values()))

    def test_unrelated_input_change_keeps_completed_steps(self):
        workflow_id = self.initialize()
        self.complete(workflow_id, 'plan', 'plan.json')
        changed = ws.invalidate_input(self.work, workflow_id, 'rubric', ws.value_digest('1.3.0'),
                                      'new rubric', '1.3.0')
        self.assertEqual(changed['affected_steps'], ['merge'])
        checkpoint = ws.read_checkpoint(self.work, workflow_id)
        self.assertEqual(checkpoint['steps']['plan']['state'], 'completed')
        self.assertEqual(checkpoint['steps']['retrieve']['state'], 'pending')

    def test_reconcile_finishes_running_step_from_owner_receipt(self):
        workflow_id = self.initialize()
        receipt_path = self.work / 'receipts' / 'plan.json'
        running = ws.start_step(self.work, workflow_id, 'plan', 'receipts/plan.json')
        receipt = self.receipt(running['operation_id'], 'outputs/plan.json')
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
        result = ws.reconcile(self.work, workflow_id)
        self.assertEqual(result['reconciled_steps'], ['plan'])
        self.assertEqual(ws.read_checkpoint(self.work, workflow_id)['steps']['plan']['state'], 'completed')

    def test_reconcile_invalidates_changed_output_and_downstream(self):
        workflow_id = self.initialize()
        self.complete(workflow_id, 'plan', 'plan.json')
        self.complete(workflow_id, 'retrieve', 'retrieval.json')
        (self.work / 'plan.json').write_text('changed', encoding='utf-8')
        result = ws.reconcile(self.work, workflow_id)
        self.assertEqual(set(result['invalidated_steps']), {'plan', 'retrieve', 'merge'})

    def test_definition_rejects_cycles_and_paths_outside_root(self):
        self.definition['steps'][0]['depends_on'] = ['merge']
        with self.assertRaisesRegex(ws.StateError, 'cycle'):
            ws.initialize(self.work, self.definition)
        self.definition['steps'][0]['depends_on'] = []
        self.definition['inputs'][0]['reference'] = '../topic.md'
        with self.assertRaisesRegex(ws.StateError, 'inside the repository'):
            ws.initialize(self.work, self.definition)

    def test_cli_failure_has_no_traceback(self):
        self.definition['steps'][0]['depends_on'] = ['merge']
        definition = self.write('definition.json', self.definition)
        result = self.run_tool('workflow_state.py', '--root', self.work, 'init',
                               '--definition', definition, ok=False)
        self.assertNotIn('Traceback', result.stderr)
