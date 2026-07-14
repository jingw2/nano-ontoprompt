/**
 * 供应链全流程测试
 * - 清库重建
 * - Route A: inventory_transactions.csv (结构化)
 * - Route B: supplier_orders.json (半结构化)
 * - Route C: supply_chain_strategy.md (非结构化)
 * - v1 LLM 提取: supply_chain_strategy.md → 实体+关系(网状图谱)
 * - Playwright 截图
 */
import { chromium } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import http from 'http';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE = 'http://localhost:5173';
const API  = 'http://localhost:8000';
const SS   = path.join(__dirname, 'sc_fulltest_screenshots');
fs.mkdirSync(SS, { recursive: true });

const SC = path.resolve(__dirname, '../test_data/供应链');

// ── 工具函数 ─────────────────────────────────────────────────────────────────
async function apiCall(method, endpoint, body, token) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const headers = { 'Authorization': 'Bearer ' + token };
    if (data) { headers['Content-Type'] = 'application/json'; headers['Content-Length'] = Buffer.byteLength(data); }
    const req = http.request({ hostname: 'localhost', port: 8000, path: endpoint, method, headers }, res => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => { try { resolve({ status: res.statusCode, body: JSON.parse(d) }); } catch { resolve({ status: res.statusCode, body: d }); } });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

async function uploadFile(filePath, token) {
  const fileName = path.basename(filePath);
  const fileBytes = fs.readFileSync(filePath);
  const ext = fileName.rsplit ? fileName.split('.').pop() : path.extname(fileName).slice(1);
  const mimeMap = { csv: 'text/csv', json: 'application/json', md: 'text/markdown', xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', pdf: 'application/pdf', docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' };
  const mime = mimeMap[ext] || 'application/octet-stream';
  const boundary = '----b' + Date.now();
  const CRLF = '\r\n';
  const head = Buffer.from(`--${boundary}${CRLF}Content-Disposition: form-data; name="file"; filename="${fileName}"${CRLF}Content-Type: ${mime}${CRLF}${CRLF}`, 'utf8');
  const tail = Buffer.from(`${CRLF}--${boundary}--${CRLF}`, 'utf8');
  const formData = Buffer.concat([head, fileBytes, tail]);
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname: 'localhost', port: 8000, path: '/api/v2/datasets/upload', method: 'POST', headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': `multipart/form-data; boundary=${boundary}`, 'Content-Length': formData.length } }, res => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch { resolve(d); } });
    });
    req.on('error', reject); req.write(formData); req.end();
  });
}

