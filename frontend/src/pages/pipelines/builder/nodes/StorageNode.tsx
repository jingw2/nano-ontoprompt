import { memo } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { HardDrive, Loader2, CheckCircle, XCircle } from 'lucide-react'

const STATUS_ICON: Record<string, React.ReactNode> = {
  running: <Loader2 size={11} className="text-emerald-500 animate-spin" />,
  success: <CheckCircle size={11} className="text-green-500" />,
  failed: <XCircle size={11} className="text-red-500" />,
}

function StorageNode({ data, selected }: NodeProps) {
  const label = (data as any).label || '存储器'
  const status = (data as any).status || 'idle'

  return (
    <div className={`px-3 py-2 rounded-xl border-2 shadow-sm bg-white text-xs min-w-[120px] relative ${
      selected ? 'border-emerald-500 ring-2 ring-emerald-200' :
      status === 'running' ? 'border-emerald-400 ring-2 ring-emerald-100' :
      status === 'success' ? 'border-green-400' :
      status === 'failed' ? 'border-red-400' :
      'border-emerald-300'
    }`}>
      {status !== 'idle' && (
        <div className="absolute -top-2 -right-2 bg-white rounded-full p-0.5 shadow-sm">
          {STATUS_ICON[status]}
        </div>
      )}
      <Handle type="target" position={Position.Left} className="!bg-emerald-500 !w-2 !h-2" />
      <Handle type="source" position={Position.Right} className="!bg-emerald-500 !w-2 !h-2" />
      <div className="flex items-center gap-1.5">
        <HardDrive size={13} className="text-emerald-500" />
        <span className="font-medium text-emerald-700">{label}</span>
      </div>
      <p className="text-gray-400 mt-0.5">存储器</p>
    </div>
  )
}

export default memo(StorageNode)
