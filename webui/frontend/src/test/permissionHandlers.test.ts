import { describe, it, expect, vi } from 'vitest'
import {
  permissionRequest,
  permissionResult,
} from '../hooks/sse/permissionHandlers'

function makeCtx() {
  return {
    setPendingPermission: vi.fn(),
    clearPendingPermission: vi.fn(),
  }
}

describe('permissionRequest handler', () => {
  it('forwards a valid payload to setPendingPermission', () => {
    const ctx = makeCtx()
    permissionRequest(
      {
        tool_call_id: 'tc-1',
        tool_name: 'write_file',
        args: { path: '/tmp/x.py' },
        pattern: '*.py',
        target: '/tmp/x.py',
      },
      ctx as never,
    )
    expect(ctx.setPendingPermission).toHaveBeenCalledWith({
      tool_call_id: 'tc-1',
      tool_name: 'write_file',
      args: { path: '/tmp/x.py' },
      pattern: '*.py',
      target: '/tmp/x.py',
    })
  })

  it('drops payloads missing tool_call_id (silent ignore)', () => {
    const ctx = makeCtx()
    permissionRequest({ tool_name: 'write_file', args: {} }, ctx as never)
    expect(ctx.setPendingPermission).not.toHaveBeenCalled()
  })

  it('drops payloads missing tool_name (silent ignore)', () => {
    const ctx = makeCtx()
    permissionRequest({ tool_call_id: 'tc-1', args: {} }, ctx as never)
    expect(ctx.setPendingPermission).not.toHaveBeenCalled()
  })

  it('handles missing optional fields by defaulting', () => {
    const ctx = makeCtx()
    permissionRequest(
      { tool_call_id: 'tc-2', tool_name: 'read_file' },
      ctx as never,
    )
    expect(ctx.setPendingPermission).toHaveBeenCalledWith({
      tool_call_id: 'tc-2',
      tool_name: 'read_file',
      args: {},
      pattern: '',
      target: '',
    })
  })

  it('tolerates empty payload object', () => {
    const ctx = makeCtx()
    permissionRequest({} as Record<string, unknown>, ctx as never)
    expect(ctx.setPendingPermission).not.toHaveBeenCalled()
  })
})

describe('permissionResult handler', () => {
  it('clears the pending permission for the matching tool_call_id', () => {
    const ctx = makeCtx()
    permissionResult(
      { tool_call_id: 'tc-1', action: 'allow', permanent: true },
      ctx as never,
    )
    expect(ctx.clearPendingPermission).toHaveBeenCalledWith('tc-1')
  })

  it('still clears even for deny results (so the dialog dismisses)', () => {
    const ctx = makeCtx()
    permissionResult(
      { tool_call_id: 'tc-2', action: 'deny', permanent: false, reason: 'no' },
      ctx as never,
    )
    expect(ctx.clearPendingPermission).toHaveBeenCalledWith('tc-2')
  })

  it('drops payloads missing tool_call_id (silent ignore)', () => {
    const ctx = makeCtx()
    permissionResult({ action: 'allow' }, ctx as never)
    expect(ctx.clearPendingPermission).not.toHaveBeenCalled()
  })

  it('tolerates empty payload object', () => {
    const ctx = makeCtx()
    permissionResult({} as Record<string, unknown>, ctx as never)
    expect(ctx.clearPendingPermission).not.toHaveBeenCalled()
  })
})