let step = 0;
async function shot(page, label) {
  const file = path.join(SS, `${String(step).padStart(2,'0')}_${label}.png`);
  await page.screenshot({ path: file, fullPage: false });
  console.log(`  📸 ${String(step).padStart(2,'0')}_${label}.png`);
  step++;
}
async function wait(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── 主流程 ───────────────────────────────────────────────────────────────────
console.log('═══════════════════════════════════════════════════════════');
console.log('  供应链全流程测试（三种数据类型 + 网状图谱）');
console.log('═══════════════════════════════════════════════════════════');

// 1. 登录
let loginRes = await apiCall('POST', '/api/v1/auth/login', { username: 'admin', password: 'admin123' });
if (!loginRes.body?.data?.access_token) {
  loginRes = await apiCall('POST', '/api/v1/auth/login', { username: 'admin', password: 'admin123' });
}
const token = loginRes.body?.data?.access_token;
if (!token) { console.error('Login failed'); process.exit(1); }
console.log('\n✅ [1] 登录成功');

// 2. 清库 —— 删除所有 v2 datasets/pipelines + v1 ontologies
console.log('\n🗑️  [2] 清理旧数据...');
// 删 v2 datasets
const dsRes = await apiCall('GET', '/api/v2/datasets', null, token);
const allDs = Array.isArray(dsRes.body) ? dsRes.body : [];
for (const ds of allDs) {
  // 无 DELETE 端点，先通过日志确认数量
}
// 删 v1 ontologies（有 DELETE 端点）
const ontoRes = await apiCall('GET', '/api/v1/ontologies?page_size=100', null, token);
const ontologies = ontoRes.body?.data?.items ?? [];
let deleted = 0;
for (const onto of ontologies) {
  await apiCall('DELETE', `/api/v1/ontologies/${onto.id}`, null, token);
  deleted++;
}
console.log(`  删除了 ${deleted} 个旧本体，原有 ${allDs.length} 个 v2 数据集（无 DELETE API，跳过）`);

// 3. 上传三种类型的供应链数据
console.log('\n📤 [3] 上传供应链数据（三种类型）...');

// Route A: 结构化 CSV
const inv = await uploadFile(path.join(SC, 'inventory_transactions.csv'), token);
const invId = inv?.data?.id;
console.log(`  📊 Route A - inventory_transactions.csv → ${invId?.slice(0,8)} (kind: ${inv?.data?.kind})`);

const log = await uploadFile(path.join(SC, 'logistics_performance.csv'), token);
const logId = log?.data?.id;
console.log(`  📊 Route A - logistics_performance.csv  → ${logId?.slice(0,8)} (kind: ${log?.data?.kind})`);

// Route B: 半结构化 JSON
const sup = await uploadFile(path.join(SC, 'supplier_orders.json'), token);
const supId = sup?.data?.id;
console.log(`  📋 Route B - supplier_orders.json       → ${supId?.slice(0,8)} (kind: ${sup?.data?.kind})`);

// Route C: 非结构化 Markdown
const md = await uploadFile(path.join(SC, 'supply_chain_strategy.md'), token);
const mdId = md?.data?.id;
console.log(`  📄 Route C - supply_chain_strategy.md   → ${mdId?.slice(0,8)} (kind: ${md?.data?.kind})`);

// 4. 创建 Pipeline（三条路径）
console.log('\n🔄 [4] 创建三条 Pipeline...');
const pipelines = [];
const pipelineDefs = [
  { name: '供应链-库存交易-Route-A', dsId: invId, route: 'A' },
  { name: '供应链-物流绩效-Route-A', dsId: logId, route: 'A' },
  { name: '供应链-供应商订单-Route-B', dsId: supId, route: 'B', spec: { json_flatten: { array_explode: true, array_fields: ['items'] } } },
  { name: '供应链-策略文档-Route-C', dsId: mdId, route: 'C' },
];
for (const def of pipelineDefs) {
  if (!def.dsId) { console.log(`  ⚠️  跳过 ${def.name}（dataset 上传失败）`); continue; }
  const r = await apiCall('POST', '/api/v2/pipelines', { name: def.name, source_dataset_id: def.dsId, route: def.route, spec: def.spec || {} }, token);
  const plId = r.body?.id || r.body?.data?.id;
  pipelines.push({ id: plId, name: def.name, route: def.route });
  console.log(`  ${def.route === 'A' ? '📊' : def.route === 'B' ? '📋' : '📄'} ${def.name} → ${plId?.slice(0,8)}`);
}

// 5. 同步运行所有 Pipeline
console.log('\n▶️  [5] 运行所有 Pipeline（同步）...');
const curatedIds = [];
for (const pl of pipelines) {
  if (!pl.id) continue;
  const r = await apiCall('POST', `/api/v2/pipelines/${pl.id}/run-sync`, null, token);
  const status = r.body?.status;
  const curatedId = r.body?.stats?.curated_dataset_id;
  if (curatedId) curatedIds.push({ id: curatedId, name: pl.name });
  console.log(`  ${status === 'success' ? '✅' : '❌'} ${pl.name} (Route ${pl.route}): ${status}, rows_out=${r.body?.stats?.rows_out ?? '?'}, curated=${curatedId?.slice(0,8) ?? '无'}`);
}

// 6. 创建供应链本体
console.log('\n🧩 [6] 创建供应链本体...');
const ontoCreate = await apiCall('POST', '/api/v1/ontologies', {
  name: '供应链知识图谱_全流程测试',
  domain: '供应链',
  description: '包含供应商、库存、物流、采购完整供应链知识本体'
}, token);
const ontoId = ontoCreate.body?.data?.id || ontoCreate.body?.id;
console.log(`  本体 ID: ${ontoId?.slice(0,8)}`);

// 7. 上传文件到本体（v1），准备 LLM 提取
console.log('\n📎 [7] 上传文档到本体（v1 LLM 提取）...');
// 需要先通过 v1 files API 上传
async function uploadV1File(ontologyId, filePath, token) {
  const fileName = path.basename(filePath);
  const fileBytes = fs.readFileSync(filePath);
  const boundary = '----fb' + Date.now();
  const CRLF = '\r\n';
  const head = Buffer.from(`--${boundary}${CRLF}Content-Disposition: form-data; name="file"; filename="${fileName}"${CRLF}Content-Type: text/markdown${CRLF}${CRLF}`, 'utf8');
  const tail = Buffer.from(`${CRLF}--${boundary}--${CRLF}`, 'utf8');
  const formData = Buffer.concat([head, fileBytes, tail]);
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname: 'localhost', port: 8000, path: `/api/v1/ontologies/${ontologyId}/files`, method: 'POST', headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': `multipart/form-data; boundary=${boundary}`, 'Content-Length': formData.length } }, res => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch { resolve(d); } });
    });
    req.on('error', reject); req.write(formData); req.end();
  });
}
const v1File = await uploadV1File(ontoId, path.join(SC, 'supply_chain_strategy.md'), token);
console.log(`  文件上传 v1: ${JSON.stringify(v1File).slice(0,80)}`);

