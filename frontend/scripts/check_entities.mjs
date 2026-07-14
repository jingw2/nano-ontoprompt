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

const ents = await api('GET', '/api/v1/ontologies/' + oid + '/entities', null, token);
const entities = ents.data ?? [];
console.log('Total entities:', entities.length);

// 看前几个
entities.slice(0, 5).forEach(e => {
  console.log('  -', e.id?.slice(0,8), e.name_cn?.slice(0,20), '|type:', e.type, '|props:', JSON.stringify(e.properties).slice(0,60));
});
console.log('Types:', [...new Set(entities.map(e => e.type))].join(', '));
