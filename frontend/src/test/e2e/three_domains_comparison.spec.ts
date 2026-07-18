/**
 * 三领域对比测试：供应链 / 医疗 / 财务
 * 每个业务域跑 Pipeline Mapping + 简易 LLM 两条路径，
 * 最终输出实体数、边数、逻辑数、动作数汇总表。
 */

/// <reference types="node" />

import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const BASE = 'http://localhost:5173'
const API  = 'http://localhost:8000'
const __filename = fileURLToPath(import.meta.url)
const __dirname  = path.dirname(__filename)
const TEST_DATA  = path.resolve(__dirname, '../../../../test_data')

const DOMAINS = ['供应链', '医疗', '财务'] as const
type Domain = typeof DOMAINS[number]

// ── 固定 ID（匹配当前 DB 状态） ───────────────────────────────────────────
const MODEL_ID   = '8f347f97-e844-4d62-b81b-8c655cd3b410'   // deepseek
const MODEL_NAME = 'deepseek-v4-flash'

const PROMPT_BY_DOMAIN: Record<Domain, string> = {
  '供应链': '9dad1123-72eb-4b9b-b5b3-1777c54ca3cd',
  '医疗':   'd9bf7a9a-5313-4be3-b941-88c33f280566',
  '财务':   'bff40feb-6f53-460e-97d1-b5e8d4f4a9be',
}

// ── 工具 ─────────────────────────────────────────────────────────────────

async function login(page: Page): Promise<string> {
  await page.goto(`${BASE}/login`)
  await page.fill('input[placeholder="用户名"]', 'admin')
  await page.fill('input[placeholder="密码"]', 'admin123')
  await page.click('button[type="submit"]')
  await page.waitForURL(`${BASE}/overview`, { timeout: 10000 })
  const token = await page.evaluate(() => localStorage.getItem('token') ?? '')
  expect(token, 'JWT token must be set').toBeTruthy()
  return token
}

