"""Shared test scaffolding. Synthetic fixtures only - no real personal or market data, ever."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)

    def write(self, name, value):
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def run_tool(self, name, *args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / 'tools' / name), *map(str, args)],
                                capture_output=True, text=True, encoding='utf-8', cwd=ROOT)
        # subprocess sets a stream to None when it could not decode it as UTF-8, which happens
        # when a tool prints a non-ASCII character in the console code page. That is a real
        # portability bug in the tool (fa_state.safe_console forces UTF-8 to prevent it), so say
        # so rather than failing later with an unrelated TypeError.
        for stream in ('stdout', 'stderr'):
            self.assertIsNotNone(getattr(result, stream),
                                 f'{name} wrote {stream} that is not valid UTF-8 - call '
                                 'fa_state.safe_console() before printing')
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result


def decision_row(**values):
    """A synthetic tracker row that passes validation, for tests to mutate one field at a time."""
    return dict(decision_id='D-001', created_on='2026-09-20', origin='user',
                question='Move idle cash to a money-market UCITS?', decision_type='cash',
                amount_eur='10000.00', instrument='SYNTHETIC-MMF', gate1='PASS', gate2='PASS',
                gate3='PASS', gate4='SUPPORTED', verdict='proceed', score='74', band='do_now',
                status='vetted', market_query_id='Q-synthetic', ips_version='', brief='',
                notes='') | values
