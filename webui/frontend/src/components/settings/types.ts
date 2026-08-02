export interface SystemInfo {
  workspace_path: string
  user_count: number
  llm: { configured: boolean; provider: string; model: string; api_key_source: string }
}

export interface LLMConfig {
  provider: string
  model: string
  api_key: string
  api_key_masked: boolean
  base_url: string
  active_profile: string
  profiles: string[]
  providers: ProviderInfo[]
}

export interface ProviderInfo {
  name: string
  label: string
  model: string
  models: string[]
  base_url: string
  key_var: string
  key_configured: boolean
}