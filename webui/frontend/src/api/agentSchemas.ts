/**
 * Agent schema loading layer.
 *
 * Fetches `/api/agents/schemas` (populated by the backend from
 * `.prompts/*.md` — see prompt_schema_extractor.py) and caches the
 * result in-memory for the lifetime of the tab.
 *
 * Usage:
 *   - AppShell init() calls `loadAgentSchemas()` (fire-and-forget warm-up)
 *   - JsonActionCard calls `getSchemaFor(agentId)` synchronously
 */
import { api } from './client'

export interface FieldHints {
  label?: string | null
  type?: string | null
  core?: boolean | null
  enum_values?: Record<string, string> | null
  format?: string | null
  description?: string | null
}

export interface AgentSchema {
  role: string
  fields: string[]
  field_hints: Record<string, FieldHints>
  action_field?: string | null
  action_enum?: string[] | null
}

let schemaCache: Record<string, AgentSchema> | null = null
let inflight: Promise<Record<string, AgentSchema>> | null = null

export async function loadAgentSchemas(
  force = false,
): Promise<Record<string, AgentSchema>> {
  if (schemaCache && !force) return schemaCache
  if (inflight) return inflight
  inflight = api
    .get<Record<string, AgentSchema>>('/agents/schemas')
    .then((res) => {
      schemaCache = res
      inflight = null
      return res
    })
    .catch((err) => {
      // Reset so a later call can retry; callers fall back to the
      // generic KEY_FIELDS rendering when the schema is unavailable.
      inflight = null
      throw err
    })
  return inflight
}

/** Synchronous lookup against the warm cache (undefined when not loaded). */
export function getSchemaFor(role: string | null | undefined): AgentSchema | undefined {
  if (!role || !schemaCache) return undefined
  return schemaCache[role]
}

/** Test helper: inject a schema map without hitting the network. */
export function __setSchemaCacheForTest(schemas: Record<string, AgentSchema> | null) {
  schemaCache = schemas
}
