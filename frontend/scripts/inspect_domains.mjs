import { createInterface } from 'readline';

const API = 'http://localhost:8002/api/v1';

async function login() {
  const r = await fetch(`${API}/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'admin123' }),
  });
  return (await r.json()).data.access_token;
}

async function apiGet(path, token) {
  const r = await fetch(`${API}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  return (await r.json()).data ?? [];
}

const token = await login();

const domains = [
  { name: '财务', oid: 'ee16adcf-ca0d-44b8-8698-67c7e8242dec' },
  { name: '营销', oid: '5234b7f3-2e3d-4536-b2ce-f48508b596a4' },
  { name: 'HR',   oid: '75d5e5f7-c101-49a6-a2ed-91642d6a0dcc' },
];

for (const { name, oid } of domains) {
  const g = await apiGet(`/ontologies/${oid}/graph`, token);
  const nodes = Object.fromEntries((g.nodes || []).map(n => [n.data.id, n.data.label]));
  const edges = g.edges || [];
  
  const isolated = (g.nodes || []).filter(n => 
    !edges.some(e => e.data.source === n.data.id || e.data.target === n.data.id)
  );

  console.log(`\n=== ${name} — ${Object.keys(nodes).length} 节点 / ${edges.length} 边 / ${isolated.length} 孤立 ===`);
  console.log('  实际边:');
  for (const e of edges) {
    const src = nodes[e.data.source] || '?';
    const tgt = nodes[e.data.target] || '?';
    console.log(`    ${src} --[${e.data.label}]--> ${tgt}`);
  }
  
  console.log('  孤立节点 (前10):');
  for (const n of isolated.slice(0, 10)) {
    console.log(`    · ${n.data.label} (${n.data.type})`);
  }
  if (isolated.length > 10) console.log(`    ... 另 ${isolated.length - 10} 个`);
}
