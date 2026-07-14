/**
 * 供应链 Pipeline → Ontology 全流程演示
 *
 * 流程：
 *   1. Upload inventory_transactions.csv + logistics_performance.csv
 *   2. 创建 Route A Pipeline (结构化)
 *   3. 同步运行 Pipeline → 生成 Curated Dataset
 *   4. 获取质量报告 + Approve
 *   5. 创建本体 + Mapping Suggest (LLM)
 *   6. Apply Mapping → 写入图谱
 *   7. Playwright 截取每个阶段 UI
 */
import { chromium } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import http from 'http';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE = 'http://localhost:5173';
const API  = 'http://localhost:8000';
const SS   = path.join(__dirname, 'pipeline_screenshots');
fs.mkdirSync(SS, { recursive: true });

const SC = path.resolve(__dirname, '../test_data/供应链');

// ── 工具函数 ─────────────────────────────────────────────────────────────────
async function apiCall(method, path, body, token, isFormData = false) {
  return new Promise((resolve, reject) => {
    const data = isFormData ? body : (body ? JSON.stringify(body) : null);
    const headers = { 'Authorization': 'Bearer ' + token };
    if (!isFormData && body) headers['Content-Type'] = 'application/json';
    if (isFormData) headers['Content-Type'] = body.contentType;

    const opts = { hostname: 'localhost', port: 8000, path, method, headers };
    const req = http.request(opts, res => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(d) }); }
        catch { resolve({ status: res.statusCode, body: d }); }
      });
    });
    req.on('error', reject);
    if (isFormData) req.write(body.data);
    else if (data) req.write(data);
    req.end();
  });
}

async function uploadFile(filePath, token) {
  const fileName = path.basename(filePath);
  const fileBytes = fs.readFileSync(filePath);
  const boundary = '----FormBoundary' + Date.now();
  const CRLF = '\r\n';
  const head = Buffer.from(
    `--${boundary}${CRLF}Content-Disposition: form-data; name="file"; filename="${fileName}"${CRLF}Content-Type: text/csv${CRLF}${CRLF}`,
    'utf8'
  );
  const tail = Buffer.from(`${CRLF}--${boundary}--${CRLF}`, 'utf8');
  const formData = Buffer.concat([head, fileBytes, tail]);

  return new Promise((resolve, reject) => {
    const opts = {
      hostname: 'localhost', port: 8000, path: '/api/v2/datasets/upload',
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + token,
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
        'Content-Length': formData.length,
      }
    };
    const req = http.request(opts, res => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => {
        try { resolve(JSON.parse(d)); } catch { resolve(d); }
      });
    });
    req.on('error', reject);
    req.write(formData); req.end();
  });
}

