import { memo } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Cpu, Loader2, CheckCircle, XCircle } from 'lucide-react'

const STATUS_ICON: Record<string, React.ReactNode> = {
  running: <Loader2 size={11} className="text-amber-500 animate-spin" />,
  success: <CheckCircle size={11} className="text-green-500" />,
  failed: <XCircle size={11} className="text-red-500" />,
}

function TransformNode({ data, selected }: NodeProps) {
  const label = (data as any).label || '转换器'
  const status = (data as any).status || 'idle'
  const steps = ((data as any).config?.steps || []) as any[]

  return (
    <div className={`px-3 py-2 rounded-xl border-2 shadow-sm bg-white text-xs min-w-[140px] relative ${
      selected ? 'border-amber-500 ring-2 ring-amber-200' :
      status === 'running' ? 'border-amber-400 ring-2 ring-amber-100' :
      status === 'success' ? 'border-green-400' :
      status === 'failed' ? 'border-red-400' :
      'border-amber-300'
    }`}>
      {status !== 'idle' && (
        <div className="absolute -top-2 -right-2 bg-white rounded-full p-0.5 shadow-sm">
          {STATUS_ICON[status]}
        </div>
      )}
      <Handle type="target" position={Position.Left} className="!bg-amber-500 !w-2 !h-2" />
      <Handle type="source" position={Position.Right} className="!bg-amber-500 !w-2 !h-2" />
      <div className="flex items-center gap-1.5">
        <Cpu size={13} className="text-amber-500" />
        <span className="font-medium text-amber-700">{label}</span>
      </div>
      <p className="text-gray-400 mt-0.5">{steps.length > 0 ? `${steps.length} 个步骤` : '转换器'}</p>
    </div>
  )
}

export default memo(TransformNode)
