import http from 'http';

async function api(method, endpoint, body, token) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const headers = { 'Authorization': 'Bearer ' + token };
    if (data) { headers['Content-Type'] = 'application/json'; headers['Content-Length'] = Buffer.byteLength(data); }
    const req = http.request({ hostname: 'localhost', port: 8000, path: endpoint, method, headers }, res => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch { resolve(d); } });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

const login = await api('POST', '/api/v1/auth/login', { username: 'admin', password: 'admin123' });
const token = login.data?.access_token;
const ontos = await api('GET', '/api/v1/ontologies?page_size=1', null, token);
const oid = ontos.data?.items?.[0]?.id;

// 获取所有实体
const entsRes = await api('GET', '/api/v1/ontologies/' + oid + '/entities', null, token);
const allEntities = Array.isArray(entsRes.data) ? entsRes.data : [];

// 分类
const logisticsEntities = allEntities.filter(e => e.type === 'SupplyChainEntity' || e.type?.includes('Route'));
const carrierEntities = allEntities.filter(e => e.type === 'Carrier');
const supplierEntities = allEntities.filter(e => e.type === 'Supplier');
console.log('Logistics:', logisticsEntities.length, 'Carriers:', carrierEntities.length, 'Suppliers:', supplierEntities.length);

// 建索引
const carrierMap = {};
carrierEntities.forEach(e => { carrierMap[e.name_cn] = e.id; });
const supplierMap = {};
supplierEntities.forEach(e => { supplierMap[e.name_cn] = e.id; });

// 对每条物流记录，采样创建关系（每20条取1条，避免太多）
let relCreated = 0;
const logSample = logisticsEntities.filter((_, i) => i % 5 === 0);  // 每5条取1条 = 20条记录

for (const logE of logSample) {
  const props = logE.properties || {};
  const carrierName = props['承运商'];
  const supplierName = props['供应商'];
  const region = props['目的区域'];

  if (carrierName && carrierMap[carrierName]) {
    await api('POST', '/api/v1/ontologies/' + oid + '/graph/relations', {
      source_entity: logE.id,
      target_entity: carrierMap[carrierName],
      type: 'SHIPS_VIA',
      description: logE.name_cn + ' shipped via ' + carrierName,
      confidence: 0.9
    }, token);
    relCreated++;
  }

  if (supplierName && supplierMap[supplierName]) {
    await api('POST', '/api/v1/ontologies/' + oid + '/graph/relations', {
      source_entity: logE.id,
      target_entity: supplierMap[supplierName],
      type: 'FROM_SUPPLIER',
      description: logE.name_cn + ' from ' + supplierName,
      confidence: 0.9
    }, token);
    relCreated++;
  }
}
console.log('Relations created:', relCreated);

// 最终图谱统计
const graph = await api('GET', '/api/v1/ontologies/' + oid + '/graph', null, token);
const g = graph.data ?? {};
console.log('Graph: nodes=' + (g.nodes?.length ?? 0) + ' edges=' + (g.edges?.length ?? 0));