async function apiCall(
  request: APIRequestContext,
  method: 'GET' | 'POST' | 'PUT' | 'DELETE',
  url: string,
  token: string,
  data?: unknown,
): Promise<any> {
  const res = await request.fetch(`${API}${url}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    data: data ? JSON.stringify(data) : undefined,
  })
  const body = await res.json()
  if (!res.ok()) throw new Error(`${method} ${url} → ${res.status()}: ${JSON.stringify(body).slice(0, 300)}`)
  return body
}

async function shot(page: Page, outDir: string, name: string) {
  await page.screenshot({ path: path.join(outDir, `${name}.jpg`), type: 'jpeg', quality: 75 })
}

// 统一统计函数：从 SQLite 查实体/边/逻辑/动作
async function collectStats(request: APIRequestContext, token: string, ontologyId: string) {
  // 实体 — v1 endpoint 返回 {data: [...]}, 无分页
  const entBody = await apiCall(request, 'GET', `/api/v1/ontologies/${ontologyId}/entities`, token)
  const entities: number = Array.isArray(entBody.data) ? entBody.data.length : 0

  // 边 (从 v1 graph 接口，SQLite fallback)
  const graphBody = await apiCall(request, 'GET', `/api/v1/ontologies/${ontologyId}/graph?limit=5000`, token)
  const edges: number = (graphBody.data?.edges ?? []).length

  // 逻辑规则
  const logicBody = await apiCall(request, 'GET', `/api/v1/ontologies/${ontologyId}/logic`, token)
  const logic: number = (logicBody.data ?? []).length

  // 动作
  const actionBody = await apiCall(request, 'GET', `/api/v1/ontologies/${ontologyId}/actions`, token)
  const actions: number = (actionBody.data ?? []).length

  return { entities, edges, logic, actions }
}

async function pollExtraction(
  request: APIRequestContext,
  token: string,
  ontologyId: string,
  taskId: string,
  timeoutMs = 360_000,
): Promise<string> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 4000))
    const body = await apiCall(request, 'GET', `/api/v1/ontologies/${ontologyId}/execute/status?task_id=${taskId}`, token)
    const status: string = body.data?.status ?? body.status
    console.log(`    polling: status=${status} pct=${body.data?.progress?.pct ?? 0}%`)
    if (status === 'completed' || status === 'failed') return status
  }
  throw new Error('LLM extraction timed out')
}

// ── Pipeline Mapping 路径 ─────────────────────────────────────────────────

async function runPipelineMapping(
  page: Page,
  request: APIRequestContext,
  token: string,
  domain: Domain,
  ts: number,
  outDir: string,
) {
  const domainDir = path.join(TEST_DATA, domain)
  const files = fs.readdirSync(domainDir).filter(f => fs.statSync(path.join(domainDir, f)).isFile()).sort()
  console.log(`\n  [${domain}][Pipeline] 文件数: ${files.length}`)

  // 1. 上传文件到 v2 datasets
  const uploaded: Array<{ name: string; dataset_id: string }> = []
  for (const filename of files) {
    const res = await request.post(`${API}/api/v2/datasets/upload`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: { file: { name: filename, mimeType: 'application/octet-stream', buffer: fs.readFileSync(path.join(domainDir, filename)) } },
    })
    const body = await res.json()
    expect(res.ok(), `upload ${filename}: ${JSON.stringify(body)}`).toBeTruthy()
    uploaded.push({ name: filename, dataset_id: body.data.id })
  }
  console.log(`    上传完成: ${uploaded.length} 个文件`)

  // 2. 创建 Pipeline
  const plBody = await apiCall(request, 'POST', '/api/v2/pipelines', token, {
    name: `E2E_${domain}_Pipeline_${ts}`,
    domain,
    description: `三领域对比 E2E — ${domain} Pipeline`,
    route: 'A',
    definition: {
      schema_version: '2.0',
      nodes: [
        { id: 'connector', type: 'connector', label: `${domain}数据源`, position: { x: 80, y: 180 },
          config: { source_type: 'file', files: uploaded } },
        { id: 'storage',   type: 'storage',   label: '分类存储',       position: { x: 330, y: 180 },
          config: { storage_mode: 'auto' } },
        { id: 'transform', type: 'transform', label: '数据转换',       position: { x: 580, y: 180 },
          config: { path: 'auto', steps: [] } },
        { id: 'output',    type: 'output',    label: '结构化输出',     position: { x: 830, y: 180 },
          config: { dataset_type: 'curated_dataset', primary_key: [] } },
      ],
      edges: [
        { id: 'e1', source: 'connector', target: 'storage' },
        { id: 'e2', source: 'storage',   target: 'transform' },
        { id: 'e3', source: 'transform', target: 'output' },
      ],
    },
  })
  const pipelineId: string = plBody.id ?? plBody.data?.id
  expect(pipelineId).toBeTruthy()
  console.log(`    Pipeline: ${pipelineId.slice(0, 8)}`)

  // 3. 前端截图 Pipeline Builder
  await page.goto(`${BASE}/data/pipelines/${pipelineId}`)
  await page.waitForTimeout(1500)
  await shot(page, outDir, `${domain}_pm_01_pipeline_builder`)

  // 4. 同步运行 Pipeline
  console.log(`    运行 Pipeline...`)
  const runBody = await apiCall(request, 'POST', `/api/v2/pipelines/${pipelineId}/run-sync`, token)
  expect(runBody.status, `Pipeline 运行失败: ${JSON.stringify(runBody).slice(0, 200)}`).toBe('success')
  const curatedIds: string[] = runBody.stats?.curated_dataset_ids ?? []
  console.log(`    Pipeline 完成: ${curatedIds.length} 个 curated dataset`)

  // 5. Publish
  await apiCall(request, 'POST', `/api/v2/pipelines/${pipelineId}/publish`, token)

  // 6. 批准全部 curated datasets
  for (const id of curatedIds) {
    await apiCall(request, 'POST', `/api/v2/curated/${id}/review?action=approve`, token)
  }

  // 7. 创建 Pipeline Mapping 本体
  const ontoBody = await apiCall(request, 'POST', '/api/v1/ontologies', token, {
    name:        `E2E_${domain}_PipelineMapping_${ts}`,
    domain,
    description: `三领域对比 — ${domain} Pipeline Mapping`,
    build_mode:  'pipeline_mapping',
  })
  const ontologyId: string = ontoBody.data?.id ?? ontoBody.id
  expect(ontologyId).toBeTruthy()
  console.log(`    本体: ${ontologyId.slice(0, 8)}`)

  // 8. 为每个 curated dataset 创建 mapping
  const outputs: Array<{ curated_dataset_id: string; source_file?: string }> =
    runBody.stats?.meta?.outputs ?? curatedIds.map((id: string) => ({ curated_dataset_id: id }))

  for (const output of outputs) {
    const sf = output.source_file ?? ''
    const entityClass = sf
      ? sf.replace(/\.[^.]+$/, '').split(/[_\-\s]+/).map((w: string) => w.charAt(0).toUpperCase() + w.slice(1)).join('')
      : `Dataset_${output.curated_dataset_id.slice(0, 6)}`
    await apiCall(request, 'POST', `/api/v2/ontologies/${ontologyId}/mappings`, token, {
      curated_dataset_id: output.curated_dataset_id,
      entity_class: entityClass,
      field_mapping: { '__primary_key__': '__row_hash__' },
      confidence: 1.0,
    })
  }

  // 9. Build all
  console.log(`    构建本体...`)
  const buildBody = await apiCall(request, 'POST', `/api/v2/ontologies/${ontologyId}/mappings/build-all`, token)
  console.log(`    构建完成: 概念=${buildBody.total_concepts} 实例=${buildBody.total_instances} 关系=${buildBody.total_relations} 逻辑=${buildBody.total_logic} 动作=${buildBody.total_actions}`)

  // 10. 前端截图本体详情 + 图谱
  await page.goto(`${BASE}/ontologies/${ontologyId}`)
  await page.waitForTimeout(1500)
  await shot(page, outDir, `${domain}_pm_02_ontology`)

  await page.goto(`${BASE}/ontologies/${ontologyId}?tab=graph`)
  await page.waitForTimeout(3000)
  await shot(page, outDir, `${domain}_pm_03_graph`)

  // 11. 统一统计（从 SQLite 直接数 — 与 pipeline build 结果相同，但统一口径）
  const stats = await collectStats(request, token, ontologyId)
  return { pipelineId, ontologyId, stats }
}

// ── 简易 LLM 路径 ────────────────────────────────────────────────────────

async function runSimpleLLM(
  page: Page,
  request: APIRequestContext,
  token: string,
  domain: Domain,
  ts: number,
  outDir: string,
) {
  const domainDir = path.join(TEST_DATA, domain)
  const files = fs.readdirSync(domainDir).filter(f => fs.statSync(path.join(domainDir, f)).isFile()).sort()
  console.log(`\n  [${domain}][简易LLM] 文件数: ${files.length}`)

  // 1. 创建本体
  const ontoBody = await apiCall(request, 'POST', '/api/v1/ontologies', token, {
    name:        `E2E_${domain}_SimpleLLM_${ts}`,
    domain,
    description: `三领域对比 — ${domain} 简易LLM`,
    build_mode:  'simple_llm',
  })
  const ontologyId: string = ontoBody.data?.id ?? ontoBody.id
  expect(ontologyId).toBeTruthy()
  console.log(`    本体: ${ontologyId.slice(0, 8)}`)

  // 2. 截图文件上传 tab
  await page.goto(`${BASE}/login`)
  await page.evaluate((tok: string) => {
    localStorage.setItem('token', tok)
    localStorage.setItem('auth-store', JSON.stringify({ state: { token: tok, user: { username: 'admin', role: 'admin' } }, version: 0 }))
  }, token)
  await page.goto(`${BASE}/ontologies/${ontologyId}?tab=files`)
  await page.waitForTimeout(1500)
  await shot(page, outDir, `${domain}_llm_01_files_tab`)

  // 3. 上传所有领域文件
  const fileIds: string[] = []
  for (const filename of files) {
    const res = await request.post(`${API}/api/v1/ontologies/${ontologyId}/files`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: { file: { name: filename, mimeType: 'application/octet-stream', buffer: fs.readFileSync(path.join(domainDir, filename)) } },
    })
    const body = await res.json()
    expect(res.ok(), `upload ${filename}: ${JSON.stringify(body)}`).toBeTruthy()
    fileIds.push(body.data.id)
  }
  console.log(`    上传完成: ${fileIds.length} 个文件`)

  await page.reload(); await page.waitForTimeout(1000)
  await shot(page, outDir, `${domain}_llm_02_files_uploaded`)

  // 4. 触发 LLM 提取
  const execBody = await apiCall(request, 'POST', `/api/v1/ontologies/${ontologyId}/execute`, token, {
    prompt_id:  PROMPT_BY_DOMAIN[domain],
    model_id:   MODEL_ID,
    model_name: MODEL_NAME,
    file_ids:   fileIds,
    constraints: [],
  })
  const taskId: string = execBody.data?.task_id ?? execBody.task_id
  expect(taskId).toBeTruthy()
  console.log(`    提取任务: ${taskId.slice(0, 8)}, 等待完成...`)

  // 截图提取中
  await page.goto(`${BASE}/ontologies/${ontologyId}?tab=files`)
  await page.waitForTimeout(1000)
  await shot(page, outDir, `${domain}_llm_03_extracting`)

  // 5. 轮询
  const finalStatus = await pollExtraction(request, token, ontologyId, taskId, 1800_000)
  console.log(`    提取结果: ${finalStatus}`)

  // 6. 前端截图结果
  await page.goto(`${BASE}/ontologies/${ontologyId}`)
  await page.waitForTimeout(1500)
  await shot(page, outDir, `${domain}_llm_04_ontology`)

  await page.goto(`${BASE}/ontologies/${ontologyId}?tab=graph`)
  await page.waitForTimeout(3000)
  await shot(page, outDir, `${domain}_llm_05_graph`)

  // 7. 统计
  const stats = await collectStats(request, token, ontologyId)
  return { ontologyId, taskId, finalStatus, stats }
}

// ── 测试入口 ──────────────────────────────────────────────────────────────

test.describe.configure({ mode: 'serial' })

test.describe('三领域对比：Pipeline Mapping vs 简易 LLM', () => {
  const ts = Date.now()
  const outDir = path.resolve(__dirname, '../../../../test-results/three-domains-comparison', String(ts))
  let token = ''

  interface Row { domain: string; path: string; entities: number; edges: number; logic: number; actions: number; ontologyId: string; error?: string }
  const rows: Row[] = []

  test.beforeAll(async ({ browser }) => {
    fs.mkdirSync(outDir, { recursive: true })
    const pg = await browser.newPage()
    token = await login(pg)
    await pg.close()
    console.log(`\n输出目录: ${outDir}`)
  })

  test.afterAll(() => {
    // 保存 JSON
    fs.writeFileSync(path.join(outDir, 'summary.json'), JSON.stringify(rows, null, 2), 'utf-8')

    // Markdown 表格
    const header = '| 业务域 | 路径 | 实体数 | 边数 | 逻辑规则数 | 动作数 | 本体ID |'
    const sep    = '|--------|------|--------|------|-----------|--------|--------|'
    const body   = rows.map(r =>
      r.error
        ? `| ${r.domain} | ${r.path} | ❌ | ❌ | ❌ | ❌ | ${r.error?.slice(0, 60)} |`
        : `| ${r.domain} | ${r.path} | ${r.entities} | ${r.edges} | ${r.logic} | ${r.actions} | \`${r.ontologyId.slice(0, 8)}\` |`
    )

    const table = [header, sep, ...body].join('\n')
    fs.writeFileSync(path.join(outDir, 'summary.md'), table + '\n', 'utf-8')

    console.log('\n\n======================================')
    console.log('         三领域 Ontology 汇总表')
    console.log('======================================')
    console.log(table)
    console.log('======================================\n')
    console.log(`完整结果: ${path.join(outDir, 'summary.md')}`)
  })

  // ── Pipeline Mapping 路径（3个域）──────────────────────────────────────
  for (const domain of DOMAINS) {
    test(`Pipeline Mapping — ${domain}`, async ({ page, request }) => {
      test.setTimeout(600_000)
      try {
        const result = await runPipelineMapping(page, request, token, domain, ts, outDir)
        rows.push({ domain, path: 'Pipeline Mapping', ...result.stats, ontologyId: result.ontologyId })
        expect(result.stats.entities, '应有至少 1 个实体').toBeGreaterThan(0)
      } catch (err: any) {
        rows.push({ domain, path: 'Pipeline Mapping', entities: 0, edges: 0, logic: 0, actions: 0, ontologyId: '', error: err.message })
        await page.screenshot({ path: path.join(outDir, `${domain}_pipeline_ERROR.jpg`), type: 'jpeg', quality: 75 }).catch(() => {})
        throw err
      }
    })
  }

  // ── 简易 LLM 路径（3个域）─────────────────────────────────────────────
  for (const domain of DOMAINS) {
    test(`简易 LLM — ${domain}`, async ({ page, request }) => {
      test.setTimeout(3_600_000) // 60 min: deepseek processes 8 files × ~3 min each
      try {
        const result = await runSimpleLLM(page, request, token, domain, ts, outDir)
        rows.push({ domain, path: '简易 LLM', ...result.stats, ontologyId: result.ontologyId })
        expect(result.finalStatus, '提取应成功').toBe('completed')
        expect(result.stats.entities, '应有至少 1 个实体').toBeGreaterThan(0)
      } catch (err: any) {
        rows.push({ domain, path: '简易 LLM', entities: 0, edges: 0, logic: 0, actions: 0, ontologyId: '', error: err.message })
        await page.screenshot({ path: path.join(outDir, `${domain}_llm_ERROR.jpg`), type: 'jpeg', quality: 75 }).catch(() => {})
        throw err
      }
    })
  }
})
