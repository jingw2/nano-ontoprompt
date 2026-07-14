import { useState, useEffect, useCallback } from 'react'
import { useDropzone } from 'react-dropzone'
import { Plus, Database, FileUp, Globe, X, Loader2, RefreshCw } from 'lucide-react'
import { apiClientV2 } from '@/api/client'

interface Connection {
  id: string
  name: string
  kind: string
  status: string
}

const KIND_META: Record<string, { icon: React.ReactNode; label: string }> = {
  file:     { icon: <FileUp size={14} />,   label: '文件上传' },
  mysql:    { icon: <Database size={14} />, label: 'MySQL' },
  postgres: { icon: <Database size={14} />, label: 'PostgreSQL' },
  mongo:    { icon: <Database size={14} />, label: 'MongoDB' },
  rest:     { icon: <Globe size={14} />,    label: 'REST API' },
}

const STATUS_STYLE: Record<string, string> = {
  active:   'text-green-600 bg-green-50 border-green-200',
  inactive: 'text-gray-400 bg-gray-50 border-gray-200',
  error:    'text-red-500 bg-red-50 border-red-200',
}

const STATUS_LABEL: Record<string, string> = {
  active: '活跃', inactive: '未激活', error: '错误',
}

const KIND_CONFIG_FIELDS: Record<string, { key: string; label: string; placeholder: string; type?: string }[]> = {
  mysql:    [
    { key: 'host', label: '主机', placeholder: 'localhost' },
    { key: 'port', label: '端口', placeholder: '3306' },
    { key: 'database', label: '数据库名', placeholder: 'mydb' },
    { key: 'user', label: '用户名', placeholder: 'root' },
    { key: 'password', label: '密码', placeholder: '••••••', type: 'password' },
  ],
  postgres: [
    { key: 'host', label: '主机', placeholder: 'localhost' },
    { key: 'port', label: '端口', placeholder: '5432' },
    { key: 'database', label: '数据库名', placeholder: 'mydb' },
    { key: 'user', label: '用户名', placeholder: 'postgres' },
    { key: 'password', label: '密码', placeholder: '••••••', type: 'password' },
  ],
  mongo:    [
    { key: 'uri', label: '连接字符串', placeholder: 'mongodb://localhost:27017/mydb' },
  ],
  rest:     [
    { key: 'url', label: 'API URL', placeholder: 'https://api.example.com/data' },
    { key: 'headers', label: '请求头 (JSON)', placeholder: '{"Authorization": "Bearer token"}' },
  ],
  file: [],
}

