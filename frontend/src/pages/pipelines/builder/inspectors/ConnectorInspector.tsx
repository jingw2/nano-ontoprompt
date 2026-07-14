import { useState, useCallback } from 'react'
import { useDropzone } from 'react-dropzone'
import { Upload, X, FileUp, Loader2, CheckCircle, XCircle } from 'lucide-react'
import { apiClientV2 } from '@/api/client'

const SOURCE_LABEL: Record<string, string> = { file: '文件上传', postgresql: 'PostgreSQL', mysql: 'MySQL', mongodb: 'MongoDB', rest_api: 'REST API' }
const DB_CONFIG_FIELDS: Record<string, { key: string; label: string; placeholder: string; type?: string }[]> = {
  postgresql: [{ key: 'host', label: '主机', placeholder: 'localhost' }, { key: 'port', label: '端口', placeholder: '5432' }, { key: 'database', label: '数据库名', placeholder: 'mydb' }, { key: 'user', label: '用户名', placeholder: 'postgres' }, { key: 'password', label: '密码', placeholder: '••••••', type: 'password' }],
  mysql: [{ key: 'host', label: '主机', placeholder: 'localhost' }, { key: 'port', label: '端口', placeholder: '3306' }, { key: 'database', label: '数据库名', placeholder: 'mydb' }, { key: 'user', label: '用户名', placeholder: 'root' }, { key: 'password', label: '密码', placeholder: '••••••', type: 'password' }],
  mongodb: [{ key: 'uri', label: '连接字符串', placeholder: 'mongodb://localhost:27017/mydb' }],
  rest_api: [{ key: 'url', label: 'API URL', placeholder: 'https://api.example.com/data' }, { key: 'headers', label: '请求头 (JSON)', placeholder: '{"Authorization":"Bearer token"}' }, { key: 'method', label: '请求方法', placeholder: 'GET' }],
}

interface UploadedFileMeta {
  name: string
  size: number
  dataset_id?: string
  kind?: string
}

