# Hook payload fixtures

One real stdin payload per supported (event, tool) — `PreToolUse` for Edit, Write and Bash, `PostToolUse` for
Edit and Write (`PostToolUse` with Bash is refused: its `tool_response` cannot be synthesized) — captured from
Claude Code **2.1.286** with a throwaway probe hook, then scrubbed: `session_id`, `transcript_path`,
`prompt_id`, `tool_use_id`, `cwd` and the edited path are placeholders, and `duration_ms` is whatever the
probe saw. `scripts/tests/test_run_hooks.py` compares the KEY SETS of `scripts/run-hooks.py`'s synthesized
payloads (top level, `tool_input`, `tool_response`) against these, so the synthesizer cannot drift from
what the harness sends without a test going red.

Refresh when the harness changes: register a hook that copies its stdin to a file under several matchers in
a scratch `--settings` file, then run, from a scratch cwd (the prompt goes BEFORE `--allowedTools`, which is
variadic and swallows it):

    claude -p "<one Write, one Edit, one Bash call>" --model haiku --setting-sources project \
      --settings probe.json --permission-mode bypassPermissions --allowedTools "Write,Edit,Bash" < /dev/null
