import { memo } from 'react'
import { BaseEdge, getSmoothStepPath, type EdgeProps } from '@xyflow/react'

export interface DAGEdgeData {
  animated?: boolean
  [key: string]: unknown
}

export const DAGEdge = memo(function DAGEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  selected,
}: EdgeProps) {
  const edgeData = (data || {}) as unknown as DAGEdgeData
  const [edgePath] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 8,
  })

  const isAnimated = edgeData.animated
  const isActive = selected || isAnimated

  return (
    <BaseEdge
      id={id}
      path={edgePath}
      style={{
        stroke: isActive ? '#3b82f6' : '#475569',
        strokeWidth: isActive ? 2 : 1.5,
        strokeDasharray: isAnimated ? '6 3' : undefined,
        filter: isActive ? 'drop-shadow(0 0 4px rgba(59, 130, 246, 0.3))' : undefined,
      }}
    />
  )
})
