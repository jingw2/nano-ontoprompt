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
console.log('Onto:', oid?.slice(0,8));

const entsRes = await api('GET', '/api/v1/ontologies/' + oid + '/entities', null, token);
const entities = Array.isArray(entsRes.data) ? entsRes.data : [];
console.log('Entities:', entities.length);

// 提取唯一承运商和供应商
const carriers = new Map();
const suppliers = new Map();
for (const e of entities) {
  const props = e.properties || {};
  const carrier = props['承运商'];
  const supplier = props['供应商'];
  if (carrier && !carriers.has(carrier)) carriers.set(carrier, null);
  if (supplier && !suppliers.has(supplier)) suppliers.set(supplier, null);
}
console.log('Unique carriers:', carriers.size, 'unique suppliers:', suppliers.size);

// 创建承运商实体
for (const [name] of carriers) {
  const r = await api('POST', '/api/v1/ontologies/' + oid + '/entities', {
    name_cn: name, name_en: name,
    type: 'Carrier',
    description: 'Supply chain carrier ' + name,
    confidence: 0.95,
    properties: { carrier_name: name }
  }, token);
  const id = r.data?.id;
  carriers.set(name, id);
}
console.log('Created carrier entities:', carriers.size);

// 创建供应商实体（最多12个）
let supCount = 0;
for (const [name] of suppliers) {
  if (supCount >= 12) break;
  const r = await api('POST', '/api/v1/ontologies/' + oid + '/entities', {
    name_cn: name, name_en: name,
    type: 'Supplier',
    description: 'Supply chain supplier ' + name,
    confidence: 0.95,
    properties: { supplier_code: name }
  }, token);
  const id = r.data?.id;
  suppliers.set(name, id);
  supCount++;
}
console.log('Created supplier entities:', supCount);

// 创建关系：物流记录 --[承运商]--> 承运商实体, 物流记录 --[供应商]--> 供应商实体
// 检查 v1 graph relations API
const testR = await api('GET', '/api/v1/ontologies/' + oid + '/graph/relations', null, token);
console.log('Relations API sample:', JSON.stringify(testR).slice(0,200));