let step = 0;
async function shot(page, label) {
  const file = path.join(SS, `${String(step).padStart(2,'0')}_${label}.png`);
  await page.screenshot({ path: file, fullPage: false });
  console.log(`  📸 ${String(step).padStart(2,'0')}_${label}.png`);
  step++;
  return file;
}
async function wait(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── 主流程 ───────────────────────────────────────────────────────────────────
console.log('═══════════════════════════════════════════════════════════');
console.log('  供应链 Pipeline → Ontology 全流程演示');
console.log('═══════════════════════════════════════════════════════════');

// 1. 获取 Token
const loginRes = await apiCall('POST', '/api/v1/auth/login', { username: 'admin', password: 'admin123' });
// 注：若 admin123 失败则尝试 admin123
const token = loginRes.body?.data?.access_token;
if (!token) { console.error('Login failed:', loginRes); process.exit(1); }
console.log('\n✅ [1] 登录成功');

// 1b. 创建文件类型 Connection 记录（让 ConnectionsTab 有数据显示）
console.log('\n🔌 [1b] 创建供应链文件 Connection...');
const connRes = await apiCall('POST', '/api/v2/connections', {
  name: '供应链数据文件-Connection',
  kind: 'file',
  config: { prefix: '供应链/' }
}, token);
const connId = connRes?.body?.data?.id || connRes?.body?.id;
console.log(`  Connection ID: ${connId?.slice(0,8) ?? '?'}`);

// 2. 上传供应链 CSV 文件
console.log('\n📤 [2] 上传供应链数据集...');
const invRes  = await uploadFile(path.join(SC, 'inventory_transactions.csv'), token);
const logRes  = await uploadFile(path.join(SC, 'logistics_performance.csv'), token);
const invDsId  = invRes?.data?.id;
const logDsId  = logRes?.data?.id;
console.log(`  📊 inventory_transactions → ${invDsId?.slice(0,8)}`);
console.log(`  📊 logistics_performance  → ${logDsId?.slice(0,8)}`);

// 3. 创建 Route A Pipeline
console.log('\n🔄 [3] 创建 Transform Pipeline...');
const pl1Res = await apiCall('POST', '/api/v2/pipelines', {
  name: '供应链-库存交易-Pipeline',
  source_dataset_id: invDsId,
  route: 'A',
  spec: { steps: ['schema_inference', 'cleansing'] }
}, token);
const pl2Res = await apiCall('POST', '/api/v2/pipelines', {
  name: '供应链-物流绩效-Pipeline',
  source_dataset_id: logDsId,
  route: 'A',
  spec: { steps: ['schema_inference', 'cleansing'] }
}, token);
const pl1Id = pl1Res?.body?.data?.id || pl1Res?.body?.id;
const pl2Id = pl2Res?.body?.data?.id || pl2Res?.body?.id;
console.log(`  🔄 Pipeline 1 → ${pl1Id?.slice(0,8)}`);
console.log(`  🔄 Pipeline 2 → ${pl2Id?.slice(0,8)}`);

// 4. 同步运行 Pipeline（绕过 Celery）
console.log('\n▶️  [4] 运行 Pipeline（同步）...');
let curated1Id, curated2Id;
if (pl1Id) {
  const run1 = await apiCall('POST', `/api/v2/pipelines/${pl1Id}/run-sync`, null, token);
  console.log(`  Pipeline 1 状态: ${run1?.body?.status ?? run1?.body?.data?.status ?? JSON.stringify(run1?.body).slice(0,60)}`);
  // 获取该 pipeline 生成的 curated dataset id
  const runId1 = run1?.body?.run_id || run1?.body?.data?.run_id;
  if (runId1) {
    const runDetail = await apiCall('GET', `/api/v2/pipelines/runs/${runId1}`, null, token);
    curated1Id = runDetail?.body?.stats?.curated_dataset_id || runDetail?.body?.data?.stats?.curated_dataset_id;
  }
}
if (pl2Id) {
  const run2 = await apiCall('POST', `/api/v2/pipelines/${pl2Id}/run-sync`, null, token);
  console.log(`  Pipeline 2 状态: ${run2?.body?.status ?? run2?.body?.data?.status ?? JSON.stringify(run2?.body).slice(0,60)}`);
  const runId2 = run2?.body?.run_id;
  if (runId2) {
    const runDetail = await apiCall('GET', `/api/v2/pipelines/runs/${runId2}`, null, token);
    curated2Id = runDetail?.body?.stats?.curated_dataset_id;
  }
}

// 5. 获取 Curated Datasets（kind = "curated"）
console.log('\n📋 [5] 查看 Curated Datasets...');
const dsListRes = await apiCall('GET', '/api/v2/datasets', null, token);
// API 返回裸数组
const allDs = Array.isArray(dsListRes?.body) ? dsListRes.body : (dsListRes?.body?.data ?? []);
const curatedDs = allDs.filter(d => d.kind === 'curated');
console.log(`  找到 ${curatedDs.length} 个 curated 数据集（共 ${allDs.length} 个）`);
const targetCurated = curatedDs[curatedDs.length - 1];
if (targetCurated) console.log(`  使用: ${targetCurated.name} (${targetCurated.id?.slice(0,8)})`);

// 6. 获取质量报告（通过 curated 路由）
if (targetCurated) {
  console.log('\n🔍 [6] 质量报告...');
  const qr = await apiCall('GET', `/api/v2/curated/${targetCurated.id}/quality`, null, token);
  const report = qr?.body?.data ?? qr?.body;
  if (report?.overall_score !== undefined) {
    console.log(`  综合质量分: ${(report.overall_score * 100).toFixed(1)}%`);
    console.log(`  行数: ${report.row_count ?? '?'}, 列数: ${report.column_count ?? '?'}`);
  } else {
    console.log(`  质量报告响应: ${JSON.stringify(qr?.body).slice(0,120)}`);
  }
}

// 7. 创建本体
console.log('\n🧩 [7] 创建供应链本体...');
const ontoRes = await apiCall('POST', '/api/v1/ontologies', {
  name: `供应链Pipeline本体_${Date.now()}`,
  domain: '供应链',
  description: '从供应链库存交易和物流绩效数据通过 Pipeline Mapping 构建的本体'
}, token);
const ontoId = ontoRes?.body?.data?.id || ontoRes?.body?.id;
console.log(`  本体 ID: ${ontoId?.slice(0,8)}`);

// 8. LLM Mapping 建议 + 创建 Mapping + Apply
if (targetCurated && ontoId) {
  console.log('\n🤖 [8] LLM Mapping 建议...');
  // 读取 curated 数据集的样本行（先读版本1）
  const versionsRes = await apiCall('GET', `/api/v2/datasets/${targetCurated.id}/versions`, null, token);
  const versions = Array.isArray(versionsRes?.body) ? versionsRes.body : (versionsRes?.body?.data ?? []);
  let sampleRows = [];
  let columns = ['transaction_id', 'product_id', 'quantity', 'date', 'warehouse_id'];
  if (versions.length > 0) {
    // 读全量数据（最多500行）写入实体
    const previewRes = await apiCall('GET', `/api/v2/datasets/${targetCurated.id}/versions/1/preview?limit=500`, null, token);
    sampleRows = Array.isArray(previewRes?.body) ? previewRes.body : (previewRes?.body?.data ?? []);
    if (sampleRows.length > 0) columns = Object.keys(sampleRows[0]);
  }
  console.log(`  数据行数: ${sampleRows.length}`);
  console.log(`  列: ${columns.slice(0,5).join(', ')}${columns.length > 5 ? '...' : ''}`);

  const suggestRes = await apiCall('POST', `/api/v2/ontologies/${ontoId}/mappings/suggest`, {
    dataset_name: targetCurated.name,
    columns,
    sample_rows: sampleRows.slice(0, 3),
    ontology_domain: '供应链'
  }, token);
  const suggestion = suggestRes?.body ?? {};
  console.log(`  实体类型: ${suggestion.entity_class_cn ?? suggestion.entity_class ?? '?'} (${suggestion.entity_class ?? '?'})`);
  console.log(`  字段映射数: ${suggestion.field_mappings?.length ?? 0}`);
  suggestion.field_mappings?.slice(0, 3).forEach(fm =>
    console.log(`    ${fm.column_name} → ${fm.property_name}`)
  );

  // 创建 Mapping 记录
  console.log('\n📝 [9] 创建 Mapping 配置...');
  const fieldMap = {};
  (suggestion.field_mappings ?? []).forEach(fm => { fieldMap[fm.column_name] = fm.property_name; });
  if (suggestion.primary_key_column) fieldMap['__primary_key__'] = suggestion.primary_key_column;

  const createMappingRes = await apiCall('POST', `/api/v2/ontologies/${ontoId}/mappings`, {
    curated_dataset_id: targetCurated.id,
    entity_class: suggestion.entity_class || 'InventoryTransaction',
    field_mapping: fieldMap,
    confidence: 0.85
  }, token);
  const mappingId = createMappingRes?.body?.mapping_id;
  console.log(`  Mapping ID: ${mappingId?.slice(0,8) ?? '?'}`);

  // Apply Mapping → 写入 Neo4j（传递样本数据）
  if (mappingId) {
    console.log('\n✅ [10] Apply Mapping → 写入图谱...');
    const applyRes = await apiCall('POST', `/api/v2/ontologies/${ontoId}/mappings/${mappingId}/apply`,
      sampleRows, token);
    console.log(`  Apply 结果: ${JSON.stringify(applyRes?.body).slice(0, 120)}`);
  }
}

console.log('\n✅ 后端流程完成！启动 Playwright UI 截图...\n');

// ── Playwright UI 截图 ────────────────────────────────────────────────────────
const browser = await chromium.launch({
  headless: true,
  executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  args: ['--no-sandbox', '--disable-setuid-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
await ctx.addInitScript(t => {
  localStorage.setItem('auth-store', JSON.stringify({ state: { user: { id: 'a', username: 'admin', role: 'admin' }, token: t }, version: 0 }));
  localStorage.setItem('token', t);
  localStorage.setItem('lang', 'zh');
}, token);
await ctx.route('http://localhost:5173/api/**', async route => {
  const url = route.request().url().replace('http://localhost:5173', 'http://localhost:8000');
  try { const r = await route.fetch({ url }); await route.fulfill({ response: r }); }
  catch { await route.continue(); }
});
const page = await ctx.newPage();

// ── A. Connections Tab
console.log('[UI-A] Pipelines → Connections');
await page.goto(`${BASE}/pipelines/connections`);
await page.waitForLoadState('networkidle'); await wait(1500);
await shot(page, 'A_connections_tab');

// ── B. Datasets Tab（含新上传的供应链数据）
console.log('[UI-B] Pipelines → Datasets');
await page.goto(`${BASE}/pipelines/datasets`);
await page.waitForLoadState('networkidle'); await wait(1500);
await shot(page, 'B_datasets_tab');

// ── C. Transforms Tab（含新建的 Pipeline）
console.log('[UI-C] Pipelines → Transforms');
await page.goto(`${BASE}/pipelines/transforms`);
await page.waitForLoadState('networkidle'); await wait(1500);
await shot(page, 'C_transforms_tab');

// ── D. Curated Tab（含质量状态）
console.log('[UI-D] Pipelines → Curated');
await page.goto(`${BASE}/pipelines/curated`);
await page.waitForLoadState('networkidle'); await wait(1500);
await shot(page, 'D_curated_tab');

// 展开一个 curated dataset 看质量报告
const expandBtn = page.locator('button[title*="展开"], button:has-text("详情"), [data-expand]').first();
if (await expandBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
  await expandBtn.click(); await wait(1000);
  await shot(page, 'D_curated_expanded');
}

// ── E. Ontologies → 向导页
console.log('[UI-E] 双路径向导');
await page.goto(`${BASE}/ontologies/new`);
await page.waitForLoadState('networkidle'); await wait(1500);
await shot(page, 'E_ontology_wizard');

// ── F. 本体详情（新建的供应链本体）
if (ontoId) {
  console.log(`[UI-F] 本体详情 ${ontoId.slice(0,8)}`);
  await page.goto(`${BASE}/ontologies/${ontoId}`);
  await page.waitForLoadState('networkidle'); await wait(2000);
  await shot(page, 'F_ontology_info_tab');

  // 实体 Tab
  const entBtn = page.locator('button:has-text("实体")').first();
  if (await entBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await entBtn.click(); await wait(1500);
    await shot(page, 'F_entities_tab');
  }

  // 图谱 Tab
  const graphBtn = page.locator('button:has-text("知识图谱")').first();
  if (await graphBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await graphBtn.click(); await wait(3000);
    await shot(page, 'F_graph_tab');
  }
}

// ── G. 本体列表（含实体/关系数）
console.log('[UI-G] 本体列表（含计数）');
await page.goto(`${BASE}/ontologies`);
await page.waitForLoadState('networkidle'); await wait(1500);
await shot(page, 'G_ontology_list');

await browser.close();
console.log(`\n═══════════════════════════════════════════════════════════`);
console.log(`  完成！共 ${step} 张截图 → ${SS}`);
console.log(`═══════════════════════════════════════════════════════════`);
