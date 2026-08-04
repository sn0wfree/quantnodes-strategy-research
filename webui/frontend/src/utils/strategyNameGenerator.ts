/**
 * Strategy Name Generator
 *
 * Generates unique strategy names based on research objectives.
 * Format: {abbreviated_name}_{user_id}_{compressed_session_ts}_{random2}
 */

// ── Constants ──────────────────────────────────────────────────────

const BASE62_CHARS = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'

const KEYWORD_MAP: Record<string, string> = {
  // Asset types
  'a股': 'Ashare', 'a股市场': 'Ashare', '沪深': 'Ashare',
  '美股': 'Ustock', '纳斯达克': 'Ustock', 'sp500': 'Ustock', '标普': 'Ustock',
  '港股': 'Hkstock',
  '加密货币': 'Crypto', '比特币': 'Btc',

  // Factor types
  '动量': 'Mom', '动量因子': 'Mom',
  '波动率': 'Vol', '波动': 'Vol',
  '价值': 'Val', '价值因子': 'Val',
  '质量': 'Qual', '质量因子': 'Qual',
  '反转': 'Rev', '均值回归': 'MeanRev',
  '多因子': 'Multi', '因子组合': 'Combo',
  '轮动': 'Rot', '行业轮动': 'SectorRot', '行业': 'Sector',
  '成长': 'Growth', '红利': 'Dividend', '低波': 'LowVol',
  '小盘': 'SmallCap', '大盘': 'LargeCap',

  // Actions
  '测试': 'Test', '验证': 'Validate',
  '研究': 'Rsrch', '优化': 'Opt', '回测': 'Backtest',

  // Filter words (skip in naming)
  '因子': '', '策略': '', '组合': '', '的': '', '在': '', '到': '', '与': '',
}

// ── Base62 encoding ────────────────────────────────────────────────

export function toBase62(num: bigint, length: number): string {
  let result = ''
  let n = num
  for (let i = 0; i < length; i++) {
    result = BASE62_CHARS[Number(n % 62n)] + result
    n = n / 62n
  }
  return result
}

export function fromBase62(str: string): bigint {
  let result = 0n
  for (const char of str) {
    result = result * 62n + BigInt(BASE62_CHARS.indexOf(char))
  }
  return result
}

// ── Timestamp compression ──────────────────────────────────────────

/**
 * Compress YYMMDDhhmmss to 7-char base36 string.
 * Uses 33 bits: YY(7) + MM(4) + DD(5) + hh(5) + mm(6) + ss(6)
 */
export function compressTimestamp(date: Date): string {
  const YY = date.getFullYear() % 100
  const MM = date.getMonth() + 1
  const DD = date.getDate()
  const hh = date.getHours()
  const mm = date.getMinutes()
  const ss = date.getSeconds()

  const packed =
    (YY << 26) |
    (MM << 22) |
    (DD << 17) |
    (hh << 12) |
    (mm << 6) |
    ss

  return packed.toString(36).padStart(7, '0')
}

/**
 * Decompress 7-char base36 string back to YYMMDDhhmmss components.
 */
export function decompressTimestamp(compressed: string): {
  YY: number; MM: number; DD: number; hh: number; mm: number; ss: number
} {
  const packed = parseInt(compressed, 36)

  return {
    YY: (packed >> 26) & 0x7F,
    MM: (packed >> 22) & 0xF,
    DD: (packed >> 17) & 0x1F,
    hh: (packed >> 12) & 0x1F,
    mm: (packed >> 6) & 0x3F,
    ss: packed & 0x3F,
  }
}

// ── Session + Timestamp compression ────────────────────────────────

/**
 * Compress session_hash7 (36 bits) + timestamp (33 bits) → 12 chars base62.
 * Uses bit packing: (session_int << 33) | time_int
 */
export function compressSessionTs(sessionHash7: string, timestampTs: string): string {
  const sessionInt = BigInt(parseInt(sessionHash7, 36))
  const timeInt = BigInt(parseInt(timestampTs, 36))

  const combined = (sessionInt << 33n) | timeInt

  return toBase62(combined, 12)
}

/**
 * Decompress 12-char base62 string back to session_hash7 + timestamp.
 */
export function decompressSessionTs(compressed: string): {
  sessionHash7: string
  timestamp: string
} {
  const combined = fromBase62(compressed)

  const sessionInt = Number(combined >> 33n)
  const timeInt = Number(combined & ((1n << 33n) - 1n))

  return {
    sessionHash7: sessionInt.toString(36).padStart(7, '0'),
    timestamp: timeInt.toString(36).padStart(7, '0'),
  }
}

// ── Keyword extraction ─────────────────────────────────────────────

/**
 * Extract keywords from Chinese objective and translate to abbreviated English.
 */
