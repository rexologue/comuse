"""External tools: commands the user adds or the chat writes, outside internal_tools/.

A command is named like a built-in one, `group.action` (telegram.send), in a group of
the user's own; its current code and its draft are a folder each

    DATA_DIR/external_tools/<group>/<action>/{current,draft}/
        tool.json           manifest (manifest.py): title, description, schemas, …
        main.py             `def run(args, ctx)`, plus any other .py files
        requirements.txt    the packages it declares
        requirements.lock   the exact packages with hashes (uv pip compile)
        .venv/              its own environment, never part of the hash

and a row of `external_tools`. A draft is changed in place until a person approves it;
then its files are frozen (their hash is checked before every run) and it takes the
current code's place. There are no versions: a task keeps the hash of the code it was
approved with (common/tools/definition.py), and other code stops it until its user
approves the task with the new one. The executor is the only writer of the table and
the folders.

The code runs in a process of its own (process.py) in its own .venv: it gets its
arguments and context as JSON and answers in JSON, sees no tokens or keys, and is
killed at its timeout. That is not a sandbox: the code can read what the user can.
What makes it safe to run is the person who read and approved it."""

from __future__ import annotations

import re


# `group.action`, like a built-in command: telegram.send. The group is the user's own:
# a built-in one (mail, fs…) belongs to the code.
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}\.[a-z][a-z0-9_]{0,40}$")
