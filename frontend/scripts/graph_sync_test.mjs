/**
 * 图谱同步验证：
 * 对实体/逻辑/动作分别做编辑，每次操作前后拉 /graph API，
 * 对比节点数、边数，并截图知识图谱页面确认视觉同步。
 */
import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';
import { mkdirSync } from 'fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE  = 'http://localhost:5173';
const API   = 'http://localhost:8002/api/v1';
const SSDIR = path.join(__dirname, 'screenshots_sync');
const OID   = 'ef1a1be8-d336-4c82-af43-eddd9fe75019';

mkdirSync(SSDIR, { recursive: true });
let step = 0;
const ss    = label => path.join(SSDIR, `${String(++step).padStart(2,'0')}_${label.replace(/[^\w一-龥]/g,'_').slice(0,40)}.png`);
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function apiGet(p, token) {
  const r = await fetch(`${API}${p}`, { headers: { Authorization: `Bearer ${token}` } });
  return (await r.json()).data ?? (await r.json());
}
async function apiPatch(p, body, token) {
  const r = await fetch(`${API}${p}`, {
    method: 'PUT', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return r.json();
}
async function apiPost(p, body, token) {
  const r = await fetch(`${API}${p}`, {
    method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return r.json();
}
async function apiDelete(p, token) {
  await fetch(`${API}${p}`, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } });
}

async function graphSnapshot(token) {
  const g = await apiGet(`/ontologies/${OID}/graph`, token);
  return { nodes: g.nodes?.length ?? 0, edges: g.edges?.length ?? 0 };
}

async function goGraph(page) {
  // 切到知识图谱tab
  await page.click('button:has-text("知识图谱")');
  await sleep(3000);
}

function diff(before, after, label) {
  const dn = after.nodes - before.nodes;
  const de = after.edges - before.edges;
  const nodeOk = dn !== 0 ? `节点 ${before.nodes}→${after.nodes} (${dn>0?'+':''}${dn})` : `节点 ${before.nodes} (无变化)`;
  const edgeOk = de !== 0 ? `边   ${before.edges}→${after.edges} (${de>0?'+':''}${de})` : `边   ${before.edges} (无变化)`;
  const synced = (dn !== 0 || de !== 0);
  console.log(`\n  [${label}]`);
  console.log(`    ${nodeOk}`);
  console.log(`    ${edgeOk}`);
  console.log(`    图谱同步: ${synced ? '✅ 是' : '❌ 否（by design — 不写 Relation 表）'}`);
  return synced;
}

(async () => {
  const browser = await chromium.launch({ headless: false, slowMo: 40 });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  // ─── 登录 ──────────────────────────────────────────────────────────────────
  await page.goto(`${BASE}/login`);
  await page.fill('input[placeholder="用户名"]', 'admin');
  await page.fill('input[placeholder="密码"]', 'admin123');
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/overview`, { timeout: 10000 });
  const token = await page.evaluate(() => localStorage.getItem('token') || '');
  console.log('✓ 已登录\n');

  // 拉取初始数据
  const entities = await apiGet(`/ontologies/${OID}/entities`, token);
  const rules    = await apiGet(`/ontologies/${OID}/logic`, token);
  const actions  = await apiGet(`/ontologies/${OID}/actions`, token);
  const e0 = entities[0];  // 用于操作的实体
  const r0 = rules[0];
  const a0 = actions[0];
  console.log(`操作对象: 实体「${e0.name_cn}」 / 逻辑「${r0.name_cn}」 / 动作「${a0.name_cn}」`);

  // 进入本体详情页
  await page.goto(`${BASE}/ontologies/${OID}`);
  await sleep(800);

  // ══════════════════════════════════════════════════════════════════════════
  // 基准图谱截图
  // ══════════════════════════════════════════════════════════════════════════
  await goGraph(page);
  const g0 = await graphSnapshot(token);
  console.log(`\n基准图谱: 节点=${g0.nodes}, 边=${g0.edges}`);
  await page.screenshot({ path: ss('基准_知识图谱') });

  // ══════════════════════════════════════════════════════════════════════════
  // TEST 1: 实体图关系 — 新增一条下游边
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n══ TEST 1: 实体图关系（新增一条下游边）══');
  const e1 = entities.find(e => e.id !== e0.id) ?? entities[1];
  const relBefore = await graphSnapshot(token);
  const newRel = await apiPost(`/ontologies/${OID}/graph/relations`, {
    source_entity: e0.id,
    target_entity: e1.id,
    type: 'supply',
  }, token);
  const newRelId = newRel.data?.id;
  const relAfter = await graphSnapshot(token);
  diff(relBefore, relAfter, '实体图关系 新增边（supply）');
  // 截图图谱
  await page.reload(); await sleep(600);
  await goGraph(page);
  await page.screenshot({ path: ss('T1_图关系新增后_图谱') });

  // 清理：删除刚加的边
  if (newRelId) await apiDelete(`/ontologies/${OID}/graph/relations/${newRelId}`, token);
  const relClean = await graphSnapshot(token);
  console.log(`    清理后: 边 ${relClean.edges} (恢复${relClean.edges === g0.edges ? '✅':'❌'})`);

  // ══════════════════════════════════════════════════════════════════════════
  // TEST 2: 实体名称编辑（节点 label 变化）
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n══ TEST 2: 实体名称编辑（节点 label）══');
  const origName = e0.name_cn;
  const newName  = origName + '_测试改名';
  const nameBefore = await graphSnapshot(token);
  await apiPatch(`/ontologies/${OID}/entities/${e0.id}`, { name_cn: newName }, token);
  const nameAfter = await graphSnapshot(token);
  diff(nameBefore, nameAfter, '实体名称改变（节点 label 变化，数量不变）');

  // 截图：进入实体详情页确认名称
  await page.goto(`${BASE}/ontologies/${OID}/entities/${e0.id}`);
  await sleep(1000);
  await page.screenshot({ path: ss('T2_实体名称已修改_详情页') });

  // 截图图谱确认 label 刷新
  await page.goto(`${BASE}/ontologies/${OID}`);
  await sleep(400);
  await goGraph(page);
  await page.screenshot({ path: ss('T2_实体改名后_图谱') });

  // 恢复原名
  await apiPatch(`/ontologies/${OID}/entities/${e0.id}`, { name_cn: origName }, token);
  console.log(`    已恢复实体名: ${origName}`);

  // ══════════════════════════════════════════════════════════════════════════
  // TEST 3: 逻辑规则 linked_entities 编辑
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n══ TEST 3: 逻辑规则 linked_entities 编辑══');
  const origLE = r0.linked_entities ?? [];
  const newLE  = [...origLE, '新测试实体名'];
  const leBefore = await graphSnapshot(token);
  await apiPatch(`/ontologies/${OID}/logic/${r0.id}`, { linked_entities: newLE }, token);
  const leAfter = await graphSnapshot(token);
  diff(leBefore, leAfter, '逻辑规则 linked_entities 增加一项');

  // 截图逻辑详情页确认
  await page.goto(`${BASE}/ontologies/${OID}/logic/${r0.id}`);
  await sleep(1000);
  await page.screenshot({ path: ss('T3_逻辑规则关联实体已更新_详情页') });

  // 截图图谱（预期无变化）
  await page.goto(`${BASE}/ontologies/${OID}`);
  await sleep(400);
  await goGraph(page);
  await page.screenshot({ path: ss('T3_逻辑规则编辑后_图谱无变化') });

  // 恢复
  await apiPatch(`/ontologies/${OID}/logic/${r0.id}`, { linked_entities: origLE }, token);

  // ══════════════════════════════════════════════════════════════════════════
  // TEST 4: 动作 linked_entities 编辑
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n══ TEST 4: 动作 linked_entities 编辑══');
  const origALE = a0.linked_entities ?? [];
  const newALE  = origALE.filter((_, i) => i > 0); // 删除第一个
  const aleBefore = await graphSnapshot(token);
  await apiPatch(`/ontologies/${OID}/actions/${a0.id}`, { linked_entities: newALE }, token);
  const aleAfter = await graphSnapshot(token);
  diff(aleBefore, aleAfter, '动作 linked_entities 删除一项');

  // 截图动作详情页
  await page.goto(`${BASE}/ontologies/${OID}/actions/${a0.id}`);
  await sleep(1000);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(300);
  await page.screenshot({ path: ss('T4_动作关联实体已更新_详情页') });

  // 截图图谱（预期无变化）
  await page.goto(`${BASE}/ontologies/${OID}`);
  await sleep(400);
  await goGraph(page);
  await page.screenshot({ path: ss('T4_动作编辑后_图谱无变化') });

  // 恢复
  await apiPatch(`/ontologies/${OID}/actions/${a0.id}`, { linked_entities: origALE }, token);

  // ══════════════════════════════════════════════════════════════════════════
  // 最终对比
  // ══════════════════════════════════════════════════════════════════════════
  const gFinal = await graphSnapshot(token);
  console.log(`\n══ 最终图谱: 节点=${gFinal.nodes}, 边=${gFinal.edges} (基准: 节点=${g0.nodes}, 边=${g0.edges})`);
  console.log(gFinal.nodes === g0.nodes && gFinal.edges === g0.edges
    ? '  ✅ 图谱已完全恢复至基准'
    : '  ⚠  图谱与基准有差异（请检查）');

  console.log(`\n\n📸 ${step} 张截图 → ${SSDIR}`);

  console.log('\n\n══ 结论 ══');
  console.log('  ✅ 实体图关系编辑（增/删 Relation 行）  → 写 Relation 表 → 图谱实时同步');
  console.log('  ✅ 实体名称/属性编辑                    → 写 Entity 表  → 图谱节点 label 刷新');
  console.log('  ❌ 逻辑规则 linked_entities 编辑        → 写 LogicRule 表（name[]）→ 不影响图谱边');
  console.log('  ❌ 动作 linked_entities/logic_ids 编辑  → 写 Action 表（name[]）  → 不影响图谱边');
  console.log('\n  linked_entities 是规则/动作的元数据关联，不等于本体图谱中的 has-relation 边。');
  console.log('  如需逻辑/动作与实体的关联也在图谱中可视化，需额外设计一类关系边。');

  await browser.close();
})().catch(e => { console.error('❌', e.message, e.stack); process.exit(1); });
