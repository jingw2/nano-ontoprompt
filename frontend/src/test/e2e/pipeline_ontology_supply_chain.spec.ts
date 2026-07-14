/// <reference types="node" />

import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const BASE = 'http://localhost:5173'
const API = 'http://localhost:8000'
const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const SUPPLY_CHAIN_DIR = path.resolve(__dirname, '../../../../test_data/供应链')

const ENTITY_BY_FILE: Record<string, string> = {
  'inventory_transactions.csv': 'InventoryTransactions',
  'logistics_performance.csv': 'LogisticsPerformance',
  'procurement_policy.docx': 'ProcurementPolicy',
  'supplier_database.xlsx': 'SupplierDatabase',
  'supplier_orders.json': 'SupplierOrders',
  'supply_chain_review.pptx': 'SupplyChainReview',
  'supply_chain_strategy.md': 'SupplyChainStrategy',
  'warehouse_management.pdf': 'WarehouseManagement',
}

const PK_BY_FILE: Record<string, string> = {
  'inventory_transactions.csv': '__row_hash__',
  'logistics_performance.csv': '运单号',
  'procurement_policy.docx': '__row_hash__',
  'supplier_database.xlsx': '供应商ID',
  'supplier_orders.json': 'order_id',
  'supply_chain_review.pptx': '__row_hash__',
  'supply_chain_strategy.md': '__row_hash__',
  'warehouse_management.pdf': '__row_hash__',
}

async function login(page: Page) {
  for (const password of ['admin123', 'admin123']) {
    await page.goto(`${BASE}/login`)
    await page.fill('input[placeholder="用户名"]', 'admin')
    await page.fill('input[placeholder="密码"]', password)
    await page.click('button[type="submit"]')
    try {
      await page.waitForURL(`${BASE}/overview`, { timeout: 5000 })
      return
    } catch {
      // Try the next known local development password.
    }
  }
  throw new Error('Unable to login as admin with known local passwords')
}

async function apiJson(request: APIRequestContext, method: 'GET' | 'POST' | 'PUT', url: string, token: string, data?: unknown) {
  const response = await request.fetch(`${API}${url}`, {
    method,
    headers: { Authorization: `Bearer ${token}` },
    data,
  })
  expect(response.ok(), `${method} ${url}: ${await response.text()}`).toBeTruthy()
  return response.json()
}

async function shot(page: Page, outDir: string, name: string) {
  await page.screenshot({
    path: path.join(outDir, `${name}.jpg`),
    type: 'jpeg',
    quality: 75,
    fullPage: false,
  })
}

