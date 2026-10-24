"""Field types shared by tool inputs."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field


# Marks an argument whose values are workspace paths. The executor checks every such
# value before the tool runs (inside the workspace or an area of DATA_DIR, common/tools/areas.py),
# whatever the tool then does with it.
PATH_ARG = "x-path"

WorkspacePath = Annotated[str, Field(json_schema_extra={PATH_ARG: "path"})]

