"""Plugins: every tool besides the built-in ones. A plugin is a group of tools served
by an MCP server the executor runs (executor/upstream.py), with its code in a folder a
person approves:

    DATA_DIR/plugins/<group>/{current,draft}/
        plugin.json         manifest.py: title, the tools (functions) or the command (a server)
        *.py, …             its code and small data
        requirements.txt    the packages it declares
        requirements.lock   the exact packages with hashes (uv pip compile)
        .venv/              its own environment, never part of the hash

and a row of `plugins` each. The plugins the chat and the user write are functions
`run(args, ctx)` our shim serves (shim.py); a ready server from elsewhere starts by its
own command. Either way the same draft, the same approval by hash, the same process.

store.py writes and approves, launch.py starts; the executor is the only writer of the
table and the folders."""
