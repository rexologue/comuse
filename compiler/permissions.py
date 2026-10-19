"""Derive a task's permissions from the literal arguments of its steps, using the
`capabilities` each tool declares (`fs.read:{folder}`, `mail.send:{to}`, …).

The user approves the whole task, permissions included, so deriving them here
keeps the LLM from forgetting a folder and the run from failing on it."""

from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath
from typing import Any

from common import templating
from common.pathrefs import DATA_PREFIX, normalize_folder
from common.schemas.task import Permissions, TaskSpec


_CAPABILITY = re.compile(r"^(?P<kind>[a-z.]+):\{(?P<arg>[a-z_]+)\}$")

_FIELDS = {
    "fs.read": "read_folders",
    "fs.write": "write_folders",
    "mail.account": "mail_accounts",
    "mail.send": "mail_send_to",
}


def derive_permissions(spec: TaskSpec, catalog: dict[str, dict[str, Any]]) -> Permissions:
    found: dict[str, list[str]] = {field: [] for field in _FIELDS.values()}
    found["net_domains"] = list(spec.permissions.net_domains)

    # Fields some argument could not determine (fully templated); only there the
    # model's own list is trusted, otherwise unused folders would be granted.
    undetermined: set[str] = set()

    for step in spec.all_steps():
        manifest = catalog.get(step.tool or "")
        if manifest is None:
            continue

        for capability in manifest.get("capabilities", []):
            match = _CAPABILITY.match(capability)
            if not match or match.group("kind") not in _FIELDS:
                continue

            field = _FIELDS[match.group("kind")]
            for value in _values(step.args.get(match.group("arg"))):
                item = _folder_of(value) if field.endswith("folders") else value.strip()

                if item is not None and not (field == "mail_send_to" and templating.has_template(item)):
                    found[field].append(item)

                elif templating.has_template(value) and not value.startswith(DATA_PREFIX):
                    undetermined.add(field)

    for field in undetermined:
        # data: areas need no permission, so the model listing them is just noise.
        found[field] += [v for v in getattr(spec.permissions, field) if not v.startswith(DATA_PREFIX)]

    return Permissions(
        read_folders=_collapse(found["read_folders"]),
        write_folders=_collapse(found["write_folders"]),
        mail_accounts=_dedupe(found["mail_accounts"]),
        mail_send_to=_dedupe([v.lower() for v in found["mail_send_to"]]),
        net_domains=_dedupe(found["net_domains"]),
    )


def _values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]

    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]

    return []


def _folder_of(ref: str) -> str | None:
    """`Отчёты/Продажи/*.xlsx` → `Отчёты/Продажи`; templated tails keep their literal
    prefix (`Документы/{{ kind }}/` → `Документы`); data: paths need no permission."""
    if ref.startswith(DATA_PREFIX):
        return None

    literal = ref.split("{{", 1)[0].split("{%", 1)[0]
    if templating.has_template(ref):
        # Only the part before the template is known; drop a partial last segment.
        literal = literal if literal.endswith(("/", "\\")) else posixpath.dirname(literal.replace("\\", "/"))
        if not literal.strip("/\\"):
            return None

    folder = normalize_folder(literal)
    if folder and PurePosixPath(folder).suffix and not templating.has_template(ref):
        folder = normalize_folder(posixpath.dirname(folder))

    return folder


def _collapse(folders: list[str]) -> list[str]:
    """Drop folders already covered by a parent in the list."""
    unique = sorted(set(normalize_folder(f) for f in folders), key=len)
    kept: list[str] = []

    for folder in unique:
        if any(parent == "" or folder == parent or folder.startswith(parent + "/") for parent in kept):
            continue

        kept.append(folder)

    return kept


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))