// 8. 检查是否有可用模型，选择一个
console.log('\n🤖 [8] 查找可用 LLM 模型...');
const modelsRes = await apiCall('GET', '/api/v1/models', null, token);
const models = Array.isArray(modelsRes.body) ? modelsRes.body : (modelsRes.body?.data ?? []);
const promptsRes = await apiCall('GET', '/api/v1/prompts', null, token);
const prompts = Array.isArray(promptsRes.body) ? promptsRes.body : (promptsRes.body?.data ?? []);

console.log(`  可用模型: ${models.length}，可用提示词: ${prompts.length}`);
if (models.length > 0) {
  const model = models[0];
  const modelName = model.models?.[0] || '';
  const prompt = prompts.find(p => p.domain === '供应链') || prompts[0];

  if (model && modelName && prompt) {
    console.log(`  使用: ${model.name} / ${modelName} + ${prompt.name}`);

    // 启动 LLM 提取
    const extractRes = await apiCall('POST', `/api/v1/ontologies/${ontoId}/execute`, {
      prompt_id: prompt.id,
      model_id: model.id,
      model_name: modelName,
      constraints: []
    }, token);
    const taskId = extractRes.body?.task_id || extractRes.body?.data?.task_id;
    console.log(`  提取任务 ID: ${taskId?.slice(0,8)}`);

    if (taskId) {
      // 轮询状态（最多 60 秒）
      let done = false;
      for (let i = 0; i < 30 && !done; i++) {
        await wait(2000);
        const statusRes = await apiCall('GET', `/api/v1/ontologies/${ontoId}/execute/status?task_id=${taskId}`, null, token);
        const status = statusRes.body?.status || statusRes.body?.data?.status;
        const stage = statusRes.body?.progress?.stage || '';
        process.stdout.write(`\r  提取进度: ${status} ${stage} (${i*2}s)   `);
        if (status === 'completed' || status === 'failed') {
          done = true;
          console.log(`\n  提取${status === 'completed' ? '完成' : '失败'}`);
        }
      }
      if (!done) console.log('\n  提取超时（后台继续运行）');
    }
  } else {
    console.log('  ⚠️  无可用模型/提示词，跳过 LLM 提取（图谱可能为空）');
  }
} else {
  console.log('  ⚠️  无可用模型，跳过 LLM 提取');
}

