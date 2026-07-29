import { nanoid } from 'nanoid'

export type IdPrefix = 'msg' | 'session' | 'agent' | 'task' | 'workflow' | 'node' | 'tool'

/**
 * Generate a unique, URL-safe ID with optional type prefix.
 *
 * Uses nanoid (collision-resistant, ~71 bits of entropy at size=12).
 * Unlike `crypto.randomUUID`, this works in non-secure contexts
 * (HTTP + non-localhost) because it relies on `crypto.getRandomValues`,
 * which is available in all modern browsers regardless of origin.
 *
 * @example
 *   uuid()         // → "V1StGXR8_Z5jd"
 *   uuid('msg')    // → "msg_V1StGXR8_Z5jd"
 */
export function uuid(prefix?: IdPrefix): string {
  const suffix = nanoid(12)
  return prefix ? `${prefix}_${suffix}` : suffix
}