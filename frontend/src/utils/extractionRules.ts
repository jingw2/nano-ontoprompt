export interface ExtractionRuleDef {
  id: string
  label_cn: string
  description_cn: string
  has_value: boolean
  default_enabled: boolean
  default_value?: number
  constraint_fn: (value?: number) => string
}

export interface ExtractionRuleState {
  enabled: boolean
  value?: number
}

export const EXTRACTION_RULES: ExtractionRuleDef[] = [
  {
    id: 'cross_doc_entity',
    label_cn: '多文档实体验证',
    description_cn: '同一实体需在 N 个或以上独立文档中均有体现才提取',
    has_value: true,
    default_enabled: false,
    default_value: 2,
    constraint_fn: (n = 2) =>
      `【约束】实体仅在你确信该实体概念在 ${n} 个或以上独立文档来源中均有体现时才输出，否则不输出。`,
  },
  {
    id: 'cross_doc_logic',
    label_cn: '多文档规则验证',
    description_cn: '同一逻辑规则需在 N 个或以上独立文档中均有体现才提取',
    has_value: true,
    default_enabled: false,
    default_value: 2,
    constraint_fn: (n = 2) =>
      `【约束】逻辑规则仅在你确信该业务规则在 ${n} 个或以上独立文档来源中均有体现时才输出，否则不输出。`,
  },
  {
    id: 'cross_doc_action',
    label_cn: '多文档动作验证',
    description_cn: '同一动作需在 N 个或以上独立文档中均有体现才提取',
    has_value: true,
    default_enabled: false,
    default_value: 2,
    constraint_fn: (n = 2) =>
      `【约束】动作仅在你确信该可执行动作在 ${n} 个或以上独立文档来源中均有体现时才输出，否则不输出。`,
  },
  {
    id: 'no_duplicate',
    label_cn: '实体去重',
    description_cn: '避免提取语义相同但名称略有不同的重复实体',
    has_value: false,
    default_enabled: false,
    constraint_fn: () =>
      '【约束】严格避免提取语义等同的重复实体，若多个名称指同一对象，只保留最完整准确的一个名称。',
  },
  {
    id: 'strict_type',
    label_cn: '严格类型校验',
    description_cn: '实体必须归属于 Prompt 中定义的预设类型，不允许自定义类型',
    has_value: false,
    default_enabled: false,
    constraint_fn: () =>
      '【约束】实体的 type 字段必须严格使用 Prompt 中预定义的类型，不允许输出其他自定义类型。',
  },
  {
    id: 'min_confidence',
    label_cn: '最低置信度阈值',
    description_cn: '置信度低于阈值的实体、规则、动作不输出',
    has_value: true,
    default_enabled: false,
    default_value: 0.8,
    constraint_fn: (n = 0.8) =>
      `【约束】所有提取内容的 confidence 字段必须如实反映确信程度，置信度低于 ${n} 的内容请勿输出。`,
  },
]

interface EntityLike {
  name_cn?: string
  properties?: Record<string, unknown>
}

interface LogicLike {
  name_cn?: string
  linked_entities?: string[]
}

interface ActionLike {
  linked_logic_ids?: unknown[]
  linked_entities?: unknown[]
}

export interface ValidationRuleDef {
  id: string
  label_cn: string
  description_cn: string
  check_fn: (entities: EntityLike[], logic: LogicLike[], actions: ActionLike[]) => { pass: boolean; detail: string }
}

export const VALIDATION_RULES: ValidationRuleDef[] = [
  {
    id: 'bidirectional_links',
    label_cn: '双向关联完整性',
    description_cn: '逻辑规则的 linked_entities 中引用的实体，在该实体详情页应能看到此逻辑规则',
    check_fn: (entities, logic) => {
      const entityNames = new Set(entities.map(e => e.name_cn).filter(Boolean))
      const broken: string[] = []
      logic.forEach(r => {
        (r.linked_entities ?? []).forEach((name: string) => {
          if (!entityNames.has(name)) broken.push(`「${r.name_cn}」引用了不存在的实体「${name}」`)
        })
      })
      return broken.length === 0
        ? { pass: true, detail: '所有逻辑规则的关联实体均存在' }
        : { pass: false, detail: broken.slice(0, 3).join('；') + (broken.length > 3 ? `...等 ${broken.length} 处` : '') }
    },
  },
  {
    id: 'entity_properties',
    label_cn: '实体属性完整性',
    description_cn: '所有提取的实体都应包含至少一个属性（properties 字段不为空）',
    check_fn: (entities) => {
      const empty = entities.filter(e => !e.properties || Object.keys(e.properties).length === 0)
      return empty.length === 0
        ? { pass: true, detail: `全部 ${entities.length} 个实体均有属性` }
        : { pass: false, detail: `${empty.length}/${entities.length} 个实体缺少属性` }
    },
  },
  {
    id: 'logic_linked_entities',
    label_cn: '逻辑规则实体关联',
    description_cn: '所有逻辑规则都应关联至少一个实体（linked_entities 不为空）',
    check_fn: (_, logic) => {
      const empty = logic.filter(r => !(r.linked_entities ?? []).length)
      return empty.length === 0
        ? { pass: true, detail: `全部 ${logic.length} 条逻辑规则均有关联实体` }
        : { pass: false, detail: `${empty.length}/${logic.length} 条逻辑规则缺少关联实体` }
    },
  },
  {
    id: 'action_logic_links',
    label_cn: '动作逻辑关联',
    description_cn: '所有动作都应关联至少一条逻辑规则（linked_logic_ids 不为空）',
    check_fn: (_, __, actions) => {
      const empty = actions.filter(a => !(a.linked_logic_ids ?? []).length)
      return empty.length === 0
        ? { pass: true, detail: `全部 ${actions.length} 个动作均有关联逻辑规则` }
        : { pass: false, detail: `${empty.length}/${actions.length} 个动作缺少关联逻辑规则` }
    },
  },
  {
    id: 'action_linked_entities',
    label_cn: '动作实体关联',
    description_cn: '所有动作都应关联至少一个实体（linked_entities 不为空）',
    check_fn: (_, __, actions) => {
      const empty = actions.filter(a => !(a.linked_entities ?? []).length)
      return empty.length === 0
        ? { pass: true, detail: `全部 ${actions.length} 个动作均有关联实体` }
        : { pass: false, detail: `${empty.length}/${actions.length} 个动作缺少关联实体` }
    },
  },
]

const VALIDATION_STORAGE_KEY = 'ontexus_validation_rules'

export function loadValidationStates(): Record<string, boolean> {
  try {
    const saved = localStorage.getItem(VALIDATION_STORAGE_KEY)
    if (saved) return JSON.parse(saved)
  } catch { /* ignore */ }
  return Object.fromEntries(VALIDATION_RULES.map(r => [r.id, true]))
}

export function saveValidationStates(states: Record<string, boolean>): void {
  localStorage.setItem(VALIDATION_STORAGE_KEY, JSON.stringify(states))
}

const STORAGE_KEY = 'ontexus_extraction_rules'

export function loadRuleStates(): Record<string, ExtractionRuleState> {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) return JSON.parse(saved)
  } catch { /* ignore */ }
  return Object.fromEntries(
    EXTRACTION_RULES.map(r => [r.id, { enabled: r.default_enabled, value: r.default_value }])
  )
}

export function saveRuleStates(states: Record<string, ExtractionRuleState>): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(states))
}

export function getActiveConstraints(states: Record<string, ExtractionRuleState>): string[] {
  return EXTRACTION_RULES
    .filter(r => states[r.id]?.enabled)
    .map(r => r.constraint_fn(states[r.id]?.value))
}
