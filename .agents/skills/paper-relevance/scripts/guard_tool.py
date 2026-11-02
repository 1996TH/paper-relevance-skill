#!/usr/bin/env python3
"""PreToolUse guard for a dedicated paper-relevance project (not an OS sandbox)."""
import argparse
import json
from pathlib import Path
import shlex
import stat
import sys

MAX_EVENT_BYTES = 2 * 1024 * 1024
SCRIPT_NAMES = ('prepare_source.py', 'check_result.py', 'render_report.py')
# Quoting may group spaces, but no shell expansion, redirection or chaining is needed.
SHELL_META = set('$`\\\n\r\t;&|<>(){}[]*?!~#')


class Denied(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Denied(reason)


def project_path(value, root, cwd, *, output=False, directory=False):
    require(isinstance(value, str) and value.strip() and '\x00' not in value,
            'A nonempty path is required.')
    path = Path(value)
    require('..' not in path.parts and not value.startswith('~'),
            'Parent traversal and home expansion are not permitted.')
    path = path if path.is_absolute() else cwd / path
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise Denied('Path must stay inside the project.') from error
    current = root
    for index, part in enumerate(relative.parts):
        current /= part
        require(not current.is_symlink(), 'Symbolic links are not permitted in tool paths.')
        if current.exists() and index < len(relative.parts) - 1:
            require(current.is_dir(), 'A path parent is not a directory.')
    if output:
        require(len(relative.parts) > 1 and relative.parts[0] == 'runs',
                'New outputs must be inside project/runs/.')
        require(not path.exists(), 'Existing files and directories cannot be overwritten.')
    elif directory:
        require(path.is_dir(), 'Working directory must exist inside the project.')
    else:
        require(path.exists() and stat.S_ISREG(path.stat().st_mode),
                'Input must be an existing regular project file.')
    return path


def script_arguments(tokens, script, root, cwd):
    """Accept only the small, documented CLI; reject abbreviations and duplicates."""
    if script == 'prepare_source.py':
        require(bool(tokens) and not tokens[0].startswith('-'), 'Source input is required.')
        project_path(tokens[0], root, cwd)
        tokens = tokens[1:]
        allowed, required = {'--output', '--origin', '--scope'}, {'--output'}
    else:
        allowed, required = {'--result', '--source'}, {'--result'}
        if script == 'render_report.py':
            allowed, required = allowed | {'--output'}, required | {'--output'}
    require(len(tokens) % 2 == 0, 'Options must be explicit name/value pairs.')
    values = {}
    for option, value in zip(tokens[::2], tokens[1::2]):
        require(option in allowed and option not in values, 'Unknown or duplicate script option.')
        require(bool(value) and not value.startswith('-'), 'An explicit option value is required.')
        values[option] = value
    require(required <= values.keys(), 'Required script option is missing.')
    for option in ('--result', '--source'):
        if option in values:
            project_path(values[option], root, cwd)
    if '--output' in values:
        project_path(values['--output'], root, cwd, output=True)
    if '--scope' in values:
        require(values['--scope'] in ('full_text', 'excerpt', 'abstract_only'),
                'Unknown source scope.')


def check_event(event, project_root):
    require(isinstance(event, dict), 'Hook input must be a JSON object.')
    require(event.get('hook_event_name') == 'PreToolUse', 'Expected a PreToolUse event.')
    root = Path(project_root)
    require(root.is_absolute() and root.is_dir() and not root.is_symlink(),
            'An existing absolute project root is required.')
    root = root.resolve(strict=True)
    cwd = project_path(event.get('cwd'), root, root, directory=True)
    data = event.get('tool_input')
    require(isinstance(data, dict), 'tool_input must be an object.')
    tool = event.get('tool_name')
    if tool == 'Write':
        require(isinstance(data.get('content'), str), 'Write content must be text.')
        project_path(data.get('file_path'), root, cwd, output=True)
    elif tool in ('Edit', 'MultiEdit', 'NotebookEdit'):
        raise Denied('In-place changes are disabled. Write a new version inside runs/.')
    elif tool == 'Bash':
        command = data.get('command')
        require(isinstance(command, str) and command and not any(c in SHELL_META for c in command),
                'Shell expansions, redirection and compound commands are disabled.')
        require(all(ord(c) >= 32 for c in command), 'Control characters are disabled.')
        tokens = shlex.split(command, posix=True)
        # -E ignores PYTHON*; -s excludes user site; -B suppresses bytecode writes.
        # -I would also remove the trusted script directory, breaking sibling imports.
        require(len(tokens) >= 5 and tokens[:4] == ['python3', '-E', '-s', '-B'],
                'Only python3 -E -s -B with an approved script is permitted.')
        script = project_path(tokens[4], root, cwd)
        expected = root / '.agents/skills/paper-relevance/scripts'
        require(script.parent == expected and script.name in SCRIPT_NAMES,
                'Only the three installed paper-relevance scripts may run.')
        script_arguments(tokens[5:], script.name, root, cwd)
    else:
        raise Denied('This guard does not support the requested tool.')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON keys are not permitted.')
        result[key] = value
    return result


def invalid_constant(value):
    raise Denied('Nonstandard JSON number.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True)
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_EVENT_BYTES + 1)
        require(len(raw) <= MAX_EVENT_BYTES, 'Hook input exceeds 2 MiB.')
        event = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                           parse_constant=invalid_constant)
        check_event(event, args.project_root)
    except (Denied, OSError, ValueError, TypeError, RecursionError) as error:
        print(f'BLOCKED: {str(error).encode("utf-8", "backslashreplace").decode("utf-8")}', file=sys.stderr)
        return 2
    # No permissionDecision=allow: ordinary host permissions still apply.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