export default function ConnectorInspector({ config, onChange, readOnly = false }: { config: Record<string, unknown>; onChange: (key: string, value: unknown) => void; readOnly?: boolean }) {
  const sourceType = String(config.source_type || 'file')
  const cv = (config.config_values || {}) as Record<string, string>
  const storedFiles = (config.files || []) as UploadedFileMeta[]
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'success' | 'failed'>('idle')
  const [testMessage, setTestMessage] = useState('')
  const onDrop = useCallback(async (accepted: File[]) => {
    if (accepted.length === 0) return
    setUploading(true)
    setUploadError('')
    try {
      const uploaded: UploadedFileMeta[] = []
      for (const file of accepted) {
        const fd = new FormData()
        fd.append('file', file)
        const res: any = await apiClientV2.post('/datasets/upload', fd, {
          headers: { 'Content-Type': 'multipart/form-data' },
        })
        uploaded.push({ name: file.name, size: file.size, dataset_id: res.id, kind: res.kind })
      }
      onChange('files', [...storedFiles, ...uploaded])
      setTestStatus('idle')
    } catch (e: any) {
      setUploadError(e?.detail || e?.message || '上传失败')
    } finally {
      setUploading(false)
    }
  }, [storedFiles, onChange])
  const { getRootProps, getInputProps, isDragActive } = useDropzone({ onDrop, multiple: true })

  const hasStoredFiles = storedFiles.length > 0
  const hasDbConfig = sourceType !== 'file' && Object.keys(cv).length > 0

  const formatSize = (s: number | undefined) => s ? `(${(s / 1024).toFixed(1)} KB)` : ''

  if (readOnly) {
    return (
      <div className="space-y-3">
        <div className="bg-blue-50 border border-blue-100 rounded-lg p-3 text-xs">
          <p className="text-blue-700 font-medium mb-1">📋 已保存配置</p>
          <p className="text-blue-600">类型: {SOURCE_LABEL[sourceType] || sourceType}</p>
          {sourceType === 'file' && hasStoredFiles && storedFiles.map((f: any, i: number) => (
            <p key={i} className="text-blue-500">📄 {f.name} {formatSize(f.size)}</p>
          ))}
          {sourceType !== 'file' && hasDbConfig && Object.entries(cv).filter(([k]) => k !== 'password').map(([k, v]) => (
            <p key={k} className="text-blue-500">{k}: {String(v).slice(0, 30)}</p>
          ))}
          {!hasStoredFiles && !hasDbConfig && <p className="text-blue-400">暂无配置数据</p>}
        </div>
      </div>
    )
  }

  return (
    <>
      <div><label className="text-xs text-gray-500 mb-1 block">数据源类型</label>
        <select value={sourceType} onChange={e => { onChange('source_type', e.target.value); onChange('config_values', {}); onChange('files', []); setTestStatus('idle') }} className="w-full border rounded-lg px-3 py-1.5 text-sm"><option value="file">文件上传</option><option value="postgresql">PostgreSQL</option><option value="mysql">MySQL</option><option value="mongodb">MongoDB</option><option value="rest_api">REST API</option></select></div>
      {sourceType === 'file' && (
        <div>
          <label className="text-xs text-gray-500 mb-1 block">上传文件（支持多选）</label>
          <div {...getRootProps()} className={`border-2 border-dashed rounded-lg p-4 text-center cursor-pointer transition-colors ${isDragActive ? 'border-blue-500 bg-blue-50' : 'border-gray-200 hover:border-gray-400'}`}>
            <input {...getInputProps()} /><Upload size={20} className="mx-auto mb-1 text-gray-400" />
            {uploading ? <p className="text-xs text-blue-500 font-medium">上传中...</p> : isDragActive ? <p className="text-xs text-blue-500 font-medium">松开以添加文件</p> : <p className="text-xs text-gray-500">拖拽文件到此处，或<span className="underline ml-0.5">点击选择</span></p>}
            <p className="text-[10px] text-gray-400 mt-1">支持 CSV/XLSX/JSON/PDF/DOCX 等，可批量多选</p>
          </div>
          {uploadError && <p className="text-xs text-red-500 mt-1">{uploadError}</p>}
          {hasStoredFiles && (<div className="mt-2 space-y-1">{storedFiles.map((f: any, i: number) => (
            <div key={i} className="flex items-center gap-2 text-xs bg-gray-50 rounded px-2 py-1.5">
              <FileUp size={11} className="text-gray-400" /><span className="flex-1 truncate">{f.name}</span>
              <span className="text-gray-400">{formatSize(f.size)}</span>
              <button onClick={() => { onChange('files', storedFiles.filter((_: any, j: number) => j !== i)) }} className="text-gray-400 hover:text-red-500"><X size={11} /></button>
            </div>
          ))}</div>)}
        </div>
      )}
      {sourceType !== 'file' && (<div className="space-y-3">{DB_CONFIG_FIELDS[sourceType]?.map(f => (<div key={f.key}><label className="text-xs text-gray-500 mb-1 block">{f.label}</label><input type={f.type || 'text'} value={String((config as any).config_values?.[f.key] || '')} onChange={e => { const cv2 = { ...((config as any).config_values || {}), [f.key]: e.target.value }; onChange('config_values', cv2); setTestStatus('idle') }} placeholder={f.placeholder} className="w-full border rounded-lg px-3 py-1.5 text-sm" /></div>))}</div>)}
      <div>
        <button onClick={async () => { setTestStatus('testing'); try { if (sourceType === 'file') { setTestStatus(hasStoredFiles ? 'success' : 'failed'); setTestMessage(hasStoredFiles ? '就绪' : '请上传'); return } await apiClientV2.post('/connections/test-config', { type: sourceType, config: cv }); setTestStatus('success'); setTestMessage('连接成功') } catch (e: any) { setTestStatus('failed'); setTestMessage(e?.detail || '失败') } }} disabled={testStatus === 'testing' || uploading}
          className={`w-full flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border ${testStatus === 'success' ? 'bg-green-50 text-green-700 border-green-200' : testStatus === 'failed' ? 'bg-red-50 text-red-700 border-red-200' : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'}`}>
          {testStatus === 'testing' && <Loader2 size={11} className="animate-spin" />}{testStatus === 'success' ? <CheckCircle size={11} /> : testStatus === 'failed' ? <XCircle size={11} /> : null}{testStatus === 'testing' ? '测试中...' : testStatus === 'success' ? '连接成功' : testStatus === 'failed' ? testMessage : '测试连接'}</button>
      </div>
      <div><label className="text-xs text-gray-500 mb-1 block">同步模式</label><select value={String(config.sync_mode || 'snapshot')} onChange={e => onChange('sync_mode', e.target.value)} className="w-full border rounded-lg px-3 py-1.5 text-sm"><option value="snapshot">SNAPSHOT</option><option value="append">APPEND</option></select></div>
    </>
  )
}
