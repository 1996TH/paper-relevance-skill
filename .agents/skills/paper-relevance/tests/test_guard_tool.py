#!/usr/bin/env python3
"""Execute the configured hook protocol in a fixture host; not a live Claude test."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

SKILL = Path(__file__).resolve().parents[1]


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='paper hook ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.skill = self.root / '.agents/skills/paper-relevance'
        shutil.copytree(SKILL / 'scripts', self.skill / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        settings = json.loads((SKILL / 'assets/claude-settings.json').read_text())
        self.config = settings['hooks']['PreToolUse'][0]
        self.assertEqual(self.config['hooks'][0]['onFailure'], 'block')
        (self.root / '.claude').mkdir()
        (self.root / '.claude/settings.json').write_text(json.dumps(settings))
        self.raw = self.root / 'paper.txt'
        self.raw.write_text('Synthetic source.\nThis is a hook test.\n')
        self.original_hash = hashlib.sha256(self.raw.read_bytes()).hexdigest()
        self.env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root))

    def tearDown(self):
        self.assertTrue(self.raw.exists(), 'The source was deleted.')
        self.assertEqual(hashlib.sha256(self.raw.read_bytes()).hexdigest(), self.original_hash)

    def event(self, tool, **data):
        return {'hook_event_name': 'PreToolUse', 'cwd': str(self.root),
                'tool_name': tool, 'tool_input': data}

    def hook(self, event=None, raw=None):
        return subprocess.run(self.config['hooks'][0]['command'], shell=True,
                              input=json.dumps(event) if raw is None else raw,
                              text=True, capture_output=True, env=self.env, cwd=self.root)

    def dispatch(self, event):
        """Honor the configured matcher and deny before executing fixture operations."""
        self.assertIsNotNone(re.fullmatch(self.config['matcher'], event['tool_name']))
        decision = self.hook(event)
        if decision.returncode != 0:
            return decision, None
        data = event['tool_input']
        if event['tool_name'] == 'Write':
            path = Path(data['file_path'])
            path = path if path.is_absolute() else self.root / path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data['content'])
            return decision, path
        if event['tool_name'] == 'Bash':
            return decision, subprocess.run(data['command'], shell=True, cwd=self.root,
                                            env=self.env, text=True, capture_output=True)
        self.fail('Unexpected admitted operation.')

    def command(self, script, *args):
        return shlex.join(['python3', '-E', '-s', '-B',
                           str(self.skill / 'scripts' / script), *map(str, args)])

    def assertBlocked(self, event):
        result, operation = self.dispatch(event)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('BLOCKED:', result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertIsNone(operation)

    def test_configured_hook_permits_only_new_run_output(self):
        decision, output = self.dispatch(self.event('Write', file_path='runs/run1/review.json', content='{}'))
        self.assertEqual(decision.returncode, 0, decision.stderr)
        self.assertEqual(decision.stdout, '')
        self.assertEqual(output.read_text(), '{}')
        self.assertBlocked(self.event('Write', file_path=str(output), content='changed'))
        self.assertEqual(output.read_text(), '{}')

    def test_source_and_config_writes_are_blocked(self):
        for path in ('paper.txt', '.claude/settings.json', '.agents/skills/paper-relevance/scripts/new.py'):
            with self.subTest(path=path):
                self.assertBlocked(self.event('Write', file_path=path, content='replace'))

    def test_edits_and_delete_shell_are_blocked_before_effect(self):
        for tool in ('Edit', 'MultiEdit', 'NotebookEdit'):
            with self.subTest(tool=tool):
                self.assertBlocked(self.event(tool, file_path=str(self.raw), old_string='Synthetic', new_string='Forged'))
        self.assertBlocked(self.event('Bash', command='rm ' + shlex.quote(str(self.raw))))

    def test_path_traversal_outside_and_symlinks_are_blocked(self):
        (self.root / 'runs').mkdir()
        (self.root / 'runs/link').symlink_to(self.root, target_is_directory=True)
        (self.root / 'runs/file-link').symlink_to(self.raw)
        for path in ('runs/../paper.txt', 'runs/link/new.txt', 'runs/file-link', '/outside.txt'):
            with self.subTest(path=path):
                self.assertBlocked(self.event('Write', file_path=path, content='replace'))

    def test_shell_expansion_chaining_and_interpreters_are_blocked(self):
        good = self.command('prepare_source.py', self.raw, '--output', 'runs/source.json')
        bad_commands = [good + '; echo attack', good + ' > runs/log.txt',
                        good + ' && echo attack', good + ' $(echo attack)',
                        good + ' `echo attack`', good + '\necho attack',
                        good.replace('python3 -E -s -B', 'python3'),
                        'python3 -E -s -B -c "print(1)"',
                        'env ' + good, good + ' # comment']
        for command in bad_commands:
            with self.subTest(command=command):
                self.assertBlocked(self.event('Bash', command=command))

    def test_untrusted_script_and_output_option_variants_are_blocked(self):
        fake = self.root / 'fake.py'
        fake.write_text('raise SystemExit(0)\n')
        commands = [shlex.join(['python3', '-E', '-s', '-B', str(fake)]),
                    self.command('prepare_source.py', self.raw, '--output', 'paper.txt'),
                    self.command('prepare_source.py', self.raw, '--output=runs/a.json'),
                    self.command('prepare_source.py', self.raw, '--output', 'runs/a.json', '--output', 'runs/b.json'),
                    self.command('prepare_source.py', self.raw, '--out', 'runs/a.json'),
                    self.command('prepare_source.py', self.raw, '--output', 'runs/a.json', '--scope', 'invented')]
        for command in commands:
            with self.subTest(command=command):
                self.assertBlocked(self.event('Bash', command=command))

    def test_bad_hook_events_fail_closed(self):
        for raw in ('{', '[]', '{"cwd":1,"cwd":2}', '{"number":NaN}',
                    json.dumps(self.event('Unknown', file_path='runs/new')),
                    json.dumps(dict(self.event('Write', file_path='runs/new', content='x'), hook_event_name='PostToolUse')),
                    json.dumps(dict(self.event('Write', file_path='runs/new', content='x'), cwd='/'))):
            with self.subTest(raw=raw):
                result = self.hook(raw=raw)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn('BLOCKED:', result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_missing_root_and_oversize_input_fail_closed(self):
        bad_env = dict(self.env, CLAUDE_PROJECT_DIR=str(self.root / 'missing'))
        result = subprocess.run(['python3', '-I', '-B', str(self.skill / 'scripts/guard_tool.py'),
                                 '--project-root', bad_env['CLAUDE_PROJECT_DIR']],
                                input=json.dumps(self.event('Write', file_path='runs/new', content='x')),
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        result = self.hook(raw=' ' * (2 * 1024 * 1024 + 1))
        self.assertEqual(result.returncode, 2)

    def test_configured_hook_failure_does_not_dispatch_operation(self):
        (self.skill / 'scripts/guard_tool.py').unlink()
        decision, operation = self.dispatch(self.event('Write', file_path='runs/not-created.txt', content='x'))
        self.assertNotEqual(decision.returncode, 0)
        self.assertIsNone(operation)
        self.assertFalse((self.root / 'runs/not-created.txt').exists())

    def test_allowed_prepare_check_render_pipeline_and_existing_output(self):
        source = self.root / 'runs/source.json'
        decision, process = self.dispatch(self.event('Bash', command=self.command(
            'prepare_source.py', self.raw, '--output', source, '--scope', 'full_text')))
        self.assertEqual(decision.returncode, 0, decision.stderr)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        match = re.search(r'^SOURCE_PACKET_SHA256: ([0-9a-f]{64})$', process.stdout, re.MULTILINE)
        self.assertIsNotNone(match, 'The guarded workflow needs the prepared packet hash without a separate command.')
        packet_hash = match.group(1)
        self.assertEqual(packet_hash, hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertBlocked(self.event('Bash', command=self.command('prepare_source.py', self.raw, '--output', source)))
        review = {'schema_version': 1, 'status': 'complete',
                  'paper': {'title': 'Hook fixture', 'authors': [], 'year': None, 'identifier': None},
                  'source_packet_sha256': packet_hash,
                  'reading': {'scope': 'full_text', 'pages_read': [1], 'visual_pages': []},
                  'research_context': 'Test the hook contract.',
                  'relevance': {'level': '부분', 'reason': 'Synthetic fixture.'},
                  'summary': {'problem': 'Test source', 'method': 'Read the source'},
                  'claims': [{'id': 'C1', 'statement': 'The source identifies itself as synthetic.',
                              'evidence': [{'page': 1, 'start_line': 1, 'end_line': 1, 'quote': 'Synthetic source.'}],
                              'conditions': 'Fixture only.'}],
                  'connections': [], 'limitations': ['Not a scientific finding.'],
                  'next_action': 'Inspect the observed tool decisions.',
                  'semantic_review': {'mode': 'self', 'checked_claim_ids': ['C1'], 'notes': 'Fixture text checked.'}}
        result_path = self.root / 'runs/review.json'
        self.dispatch(self.event('Write', file_path=str(result_path), content=json.dumps(review)))
        for script in ('check_result.py', 'render_report.py'):
            args = ['--result', result_path, '--source', source]
            if script == 'render_report.py':
                args += ['--output', self.root / 'runs/report.md']
            decision, process = self.dispatch(self.event('Bash', command=self.command(script, *args)))
            self.assertEqual(decision.returncode, 0, decision.stderr)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue((self.root / 'runs/report.md').is_file())
        self.assertFalse(list(self.skill.rglob('__pycache__')))


if __name__ == '__main__':
    unittest.main()
