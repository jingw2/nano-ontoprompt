import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm, useWatch } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { promptApi } from '@/api/ontologies'
import { DOMAINS } from '@/types/ontology'
import type { Prompt } from '@/types/ontology'
import { useEffect, useState } from 'react'
import { Wand2 } from 'lucide-react'

// Maps each selectable domain to the best-matching builtin template name
const DOMAIN_TEMPLATE_MAP: Record<string, string> = {
  '供应链': '供应链本体提取',
  '采购':   '供应链本体提取',
  '制造':   '供应链本体提取',
  '财务':   '财务本体提取',
  '医疗':   '医疗本体提取',
  '法律':   '法律本体提取',
  '教育':   '教育本体提取',
}

export default function PromptDetailPage() {
  const { t } = useTranslation()
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const isCreate = id === 'create'
  const [generating, setGenerating] = useState(false)
  const [generateMsg, setGenerateMsg] = useState('')
  const [generateError, setGenerateError] = useState(false)

  const { data: prompt } = useQuery({
    queryKey: ['prompt', id],
    queryFn: () => promptApi.get(id!),
    enabled: !isCreate && !!id,
  })

  const { register, handleSubmit, reset, setValue, control } = useForm<Partial<Prompt>>({
    defaultValues: { domain: DOMAINS[0], version: 'v1.0' }
  })

  useEffect(() => { if (prompt) reset(prompt) }, [prompt, reset])

  const selectedDomain = useWatch({ control, name: 'domain' }) || DOMAINS[0]
  const watchedName = useWatch({ control, name: 'name' })

  const createMut = useMutation({
    mutationFn: (data: Partial<Prompt>) => promptApi.create(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['prompts'] }); navigate('/prompts') },
  })

  const updateMut = useMutation({
    mutationFn: (data: Partial<Prompt>) => promptApi.update(id!, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['prompts'] })
      qc.invalidateQueries({ queryKey: ['prompt', id] })
    },
  })

  const onSubmit = (data: Partial<Prompt>) => {
    if (isCreate) createMut.mutate(data)
    else updateMut.mutate(data)
  }

  const handleGenerate = async () => {
    const domain = selectedDomain || DOMAINS[0]
    setGenerating(true)
    setGenerateMsg('')
    try {
      const templates = (await promptApi.getTemplates()) as { name: string; domain: string; content: string }[]
      // Pick the best-matching builtin template for the selected domain
      const preferredName = DOMAIN_TEMPLATE_MAP[domain]
      const match = preferredName
        ? templates.find(t => t.name === preferredName)
        : templates.find(t => t.name === '通用本体提取')
      const tpl = match ?? templates.find(t => t.name === '通用本体提取') ?? templates[0]
      if (tpl) {
        setValue('content', tpl.content, { shouldDirty: true })
        if (!watchedName) setValue('name', tpl.name, { shouldDirty: false })
        setGenerateMsg(t('prompt.template_filled', { name: tpl.name }))
        setGenerateError(false)
      } else {
        setGenerateMsg(t('prompt.no_templates'))
        setGenerateError(true)
      }
    } catch {
      setGenerateMsg(t('prompt.generate_failed'))
      setGenerateError(true)
    } finally {
      setGenerating(false)
    }
  }

  const isPending = createMut.isPending || updateMut.isPending

  return (
    <div className="max-w-3xl">
      <div className="flex items-center gap-3 mb-6">
        <button onClick={() => navigate('/prompts')} className="text-gray-500 hover:text-black text-sm">{t('ontology.back')}</button>
        <h2 className="text-xl font-semibold">{isCreate ? t('prompt.create_title') : t('prompt.edit_title')}</h2>
      </div>

      <form onSubmit={handleSubmit(onSubmit)} className="bg-white border rounded-lg p-6 space-y-5">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1">{t('prompt.name_label')}</label>
            <input {...register('name', { required: true })} className="w-full border rounded-lg px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">{t('prompt.version_label')}</label>
            <input {...register('version')} className="w-full border rounded-lg px-3 py-2 text-sm" placeholder="v1.0" />
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">{t('prompt.domain_label')}</label>
          <select {...register('domain', { required: true })} className="w-full border rounded-lg px-3 py-2 text-sm">
            {DOMAINS.map(d => <option key={d}>{d}</option>)}
          </select>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="block text-sm font-medium">{t('prompt.content_label')}</label>
            {isCreate && (
              <button type="button" onClick={handleGenerate} disabled={generating}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white rounded-lg text-xs font-medium transition-colors">
                <Wand2 size={13} />
                {generating ? t('prompt.generating') : t('prompt.generate_btn')}
              </button>
            )}
          </div>
          {generateMsg && (
            <p className={`text-xs mb-2 ${generateError ? 'text-red-500' : 'text-green-600'}`}>
              {generateMsg}
            </p>
          )}
          <textarea {...register('content', { required: true })} rows={18}
            className="w-full border rounded-lg px-3 py-2 text-sm font-mono resize-y" />
        </div>

        <div className="flex justify-end gap-3 pt-2 border-t">
          <button type="button" onClick={() => navigate('/prompts')} className="px-4 py-2 border rounded-lg text-sm">{t('common.cancel')}</button>
          <button type="submit" disabled={isPending}
            className="px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50">
            {isPending ? t('prompt.saving') : t('common.save')}
          </button>
        </div>
      </form>
    </div>
  )
}
