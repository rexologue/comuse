// Names the user reads instead of tool ids: a tool's own title, or the agent's for its
// own tools (GET /agent/meta-tools; traced with a `meta:` prefix).

import { useMetaTools, useTools } from "@/api/queries";
import type { ToolManifest } from "@/api/types";

export function toolTitle(name: string, tools: Map<string, ToolManifest> | undefined, meta: Map<string, string> | undefined): string {
  const bare = name.replace(/^meta:/, "");

  return tools?.get(bare)?.title ?? meta?.get(bare) ?? bare.replace("__", ".");
}

export function useToolTitle(): (name: string) => string {
  const tools = useTools();
  const meta = useMetaTools();

  return (name) => toolTitle(name, tools.data, meta.data);
}