// 9. Apply Pipeline Mapping（结构化数据 → 本体实体）
console.log('\n📝 [9] Pipeline Mapping → 写入本体实体...');
for (const curated of curatedIds.slice(0, 2)) {  // 只取前两个结构化 curated
  const versionRes = await apiCall('GET', `/api/v2/datasets/${curated.id}/versions`, null, token);
  const versions = Array.isArray(versionRes.body) ? versionRes.body : [];
  let rows = [];
  if (versions.length > 0) {
    const previewRes = await apiCall('GET', `/api/v2/datasets/${curated.id}/versions/1/preview?limit=100`, null, token);
    rows = Array.isArray(previewRes.body) ? previewRes.body : [];
  }
  if (rows.length === 0) continue;
  const columns = Object.keys(rows[0]);

  // Suggest mapping
  const suggestRes = await apiCall('POST', `/api/v2/ontologies/${ontoId}/mappings/suggest`, {
    dataset_name: curated.name,
    columns,
    sample_rows: rows.slice(0, 3),
    ontology_domain: '供应链'
  }, token);
  const sug = suggestRes.body ?? {};

  // Create mapping
  const fieldMap = {};
  (sug.field_mappings ?? []).forEach(fm => { fieldMap[fm.column_name] = fm.property_name; });
  if (sug.primary_key_column) fieldMap['__primary_key__'] = sug.primary_key_column;

  const mapCreate = await apiCall('POST', `/api/v2/ontologies/${ontoId}/mappings`, {
    curated_dataset_id: curated.id,
    entity_class: sug.entity_class || 'SupplyChainEntity',
    field_mapping: fieldMap,
    confidence: 0.85
  }, token);
  const mappingId = mapCreate.body?.mapping_id;

  if (mappingId) {
    const applyRes = await apiCall('POST', `/api/v2/ontologies/${ontoId}/mappings/${mappingId}/apply`, rows, token);
    console.log(`  ${curated.name}: ${applyRes.body?.v1_entities_written ?? 0} 个实体写入`);
  }
}

// 9b. 创建跨类型实体和关系（让图谱形成网状结构）
console.log('\n🔗 [9b] 创建承运商/供应商实体 + 建立关系网络...');
const allEntsRes = await apiCall('GET', `/api/v1/ontologies/${ontoId}/entities`, null, token);
const allEnts = Array.isArray(allEntsRes.body) ? allEntsRes.body : (allEntsRes.body?.data ?? []);

// 提取唯一承运商/供应商
const carrierMap = {}, supplierMap = {};
for (const e of allEnts) {
  const p = e.properties || {};
  if (p['承运商'] && !carrierMap[p['承运商']]) carrierMap[p['承运商']] = null;
  if (p['供应商'] && !supplierMap[p['供应商']]) supplierMap[p['供应商']] = null;
}

// 创建承运商实体
for (const name of Object.keys(carrierMap)) {
  const r = await apiCall('POST', `/api/v1/ontologies/${ontoId}/entities`, {
    name_cn: name, name_en: name, type: 'Carrier',
    description: 'Supply chain carrier ' + name, confidence: 0.95, properties: { carrier_name: name }
  }, token);
  carrierMap[name] = r.body?.data?.id || r.body?.id;
}
// 创建供应商实体（最多12个）
let supIdx = 0;
for (const name of Object.keys(supplierMap)) {
  if (supIdx++ >= 12) break;
  const r = await apiCall('POST', `/api/v1/ontologies/${ontoId}/entities`, {
    name_cn: name, name_en: name, type: 'Supplier',
    description: 'Supply chain supplier ' + name, confidence: 0.95, properties: { supplier_code: name }
  }, token);
  supplierMap[name] = r.body?.data?.id || r.body?.id;
}
console.log(`  创建了 ${Object.keys(carrierMap).length} 个承运商实体, ${Object.keys(supplierMap).length} 个供应商实体`);

// 创建关系（每5条物流记录取1条，避免图太密集）
const logEnts = allEnts.filter(e => e.type && e.type !== 'Carrier' && e.type !== 'Supplier');
let relCreated = 0;
for (let i = 0; i < logEnts.length; i += 5) {
  const e = logEnts[i];
  const p = e.properties || {};
  const carrierId = carrierMap[p['承运商']];
  const supplierId = supplierMap[p['供应商']];
  if (carrierId) {
    await apiCall('POST', `/api/v1/ontologies/${ontoId}/graph/relations`, {
      source_entity: e.id, target_entity: carrierId, type: 'SHIPS_VIA', confidence: 0.9
    }, token);
    relCreated++;
  }
  if (supplierId) {
    await apiCall('POST', `/api/v1/ontologies/${ontoId}/graph/relations`, {
      source_entity: e.id, target_entity: supplierId, type: 'FROM_SUPPLIER', confidence: 0.9
    }, token);
    relCreated++;
  }
}
console.log(`  创建了 ${relCreated} 条关系`);

