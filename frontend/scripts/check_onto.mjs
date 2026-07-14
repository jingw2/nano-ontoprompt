import http from 'http';

async function apiCall(method, endpoint, body, token) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const headers = { 'Authorization': 'Bearer ' + token };
    if (data) { headers['Content-Type'] = 'application/json'; headers['Content-Length'] = Buffer.byteLength(data); }
    const req = http.request({ hostname: 'localhost', port: 8000, path: endpoint, method, headers }, res => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch { resolve({ body: d }); } });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

const login = await apiCall('POST', '/api/v1/auth/login', { username: 'admin', password: 'admin123' });
const token = login.data?.access_token || login.body?.data?.access_token;

// 找最新本体
const ontos = await apiCall('GET', '/api/v1/ontologies?page_size=5', null, token);
const onto = ontos.data?.items?.[0] || ontos.body?.data?.items?.[0];
console.log('Onto:', onto?.id?.slice(0,8), onto?.name);

if (!onto) process.exit(1);
const oid = onto.id;

// 获取实体列表
const ents = await apiCall('GET', '/api/v1/ontologies/' + oid + '/entities', null, token);
const entities = ents.data ?? ents.body?.data ?? [];
console.log('Entities:', entities.length);

// 获取图谱关系现状
const graph = await apiCall('GET', '/api/v1/ontologies/' + oid + '/graph', null, token);
const existing = graph.data ?? graph.body?.data ?? {};
console.log('Current nodes:', existing.nodes?.length ?? 0, 'edges:', existing.edges?.length ?? 0);
