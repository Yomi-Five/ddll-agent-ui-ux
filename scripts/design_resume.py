#!/usr/bin/env python3
"""Load the design skill for a registered Codex chat after resume or compaction."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile


SCRIPT = Path(__file__).resolve()
SKILL = SCRIPT.parent.parent / 'SKILL.md'
HISTORY_BYTES = 8 * 1024 * 1024
FIGMA_TOOL = re.compile(r'^(?:functions\.)?mcp__figma(?:_console)?__')
FIGMA_CALL = re.compile(r'\btools\.mcp__figma(?:_console)?__[A-Za-z0-9_]+\s*\(')


def state_root():
    return Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'design-skill-state'


def session_path(directory, session_id):
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 256:
        raise ValueError('Missing or invalid session_id; pass --session-id or use CODEX_THREAD_ID.')
    return directory / (hashlib.sha256(session_id.encode('utf-8')).hexdigest() + '.json')


@contextmanager
def session_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_state(path, session_id):
    if not path.exists():
        return {}
    state = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(state, dict) or state.get('session_id') != session_id:
        raise ValueError('Design recovery state does not match this chat.')
    return state


def write_state(path, state):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.stem + '.', delete=False) as output:
            temporary = Path(output.name)
            json.dump(state, output, ensure_ascii=False, indent=2)
            output.write('\n')
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def executable_code(arguments):
    try:
        parsed = json.loads(arguments)
    except ValueError:
        parsed = arguments
    code = parsed.get('code', '') if isinstance(parsed, dict) else parsed
    if not isinstance(code, str):
        return ''
    result = []
    index = 0
    while index < len(code):
        char = code[index]
        if char in ('"', "'", '`'):
            quote = char
            index += 1
            while index < len(code):
                if code[index] == '\\':
                    index += 2
                elif code[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
            result.append(' ')
        elif code.startswith('//', index):
            end = code.find('\n', index)
            index = len(code) if end < 0 else end + 1
            result.append(' ')
        elif code.startswith('/*', index):
            end = code.find('*/', index + 2)
            index = len(code) if end < 0 else end + 2
            result.append(' ')
        else:
            result.append(char)
            index += 1
    return ''.join(result)


def has_figma_history(transcript):
    """Best-effort enrollment for old chats; transcript schema is not a stable API."""
    if not isinstance(transcript, str) or not transcript:
        return False
    try:
        with Path(transcript).open('rb') as source:
            size = source.seek(0, os.SEEK_END)
            start = max(0, size - HISTORY_BYTES)
            source.seek(start)
            if start:
                source.readline()
            for line in source:
                try:
                    item = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(item, dict):
                    continue
                call = item.get('payload', item)
                if not isinstance(call, dict) or call.get('type') != 'function_call':
                    continue
                name = call.get('name', '')
                if isinstance(name, str) and FIGMA_TOOL.match(name):
                    return True
                arguments = call.get('arguments')
                if name in ('exec', 'functions.exec') and isinstance(arguments, str):
                    if FIGMA_CALL.search(executable_code(arguments)):
                        return True
    except (OSError, ValueError):
        return False
    return False


def context(event_name, message):
    return {'hookSpecificOutput': {'hookEventName': event_name, 'additionalContext': message}}


def registration_hint(session_id):
    command = shlex.join([sys.executable, str(SCRIPT), 'activate', '--session-id', session_id])
    return ('本次会话恢复尚无设计任务标记。若当前历史或已有任务记录表明正在接续设计／评审，'
            '即使用户只说“继续”，执行前也必须读取 ' + str(SKILL) + '，并登记当前聊天：\n'
            + command + ' --record <已有任务记录绝对路径>\n非设计任务无需登记。')


def activate_session(session_id, directory, cwd, record=None):
    path = session_path(directory, session_id)
    record_path = None
    if record is not None:
        record_path = Path(record).expanduser().resolve()
        if not record_path.is_file():
            raise ValueError('Task record must be an existing file; reuse the current task record.')
    with session_lock(path):
        state = read_state(path, session_id)
        state.update({'schema': 1, 'session_id': session_id, 'task_type': 'design',
                      'status': 'active', 'cwd': str(cwd), 'required_skill': 'ddll-agent-ui-ux'})
        if record_path is not None:
            state['record_path'] = str(record_path)
        write_state(path, state)
    return state


def handle_event(event, directory=None, skill_path=None):
    if not isinstance(event, dict):
        raise ValueError('Hook input must be a JSON object.')
    event_name = event.get('hook_event_name')
    if event_name not in ('SessionStart', 'UserPromptSubmit', 'PostToolUse'):
        return {}
    directory = Path(directory) if directory is not None else state_root()
    skill_path = Path(skill_path) if skill_path is not None else SKILL
    session_id = event.get('session_id')
    path = session_path(directory, session_id)
    source = event.get('source')
    is_figma = event_name == 'PostToolUse' and bool(FIGMA_TOOL.match(str(event.get('tool_name', ''))))
    if event_name == 'PostToolUse' and not is_figma:
        return {}
    if event_name == 'SessionStart' and source == 'clear':
        if path.exists():
            with session_lock(path):
                state = read_state(path, session_id)
                state.update({'status': 'inactive', 'start_delivery_pending': False})
                write_state(path, state)
        return {}
    if not path.exists() and not is_figma and not has_figma_history(event.get('transcript_path')):
        if event_name == 'SessionStart' and source in ('resume', 'compact', 'fork'):
            return context(event_name, registration_hint(session_id))
        return {}
    with session_lock(path):
        state = read_state(path, session_id)
        if not state:
            state = {'schema': 1, 'session_id': session_id, 'task_type': 'design',
                     'required_skill': 'ddll-agent-ui-ux', 'status': 'active',
                     'enrolled_by': 'figma_tool' if is_figma else 'figma_history'}
        if is_figma:
            state['status'] = 'active'
        if state.get('status') != 'active':
            return {}
        state['cwd'] = str(event.get('cwd') or state.get('cwd') or '')
        body = skill_path.read_text(encoding='utf-8')
        fingerprint = hashlib.sha256(body.encode('utf-8')).hexdigest()
        turn_id = event.get('turn_id')
        if event_name != 'SessionStart' and turn_id:
            if state.get('start_delivery_pending') and state.get('skill_sha256') == fingerprint:
                state.update({'start_delivery_pending': False, 'last_turn_id': turn_id})
                write_state(path, state)
                return {}
            if state.get('last_turn_id') == turn_id and state.get('skill_sha256') == fingerprint:
                write_state(path, state)
                return {}
        record = state.get('record_path')
        record_note = ('任务记录：' + record + '。先核记录与最后实际写入，只补未完成项。') if record else (
            '尚未关联任务记录；复用本聊天已有记录并用恢复登记命令关联，不另建重复台账。')
        message = ('已为当前聊天自动完整加载 ddll-agent-ui-ux 最新入口，视为本轮已读；'
                   '无需再用工具重复读取入口，仍须读取本轮适用引用。\n技能目录：'
                   + str(skill_path.parent) + '；下文相对引用按此目录解析。\n'
                   + record_note + '\n先核用户当前范围及设计任务是否仍在进行；本记录不新增编辑授权，'
                   '纯评审保持只读。任务已完成／取消或已切换无关任务时，更新恢复状态。\n\n' + body)
        state.update({'skill_sha256': fingerprint,
                      'start_delivery_pending': event_name == 'SessionStart' and source in ('startup', 'resume', 'fork')})
        if turn_id:
            state['last_turn_id'] = turn_id
        write_state(path, state)
        return context(event_name, message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=None)
    subparsers = parser.add_subparsers(dest='action')
    for action in ('activate', 'complete', 'deactivate', 'status'):
        sub = subparsers.add_parser(action)
        sub.add_argument('--session-id', default=os.environ.get('CODEX_THREAD_ID'))
        if action == 'activate':
            sub.add_argument('--record')
    args = parser.parse_args()
    directory = args.state_dir if args.state_dir is not None else state_root()
    event = None
    try:
        if args.action == 'activate':
            result = activate_session(args.session_id, directory, Path.cwd(), args.record)
        elif args.action:
            path = session_path(directory, args.session_id)
            if args.action == 'status':
                result = read_state(path, args.session_id) or {'status': 'unregistered'}
            elif path.exists():
                with session_lock(path):
                    result = read_state(path, args.session_id)
                    result.update({'status': 'completed' if args.action == 'complete' else 'inactive',
                                   'start_delivery_pending': False})
                    write_state(path, result)
            else:
                result = {'status': 'unregistered'}
        else:
            event = json.load(sys.stdin)
            result = handle_event(event, directory)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, TypeError) as error:
        if args.action:
            print(str(error), file=sys.stderr)
            return 1
        result = {'systemMessage': '设计恢复 Hook 未完成：' + str(error)}
        if isinstance(event, dict) and event.get('hook_event_name') in ('SessionStart', 'UserPromptSubmit', 'PostToolUse'):
            result.update(context(event['hook_event_name'], '设计恢复自动加载失败；如本轮接续设计，执行前手动完整读取 ' + str(SKILL) + ' 并核对当前任务记录。'))
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