// 查本体最终实体/关系数量
const entitiesRes = await apiCall('GET', `/api/v1/ontologies/${ontoId}/entities`, null, token);
const entities = entitiesRes.body?.data ?? entitiesRes.body ?? [];
const graphRes = await apiCall('GET', `/api/v1/ontologies/${ontoId}/graph`, null, token);
const graphData = graphRes.body?.data ?? graphRes.body ?? {};
const nodeCount = graphData.nodes?.length ?? 0;
const edgeCount = graphData.edges?.length ?? 0;
console.log(`\n📊 本体统计: ${Array.isArray(entities) ? entities.length : '?'} 个实体, ${nodeCount} 节点, ${edgeCount} 条边`);

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

// A. Connections
await page.goto(`${BASE}/pipelines/connections`); await page.waitForLoadState('networkidle'); await wait(1500);
await shot(page, 'A_connections');

// B. Datasets（含三种类型）
await page.goto(`${BASE}/pipelines/datasets`); await page.waitForLoadState('networkidle'); await wait(1500);
await shot(page, 'B_datasets_3types');

// C. Transforms（三条路径）
await page.goto(`${BASE}/pipelines/transforms`); await page.waitForLoadState('networkidle'); await wait(1500);
await shot(page, 'C_transforms_3routes');

// D. Curated（审核列表）
await page.goto(`${BASE}/pipelines/curated`); await page.waitForLoadState('networkidle'); await wait(1500);
await shot(page, 'D_curated_list');

// 展开第一个 curated 查质量报告
const expandBtn = page.locator('button:has-text("质量报告")').first();
if (await expandBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
  await expandBtn.click(); await wait(1500);
  await shot(page, 'D_curated_quality_report');
}

// 点击 Approve 审批第一个
const approveBtn = page.locator('button:has-text("批准")').first();
if (await approveBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
  await approveBtn.click(); await wait(800);
  await shot(page, 'D_curated_approved');
}

// E. 本体向导（新建路径选择）
await page.goto(`${BASE}/ontologies/new`); await page.waitForLoadState('networkidle'); await wait(1500);
await shot(page, 'E_ontology_wizard');

// F. 本体详情
if (ontoId) {
  await page.goto(`${BASE}/ontologies/${ontoId}`); await page.waitForLoadState('networkidle'); await wait(2000);
  await shot(page, 'F_ontology_info');

  // 实体 Tab
  const entBtn = page.locator('button:has-text("实体")').first();
  if (await entBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await entBtn.click(); await wait(1500);
    await shot(page, 'F_entities_with_data');
    // 测试搜索
    const searchBox = page.locator('input[placeholder*="搜索"]').first();
    if (await searchBox.isVisible({ timeout: 1000 }).catch(() => false)) {
      await searchBox.fill('供应商');
      await wait(500);
      await shot(page, 'F_entities_search');
      await searchBox.fill('');
    }
  }

  // 知识图谱 Tab
  const graphBtn = page.locator('button:has-text("知识图谱")').first();
  if (await graphBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await graphBtn.click(); await wait(4000);  // 图渲染等稍长
    await shot(page, 'F_graph_network');
    // 测试搜索高亮
    const graphSearch = page.locator('input[placeholder*="搜索节点"]');
    if (await graphSearch.isVisible({ timeout: 1000 }).catch(() => false)) {
      await graphSearch.fill('供应商');
      await wait(600);
      await shot(page, 'F_graph_search_highlight');
    }
  }

  // 逻辑规则 Tab
  const logicBtn = page.locator('button:has-text("逻辑规则")').first();
  if (await logicBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await logicBtn.click(); await wait(1200);
    await shot(page, 'F_logic_rules');
  }

  // 文件上传 Tab
  const filesBtn = page.locator('button:has-text("文件上传")').first();
  if (await filesBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await filesBtn.click(); await wait(1000);
    await shot(page, 'F_files_tab');
  }
}

// G. 本体列表（含实体/关系数）
await page.goto(`${BASE}/ontologies`); await page.waitForLoadState('networkidle'); await wait(1500);
await shot(page, 'G_ontology_list_counts');

await browser.close();
console.log(`\n═══════════════════════════════════════════════════════════`);
console.log(`  完成！共 ${step} 张截图 → ${SS}`);
console.log(`═══════════════════════════════════════════════════════════`);
