import { memo } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Database, Loader2, CheckCircle, XCircle } from 'lucide-react'

const STATUS_ICON: Record<string, React.ReactNode> = {
  running: <Loader2 size={11} className="text-purple-500 animate-spin" />,
  success: <CheckCircle size={11} className="text-green-500" />,
  failed: <XCircle size={11} className="text-red-500" />,
}

function OutputNode({ data, selected }: NodeProps) {
  const label = (data as any).label || '输出'
  const status = (data as any).status || 'idle'
  const count = ((data as any).config?.curated_dataset_ids || []).length

  return (
    <div className={`px-3 py-2 rounded-xl border-2 shadow-sm bg-white text-xs min-w-[120px] relative ${
      selected ? 'border-purple-500 ring-2 ring-purple-200' :
      status === 'running' ? 'border-purple-400 ring-2 ring-purple-100' :
      status === 'success' ? 'border-green-400' :
      status === 'failed' ? 'border-red-400' :
      'border-purple-300'
    }`}>
      {status !== 'idle' && (
        <div className="absolute -top-2 -right-2 bg-white rounded-full p-0.5 shadow-sm">
          {STATUS_ICON[status]}
        </div>
      )}
      <Handle type="target" position={Position.Left} className="!bg-purple-500 !w-2 !h-2" />
      <div className="flex items-center gap-1.5">
        <Database size={13} className="text-purple-500" />
        <span className="font-medium text-purple-700">{label}</span>
      </div>
      <p className="text-gray-400 mt-0.5">{count > 1 ? `${count} Curated Datasets` : 'Curated Dataset'}</p>
    </div>
  )
}

export default memo(OutputNode)
