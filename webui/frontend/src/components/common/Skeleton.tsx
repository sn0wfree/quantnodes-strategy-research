interface SkeletonProps {
  className?: string
  variant?: 'rect' | 'circle' | 'text'
}

export function Skeleton({ className = '', variant = 'rect' }: SkeletonProps) {
  const base = 'animate-pulse bg-slate-700/50'
  const variantClass = {
    rect: 'rounded-lg',
    circle: 'rounded-full',
    text: 'rounded h-4',
  }[variant]

  return (
    <div className={`${base} ${variantClass} ${className}`} />
  )
}

export function MessageSkeleton() {
  return (
    <div className="flex gap-3 px-4 py-3">
      <Skeleton variant="circle" className="h-8 w-8 flex-shrink-0" />
      <div className="flex-1 space-y-2">
        <Skeleton variant="text" className="w-24" />
        <Skeleton variant="text" className="w-full" />
        <Skeleton variant="text" className="w-3/4" />
      </div>
    </div>
  )
}

export function AgentSkeleton() {
  return (
    <div className="flex items-center gap-3 px-4 py-3">
      <Skeleton variant="circle" className="h-6 w-6" />
      <Skeleton variant="text" className="w-32" />
      <Skeleton variant="rect" className="ml-auto h-5 w-16 rounded-full" />
    </div>
  )
}
