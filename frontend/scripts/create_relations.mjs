import http from 'http';
import crypto from 'crypto';

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
console.log('Ontology:', oid?.slice(0,8));

const entsRes = await api('GET', '/api/v1/ontologies/' + oid + '/entities', null, token);
const entities = entsRes.data ?? [];
console.log('Entities:', entities.length);

// 提取唯一承运商和供应商
const carriers = new Map();   // name -> entity
const suppliers = new Map();  // name -> entity

for (const e of entities) {
  const props = e.properties || {};
  const carrier = props['承运商'] || props['carrier'];
  const supplier = props['供应商'] || props['supplier'];
  if (carrier && !carriers.has(carrier)) carriers.set(carrier, null);
  if (supplier && !suppliers.has(supplier)) suppliers.set(supplier, null);
}
console.log('Unique carriers:', carriers.size, 'suppliers:', suppliers.size);

// 创建承运商实体
for (const [name] of carriers) {
  const r = await api('POST', '/api/v1/ontologies/' + oid + '/entities', {
    name_cn: name, name_en: name, type: '承运商', description: 供应链承运商: , confidence: 0.95, properties: { carrier_name: name }
  }, token);
  const id = r.data?.id || r.id;
  carriers.set(name, id);
}
console.log('Created carrier entities:', carriers.size);

// 创建供应商实体（只取前15个避免太多）
let supCount = 0;
for (const [name] of suppliers) {
  if (supCount >= 15) break;
  const r = await api('POST', '/api/v1/ontologies/' + oid + '/entities', {
    name_cn: name, name_en: name, type: '供应商', description: 供应链供应商: , confidence: 0.95, properties: { supplier_code: name }
  }, token);
  const id = r.data?.id || r.id;
  suppliers.set(name, id);
  supCount++;
}
console.log('Created supplier entities:', supCount);

// 创建关系 Relation API endpoint
const graphRes = await api('GET', '/api/v1/ontologies/' + oid + '/graph', null, token);
const graphData = graphRes.data ?? {};
console.log('Graph relations endpoint available');

// 查看 relations 端点
const relRes = await api('GET', '/api/v1/ontologies/' + oid + '/graph/relations', null, token);
console.log('Relations:', JSON.stringify(relRes).slice(0,200));
