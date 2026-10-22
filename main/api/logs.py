"""The tail of the log files (common/service.py), for a client to show without a shell:
agent.log in one process, a file per service otherwise."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request

from main.auth import require_client


router = APIRouter(dependencies=[Depends(require_client)])

# Bytes read from the end per line asked for at first; doubled while too few lines came.
_BYTES_PER_LINE = 256


@router.get("/logs", summary="Last lines of every current log file")
async def logs(request: Request, lines: int = Query(200, ge=1, le=2000)):
    log_dir = request.app.state.settings.storage.log_dir
    files = sorted(log_dir.glob("*.log")) if log_dir.is_dir() else []

    return {"dir": str(log_dir), "files": {path.name: tail(path, lines) for path in files}}


def tail(path: Path, lines: int) -> list[str]:
    with path.open("rb") as file:
        size = file.seek(0, 2)
        block = lines * _BYTES_PER_LINE

        while True:
            start = max(0, size - block)
            file.seek(start)
            data = file.read()

            if start == 0 or data.count(b"\n") > lines:
                break

            block *= 2

    text = data.decode("utf-8", errors="replace").splitlines()

    # Reading from the middle of the file starts mid-line: that line is not whole.
    if start > 0 and text:
        text = text[1:]

    return text[-lines:]