export function extractKeywords(objective: string): string[] {
  const keywords: string[] = []
  const processed = new Set<number>()

  // Sort keywords by length (longest first) for greedy matching
  const sortedKeywords = Object.keys(KEYWORD_MAP).sort((a, b) => b.length - a.length)

  for (const keyword of sortedKeywords) {
    let searchStart = 0
    while (searchStart < objective.length) {
      const idx = objective.indexOf(keyword, searchStart)
      if (idx === -1) break

      // Check if this position was already processed
      const end = idx + keyword.length
      const alreadyProcessed = Array.from(processed).some(
        (p) => idx < p + 1 && end > p
      )
      if (!alreadyProcessed) {
        const translation = KEYWORD_MAP[keyword]
        if (translation) {
          keywords.push(translation)
        }
        // Mark all positions as processed
        for (let i = idx; i < end; i++) {
          processed.add(i)
        }
      }

      searchStart = end
    }
  }

  return keywords
}

/**
 * Generate abbreviated strategy name from keywords.
 * Combines first 2-3 keywords, max 20 chars.
 */
export function generateAbbreviatedName(keywords: string[]): string {
  if (keywords.length === 0) {
    return 'Strategy'
  }

  // Take first 2-3 keywords, join them
  const parts = keywords.slice(0, 3)
  const name = parts.join('')

  // Truncate to 20 chars if too long
  return name.length > 20 ? name.slice(0, 20) : name
}

// ── Random string generation ───────────────────────────────────────

/**
 * Generate 2-char alphanumeric random string.
 */
export function generateRandom2(): string {
  const chars = '0123456789abcdefghijklmnopqrstuvwxyz'
  let result = ''
  for (let i = 0; i < 2; i++) {
    result += chars[Math.floor(Math.random() * chars.length)]
  }
  return result
}

/**
 * Generate session hash (SHA256 first 7 chars of hex).
 * For frontend, we use a simple hash since crypto.subtle may not be available.
 */
export function generateSessionHash7(sessionId: string): string {
  // Simple FNV-1a hash for frontend compatibility
  let hash = 0xcbf29ce484222325n
  for (let i = 0; i < sessionId.length; i++) {
    hash ^= BigInt(sessionId.charCodeAt(i))
    hash = (hash * 0x100000001b3n) & 0xffffffffffffffffn
  }
  // Convert to base36 and take first 7 chars
  return hash.toString(36).slice(0, 7).padStart(7, '0')
}

// ── Main generator ─────────────────────────────────────────────────

export interface StrategyNameParts {
  abbreviatedName: string
  userId: string
  compressedSessionTs: string
  random2: string
}

/**
 * Generate full strategy name from components.
 */
export function generateStrategyName(
  objective: string,
  userId: string,
  sessionId: string,
  timestamp?: Date
): { name: string; parts: StrategyNameParts } {
  const now = timestamp || new Date()

  // 1. Extract keywords and generate abbreviated name
  const keywords = extractKeywords(objective)
  const abbreviatedName = generateAbbreviatedName(keywords)

  // 2. Generate session hash
  const sessionHash7 = generateSessionHash7(sessionId)

  // 3. Generate compressed timestamp
  const timestampTs = compressTimestamp(now)

  // 4. Compress session + timestamp
  const compressedSessionTs = compressSessionTs(sessionHash7, timestampTs)

  // 5. Generate random
  const random2 = generateRandom2()

  // 6. Combine
  const parts: StrategyNameParts = {
    abbreviatedName,
    userId,
    compressedSessionTs,
    random2,
  }

  const name = `${abbreviatedName}_${userId}_${compressedSessionTs}_${random2}`

  return { name, parts }
}

/**
 * Regenerate strategy name with new random (for uniqueness).
 */
export function regenerateWithRandom(
  parts: Omit<StrategyNameParts, 'random2'>
): { name: string; random2: string } {
  const random2 = generateRandom2()
  const name = `${parts.abbreviatedName}_${parts.userId}_${parts.compressedSessionTs}_${random2}`
  return { name, random2 }
}

/**
 * Validate strategy name format.
 */
export function validateStrategyName(name: string): { valid: boolean; error?: string } {
  if (!name) {
    return { valid: false, error: '策略名不能为空' }
  }

  if (name.length > 64) {
    return { valid: false, error: '策略名过长（最多64字符）' }
  }

  // Check format: {name}_{userId}_{compressed}_{random2}
  const parts = name.split('_')
  if (parts.length < 4) {
    return { valid: false, error: '策略名格式不正确' }
  }

  // Check for invalid characters (only alphanumeric and underscore)
  if (!/^[a-zA-Z0-9_]+$/.test(name)) {
    return { valid: false, error: '策略名只能包含字母、数字和下划线' }
  }

  return { valid: true }
}
