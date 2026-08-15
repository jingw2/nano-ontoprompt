import { useTranslation } from 'react-i18next'

export interface ExternalToolDescriptor {
  name: string
  availableLater: boolean
}

const EXTERNAL_TOOLS: ExternalToolDescriptor[] = [
  { name: 'Search', availableLater: true },
  { name: 'Playwright', availableLater: true },
  { name: 'MCP Connections', availableLater: true },
  { name: 'Signed Skills', availableLater: true },
]

function toolNameKey(name: string): string {
  return `agent.tools.name_${name.toLowerCase().replace(/\s+/g, '_')}`
}

export default function ExternalToolCard() {
  const { t } = useTranslation()
  return (
    <div data-testid="external-tool-cards">
      {EXTERNAL_TOOLS.map(tool => (
        <div key={tool.name} className="border rounded-lg p-4 bg-gray-50 opacity-70" data-testid="external-tool-card">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">{t(toolNameKey(tool.name), tool.name)}</p>
            <span className="px-2 py-0.5 rounded text-xs bg-gray-200 text-gray-600">
              {t('agent.tools.available_later', 'Available later')}
            </span>
          </div>
          <p className="text-xs text-gray-500 mt-1">
            {t('agent.tools.external_later', '外部工具将在后续版本提供')}
          </p>
        </div>
      ))}
    </div>
  )
}
