import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name('design_resume.py')
SPEC = importlib.util.spec_from_file_location('design_resume', SCRIPT)
resume = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resume)


class DesignResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.states = self.directory / 'states'
        self.skill = self.directory / 'SKILL.md'
        self.skill.write_text('必须保留组件关联。\n', encoding='utf-8')
        self.record = self.directory / 'progress.md'
        self.record.write_text('已做：页头；下一步：表格。\n', encoding='utf-8')

    def activate(self, session='chat-a'):
        return resume.activate_session(session, self.states, self.directory, self.record)

    def send(self, kind, session='chat-a', **fields):
        return resume.handle_event(dict(hook_event_name=kind, session_id=session,
                                        cwd=str(self.directory), **fields), self.states, self.skill)

    def text(self, output):
        return output.get('hookSpecificOutput', {}).get('additionalContext', '')

    def cli(self, *arguments, event=None):
        result = subprocess.run([sys.executable, str(SCRIPT), '--state-dir', str(self.states),
                                 *arguments], input=json.dumps(event) if event is not None else None,
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_resume_includes_full_latest_entry_and_record(self):
        self.activate()
        output = self.send('SessionStart', source='resume')
        self.assertTrue(self.text(output).endswith(self.skill.read_text()))
        self.assertIn(str(self.record), self.text(output))
        self.assertEqual(self.record.read_text(), '已做：页头；下一步：表格。\n')

    def test_continue_prompt_does_not_need_design_keywords(self):
        self.activate()
        self.assertIn('必须保留组件关联', self.text(self.send('UserPromptSubmit', turn_id='t1', prompt='继续')))

    def test_same_turn_events_only_deliver_once(self):
        self.activate()
        self.assertTrue(self.send('UserPromptSubmit', turn_id='t1', prompt='继续'))
        self.assertEqual(self.send('UserPromptSubmit', turn_id='t1', prompt='继续'), {})
        self.assertEqual(self.send('PostToolUse', turn_id='t1', tool_name='mcp__figma_console__figma_execute'), {})

    def test_new_turn_loads_again(self):
        self.activate()
        self.send('UserPromptSubmit', turn_id='t1', prompt='继续')
        self.assertTrue(self.send('UserPromptSubmit', turn_id='t2', prompt='改一下'))

    def test_compaction_reloads_in_same_turn(self):
        self.activate()
        self.send('UserPromptSubmit', turn_id='t1', prompt='继续')
        self.assertTrue(self.send('SessionStart', source='compact'))
        self.assertTrue(self.send('UserPromptSubmit', turn_id='t2', prompt='继续'))

    def test_resume_and_first_prompt_share_one_load(self):
        self.activate()
        self.assertTrue(self.send('SessionStart', source='resume'))
        self.assertEqual(self.send('UserPromptSubmit', turn_id='t1', prompt='继续'), {})
        self.assertTrue(self.send('UserPromptSubmit', turn_id='t2', prompt='继续'))

    def test_entry_change_invalidates_same_turn_cache(self):
        self.activate()
        self.send('UserPromptSubmit', turn_id='t1', prompt='继续')
        self.skill.write_text('新增的用户规则。\n', encoding='utf-8')
        self.assertIn('新增的用户规则', self.text(self.send('UserPromptSubmit', turn_id='t1', prompt='继续')))

    def test_other_chat_in_same_directory_stays_unregistered(self):
        self.activate()
        self.assertEqual(self.send('UserPromptSubmit', session='chat-b', turn_id='t1', prompt='继续'), {})
        self.assertFalse(resume.session_path(self.states, 'chat-b').exists())

    def test_unrelated_prompt_does_not_create_state(self):
        self.assertEqual(self.send('UserPromptSubmit', turn_id='t1', prompt='写个 Python 函数'), {})
        self.assertFalse(self.states.exists())

    def test_figma_call_registers_only_its_chat(self):
        self.assertTrue(self.send('PostToolUse', tool_name='mcp__figma__get_design_context', turn_id='t1'))
        state = resume.read_state(resume.session_path(self.states, 'chat-a'), 'chat-a')
        self.assertEqual(state['status'], 'active')
        self.assertTrue(self.send('SessionStart', source='resume'))

    def test_non_figma_tool_cannot_register_chat(self):
        self.assertEqual(self.send('PostToolUse', tool_name='Bash', turn_id='t1'), {})
        self.assertFalse(self.states.exists())

    def test_legacy_chat_recovers_from_actual_tool_call(self):
        transcript = self.directory / 'legacy.jsonl'
        transcript.write_text(json.dumps({'type': 'response_item', 'payload': {
            'type': 'function_call', 'name': 'mcp__figma_console__figma_execute', 'arguments': '{}'}}) + '\n')
        self.assertIn('必须保留组件关联', self.text(self.send('SessionStart', source='resume', transcript_path=str(transcript))))

    def test_code_mode_figma_history_is_detected(self):
        transcript = self.directory / 'code-mode.jsonl'
        transcript.write_text(json.dumps({'payload': {'type': 'function_call', 'name': 'exec',
            'arguments': 'await tools.mcp__figma_console__figma_execute({code: "..."});'}}) + '\n')
        self.assertTrue(self.send('SessionStart', source='resume', transcript_path=str(transcript)))

    def test_mentions_and_malformed_history_do_not_register(self):
        transcript = self.directory / 'other.jsonl'
        transcript.write_text('not json\n' + json.dumps({'payload': {'type': 'message',
            'role': 'user', 'content': 'mcp__figma_console__figma_execute'}}) + '\n')
        output = self.send('SessionStart', source='resume', transcript_path=str(transcript))
        self.assertNotIn('必须保留组件关联', self.text(output))
        self.assertIn('尚无设计任务标记', self.text(output))
        self.assertFalse(self.states.exists())

    def test_quoted_code_and_comments_cannot_register_chat(self):
        transcript = self.directory / 'quoted-code.jsonl'
        code = '''await tools.exec_command({cmd: "echo tools.mcp__figma_console__figma_execute("});
        // await tools.mcp__figma_console__figma_execute({});
        /* await tools.mcp__figma_console__figma_execute({}); */'''
        transcript.write_text(json.dumps({'payload': {'type': 'function_call', 'name': 'exec',
                                                       'arguments': code}}) + '\n')
        self.send('SessionStart', source='resume', transcript_path=str(transcript))
        self.assertFalse(self.states.exists())

    def test_json_wrapped_executable_code_is_detected(self):
        transcript = self.directory / 'wrapped-code.jsonl'
        arguments = json.dumps({'code': 'await tools.mcp__figma__get_design_context({});'})
        transcript.write_text(json.dumps({'payload': {'type': 'function_call', 'name': 'exec',
                                                       'arguments': arguments}}) + '\n')
        self.send('SessionStart', source='resume', transcript_path=str(transcript))
        self.assertTrue(resume.session_path(self.states, 'chat-a').exists())

    def test_complete_and_deactivate_stop_automatic_resume(self):
        self.activate()
        self.cli('complete', '--session-id', 'chat-a')
        self.assertEqual(self.send('SessionStart', source='resume'), {})
        self.activate()
        self.cli('deactivate', '--session-id', 'chat-a')
        self.assertEqual(self.send('UserPromptSubmit', turn_id='t1', prompt='继续'), {})

    def test_clear_stops_carrying_old_task(self):
        self.activate()
        self.assertEqual(self.send('SessionStart', source='clear'), {})
        self.assertEqual(self.send('UserPromptSubmit', turn_id='t1', prompt='新话题'), {})

    def test_cli_registers_existing_record_and_handles_real_stdin(self):
        self.cli('activate', '--session-id', 'chat-a', '--record', str(self.record))
        output = self.cli(event={'hook_event_name': 'SessionStart', 'source': 'resume',
                                'session_id': 'chat-a', 'cwd': str(self.directory)})
        self.assertTrue(self.text(output).endswith(resume.SKILL.read_text()))
        self.assertEqual(output['hookSpecificOutput']['hookEventName'], 'SessionStart')

    def test_corrupt_state_fails_with_correct_event_and_manual_fallback(self):
        self.activate()
        resume.session_path(self.states, 'chat-a').write_text('{broken')
        output = self.cli(event={'hook_event_name': 'UserPromptSubmit', 'session_id': 'chat-a',
                                'turn_id': 't1', 'prompt': '继续'})
        self.assertIn('systemMessage', output)
        self.assertEqual(output['hookSpecificOutput']['hookEventName'], 'UserPromptSubmit')
        self.assertIn('手动完整读取', self.text(output))

    def test_missing_entry_does_not_claim_success(self):
        self.activate()
        self.skill.unlink()
        with self.assertRaises(OSError):
            self.send('SessionStart', source='resume')
        state = resume.read_state(resume.session_path(self.states, 'chat-a'), 'chat-a')
        self.assertNotIn('skill_sha256', state)

    def test_rejects_missing_record(self):
        with self.assertRaises(ValueError):
            resume.activate_session('chat-a', self.states, self.directory, self.directory / 'missing.md')
        self.assertFalse(self.states.exists())


if __name__ == '__main__':
    unittest.main()