test.describe('Supply chain pipeline to ontology mapping', () => {
  test('creates, runs, publishes and maps all supply-chain fixtures', async ({ page, request }) => {
    test.setTimeout(180000)
    const ts = Date.now()
    const outDir = path.resolve(__dirname, '../../../../test-results/supply-chain-e2e', String(ts))
    fs.mkdirSync(outDir, { recursive: true })

    await login(page)
    const token = await page.evaluate(() => localStorage.getItem('token') || '')
    expect(token).toBeTruthy()

    const filenames = fs.readdirSync(SUPPLY_CHAIN_DIR).filter((name: string) => ENTITY_BY_FILE[name]).sort()
    expect(filenames).toHaveLength(8)

    const uploaded: Array<{ name: string; dataset_id: string }> = []
    for (const name of filenames) {
      const filePath = path.join(SUPPLY_CHAIN_DIR, name)
      const response = await request.post(`${API}/api/v2/datasets/upload`, {
        headers: { Authorization: `Bearer ${token}` },
        multipart: {
          file: {
            name,
            mimeType: 'application/octet-stream',
            buffer: fs.readFileSync(filePath),
          },
        },
      })
      expect(response.ok(), `upload ${name}: ${await response.text()}`).toBeTruthy()
      const body = await response.json()
      uploaded.push({ name, dataset_id: body.data.id })
    }

    const pipeline = await apiJson(request, 'POST', '/api/v2/pipelines', token, {
      name: `SC_GOLDEN_E2E_${ts}`,
      domain: '供应链',
      description: 'Playwright golden flow: pipeline -> curated -> ontology mapping',
      route: 'A',
      definition: {
        schema_version: '2.0',
        nodes: [
          { id: 'connector_all', type: 'connector', label: '供应链数据源', position: { x: 80, y: 180 }, config: { source_type: 'file', files: uploaded } },
          { id: 'storage_all', type: 'storage', label: '分类存储', position: { x: 330, y: 180 }, config: { storage_mode: 'auto' } },
          { id: 'transform_all', type: 'transform', label: '分路径转换', position: { x: 580, y: 180 }, config: { path: 'auto', steps: [{ op: 'vlm_extract', params: { strategy: 'vlm' } }] } },
          { id: 'output_all', type: 'output', label: '结构化输出', position: { x: 830, y: 180 }, config: { dataset_type: 'curated_dataset', primary_key: [] } },
        ],
        edges: [
          { id: 'e1', source: 'connector_all', target: 'storage_all' },
          { id: 'e2', source: 'storage_all', target: 'transform_all' },
          { id: 'e3', source: 'transform_all', target: 'output_all' },
        ],
      },
    })

    await page.goto(`${BASE}/pipelines/${pipeline.id}`)
    await expect(page.locator('text=供应链数据源')).toBeVisible()
    await shot(page, outDir, '01-pipeline-seeded')

    const run = await apiJson(request, 'POST', `/api/v2/pipelines/${pipeline.id}/run-sync`, token)
    expect(run.status).toBe('success')
    const curatedIds = run.stats.curated_dataset_ids as string[]
    expect(curatedIds).toHaveLength(8)

    await page.goto(`${BASE}/pipelines/${pipeline.id}`)
    await expect(page.locator('text=8 Curated Datasets')).toBeVisible({ timeout: 15000 })
    await shot(page, outDir, '02-pipeline-after-run')

    await apiJson(request, 'POST', `/api/v2/pipelines/${pipeline.id}/publish`, token)
    await page.goto(`${BASE}/pipelines/${pipeline.id}`)
    await expect(page.locator('text=published')).toBeVisible()
    await shot(page, outDir, '03-pipeline-published')

    for (const id of curatedIds) {
      await apiJson(request, 'POST', `/api/v2/curated/${id}/review?action=approve`, token)
    }
    await page.goto(`${BASE}/pipelines/curated`)
    await expect(page.locator(`text=SC_GOLDEN_E2E_${ts}`).first()).toBeVisible({ timeout: 15000 })
    await expect(page.locator('text=已审批').first()).toBeVisible()
    await shot(page, outDir, '04-curated-approved')

    const ontologyBody = await apiJson(request, 'POST', '/api/v1/ontologies', token, {
      name: `供应链 Ontology Golden ${ts}`,
      domain: '供应链',
      description: 'Generated by Playwright supply-chain golden e2e',
      build_mode: 'pipeline_mapping',
    })
    const ontologyId = ontologyBody.data?.id || ontologyBody.id
    expect(ontologyId).toBeTruthy()

    const outputs = run.stats.meta.outputs as Array<{ curated_dataset_id: string; source_file: string }>
    for (const output of outputs) {
      const sourceFile = output.source_file
      await apiJson(request, 'POST', `/api/v2/ontologies/${ontologyId}/mappings`, token, {
        curated_dataset_id: output.curated_dataset_id,
        entity_class: ENTITY_BY_FILE[sourceFile],
        field_mapping: { '__primary_key__': PK_BY_FILE[sourceFile] || '__row_hash__' },
        primary_key_column: PK_BY_FILE[sourceFile] || '__row_hash__',
        confidence: 1.0,
      })
    }

    const build = await apiJson(request, 'POST', `/api/v2/ontologies/${ontologyId}/mappings/build-all`, token)
    expect(build.total_entities).toBeGreaterThanOrEqual(100)
    expect(build.total_relations).toBeGreaterThanOrEqual(50)
    expect(build.total_logic).toBeGreaterThanOrEqual(10)
    expect(build.total_actions).toBeGreaterThanOrEqual(15)
    expect(build.link_mappings_inferred).toBeGreaterThanOrEqual(1)

    await page.goto(`${BASE}/ontologies/${ontologyId}`)
    await page.click('button:has-text("Curated 数据集")')
    await expect(page.locator('text=SupplierDatabase')).toBeVisible({ timeout: 15000 })
    await shot(page, outDir, '05-ontology-curated-mappings')

    await page.click('button:has-text("实体")')
    await expect(page.locator('table')).toBeVisible()
    await expect(page.locator('tbody tr').filter({ hasText: 'InventoryTransactions' }).first()).toBeVisible()
    await shot(page, outDir, '06-ontology-entities')

    await page.click('button:has-text("逻辑规则")')
    await expect(page.locator('text=Mapping Rule').first()).toBeVisible()
    await shot(page, outDir, '07-ontology-logic')

    await page.click('button:has-text("动作")')
    await expect(page.locator('text=Create').first()).toBeVisible()
    await shot(page, outDir, '08-ontology-actions')

    await page.goto(`${BASE}/ontologies/${ontologyId}?tab=graph`)
    await expect(page.locator('text=/SQLite 图谱|Neo4j 已连接/')).toBeVisible({ timeout: 15000 })
    await expect(page.getByTestId('ontology-graph-canvas')).toBeVisible({ timeout: 15000 })
    await page.waitForTimeout(1200)
    await shot(page, outDir, '09-ontology-graph')

    fs.writeFileSync(path.join(outDir, 'result.json'), JSON.stringify({
      pipeline_id: pipeline.id,
      ontology_id: ontologyId,
      curated_count: curatedIds.length,
      build,
    }, null, 2), 'utf-8')
  })
})