function FileUploadZone({ files, onFilesChange }: { files: File[]; onFilesChange: (f: File[]) => void }) {
  const onDrop = useCallback((accepted: File[]) => {
    onFilesChange([...files, ...accepted])
  }, [files, onFilesChange])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({ onDrop, multiple: true })

  return (
    <div>
      <div
        {...getRootProps()}
        className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors
          ${isDragActive ? 'border-black bg-gray-50' : 'border-gray-200 hover:border-gray-400'}`}
      >
        <input {...getInputProps()} />
        <FileUp size={28} className="mx-auto mb-2 text-gray-400" />
        {isDragActive ? (
          <p className="text-sm text-black font-medium">松开以添加文件</p>
        ) : (
          <>
            <p className="text-sm text-gray-600">拖拽文件到此处，或<span className="underline ml-1 cursor-pointer">点击选择</span></p>
            <p className="text-xs text-gray-400 mt-1">支持 CSV、XLSX、JSON、PDF、DOCX 等格式，可多选</p>
          </>
        )}
      </div>
      {files.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {files.map((f, i) => (
            <div key={i} className="flex items-center gap-2 text-xs bg-gray-50 rounded-lg px-3 py-2">
              <FileUp size={12} className="text-gray-400 shrink-0" />
              <span className="flex-1 truncate text-gray-700">{f.name}</span>
              <span className="text-gray-400">{(f.size / 1024).toFixed(1)} KB</span>
              <button
                type="button"
                onClick={() => onFilesChange(files.filter((_, j) => j !== i))}
                className="text-gray-400 hover:text-red-500"
              >
                <X size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function ConnectionsTab() {
  const [connections, setConnections] = useState<Connection[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState('')
  const [syncing, setSyncing] = useState<string | null>(null)

  const [formName, setFormName] = useState('')
  const [formKind, setFormKind] = useState('mysql')
  const [formConfig, setFormConfig] = useState<Record<string, string>>({})
  const [formSyncMode, setFormSyncMode] = useState<'snapshot' | 'append'>('snapshot')
  const [formFiles, setFormFiles] = useState<File[]>([])

  const loadConnections = () => {
    setLoading(true)
    apiClientV2.get('/connections')
      .then((res: unknown) => setConnections(Array.isArray(res) ? res : ((res as { data?: Connection[] })?.data ?? [])))
      .catch(() => setConnections([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadConnections() }, [])

  const resetForm = () => {
    setFormName('')
    setFormKind('mysql')
    setFormConfig({})
    setFormSyncMode('snapshot')
    setFormFiles([])
    setFormError('')
  }

  const handleSave = async () => {
    if (!formName.trim()) { setFormError('请填写连接名称'); return }
    if (formKind === 'file' && formFiles.length === 0) { setFormError('请至少选择一个文件'); return }
    setSaving(true)
    setFormError('')
    try {
      if (formKind === 'file') {
        for (const file of formFiles) {
          const fd = new FormData()
          fd.append('file', file)
          await apiClientV2.post('/datasets/upload', fd, {
            headers: { 'Content-Type': 'multipart/form-data' },
          })
        }
        await apiClientV2.post('/connections', {
          name: formName, kind: 'file',
          config: { files: formFiles.map(f => f.name), sync_mode: formSyncMode },
        })
      } else {
        await apiClientV2.post('/connections', {
          name: formName, kind: formKind,
          config: { ...formConfig, sync_mode: formSyncMode },
        })
      }
      setShowForm(false)
      resetForm()
      loadConnections()
    } catch (e: unknown) {
      const err = e as { detail?: string; response?: { data?: { detail?: string } }; message?: string }
      setFormError(err?.detail || err?.response?.data?.detail || err?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleSync = async (id: string) => {
    setSyncing(id)
    try {
      await apiClientV2.post(`/connections/${id}/sync`, {})
      loadConnections()
    } catch {
      // ignore sync errors silently
    } finally {
      setSyncing(null)
    }
  }

  const handleDelete = async (id: string) => {
    if (!window.confirm('确认删除此连接？')) return
    await apiClientV2.delete(`/connections/${id}`)
    loadConnections()
  }

  if (loading) return <div className="text-gray-400 text-sm p-4">加载中...</div>

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-lg font-semibold">数据连接</h2>
          <p className="text-xs text-gray-400 mt-0.5">管理数据源连接，支持数据库、API 和文件上传</p>
        </div>
        <button
          onClick={() => { resetForm(); setShowForm(true) }}
          className="flex items-center gap-2 bg-black text-white px-4 py-2 rounded-lg text-sm"
        >
          <Plus size={14} /> 新建连接
        </button>
      </div>

      {showForm && (
        <div className="border rounded-xl p-5 bg-white space-y-4">
          <div className="flex justify-between items-center">
            <h3 className="font-medium text-sm">新建连接</h3>
            <button onClick={() => { setShowForm(false); resetForm() }} className="text-gray-400 hover:text-black">
              <X size={16} />
            </button>
          </div>

          <div>
            <label className="text-xs text-gray-500 mb-1 block">连接名称 *</label>
            <input
              value={formName}
              onChange={e => setFormName(e.target.value)}
              placeholder="例：ERP 订单数据库"
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-black"
            />
          </div>

          <div>
            <label className="text-xs text-gray-500 mb-2 block">连接类型</label>
            <div className="flex gap-2 flex-wrap">
              {Object.entries(KIND_META).map(([k, m]) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => { setFormKind(k); setFormConfig({}); setFormFiles([]) }}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border transition-colors
                    ${formKind === k ? 'bg-black text-white border-black' : 'border-gray-200 text-gray-600 hover:bg-gray-50'}`}
                >
                  {m.icon} {m.label}
                </button>
              ))}
            </div>
          </div>

          {formKind === 'file' ? (
            <FileUploadZone files={formFiles} onFilesChange={setFormFiles} />
          ) : (
            <div className="space-y-3">
              {KIND_CONFIG_FIELDS[formKind]?.map(f => (
                <div key={f.key}>
                  <label className="text-xs text-gray-500 mb-1 block">{f.label}</label>
                  <input
                    type={f.type || 'text'}
                    value={formConfig[f.key] || ''}
                    onChange={e => setFormConfig(p => ({ ...p, [f.key]: e.target.value }))}
                    placeholder={f.placeholder}
                    className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-black"
                  />
                </div>
              ))}
            </div>
          )}

          <div>
            <label className="text-xs text-gray-500 mb-2 block">同步模式</label>
            <div className="flex gap-4">
              {(['snapshot', 'append'] as const).map(m => (
                <label key={m} className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="radio"
                    name="sync_mode"
                    value={m}
                    checked={formSyncMode === m}
                    onChange={() => setFormSyncMode(m)}
                    className="accent-black"
                  />
                  <span>{m === 'snapshot' ? 'SNAPSHOT（全量覆盖）' : 'APPEND（增量追加）'}</span>
                </label>
              ))}
            </div>
          </div>

          {formError && <p className="text-red-500 text-xs">{formError}</p>}

          <div className="flex gap-2 justify-end">
            <button onClick={() => { setShowForm(false); resetForm() }} className="px-4 py-2 text-sm border rounded-lg hover:bg-gray-50">
              取消
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-2 px-4 py-2 text-sm bg-black text-white rounded-lg disabled:opacity-50"
            >
              {saving && <Loader2 size={13} className="animate-spin" />}
              {saving ? '保存中...' : '保存'}
            </button>
          </div>
        </div>
      )}

      {connections.length === 0 ? (
        <div className="border-2 border-dashed rounded-xl p-10 text-center text-gray-400 space-y-2">
          <Database size={28} className="mx-auto opacity-30" />
          <p className="text-sm">暂无数据连接</p>
          <p className="text-xs">点击「新建连接」添加数据源</p>
        </div>
      ) : (
        <div className="border rounded-xl divide-y overflow-hidden">
          {connections.map(c => {
            const meta = KIND_META[c.kind] ?? KIND_META.file
            const statusStyle = STATUS_STYLE[c.status] ?? STATUS_STYLE.inactive
            const statusLabel = STATUS_LABEL[c.status] ?? c.status
            return (
              <div key={c.id} className="p-4 flex items-center gap-3">
                <div className="w-8 h-8 bg-gray-100 rounded-lg flex items-center justify-center text-gray-500">
                  {meta.icon}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-sm truncate">{c.name}</p>
                  <p className="text-xs text-gray-400">{meta.label}</p>
                </div>
                <span className={`text-xs font-medium px-2 py-0.5 rounded border ${statusStyle}`}>
                  {statusLabel}
                </span>
                <button
                  onClick={() => handleSync(c.id)}
                  disabled={syncing === c.id}
                  className="flex items-center gap-1 text-xs px-2.5 py-1.5 border rounded-lg hover:bg-gray-50 disabled:opacity-50 transition-colors"
                >
                  <RefreshCw size={11} className={syncing === c.id ? 'animate-spin' : ''} />
                  同步
                </button>
                <button
                  onClick={() => handleDelete(c.id)}
                  className="text-gray-400 hover:text-red-500 text-xs px-1 transition-colors"
                >
                  删除
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
