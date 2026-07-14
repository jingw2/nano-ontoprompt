const API = 'http://localhost:8002/api/v1';
const OID = 'ef1a1be8-d336-4c82-af43-eddd9fe75019';

const loginRes = await fetch(`${API}/auth/login`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: 'admin', password: 'admin123' }),
});
const token = (await loginRes.json()).data.access_token;

const g = await fetch(`${API}/ontologies/${OID}/graph`, {
  headers: { Authorization: `Bearer ${token}` },
}).then(r => r.json()).then(j => j.data);

const nodes = Object.fromEntries(g.nodes.map(n => [n.data.id, n.data.label]));
const edges = g.edges;

// 关系类型分布
const types = {};
for (const e of edges) {
  const t = e.data.label || '(空)';
  types[t] = (types[t] ?? 0) + 1;
}
console.log(`\n边总数: ${edges.length}`);
console.log('\n关系类型分布:');
for (const [t, n] of Object.entries(types).sort((a,b) => b[1]-a[1])) {
  console.log(`  "${t}": ${n} 条`);
}

console.log('\n所有边（源实体 → 关系 → 目标实体）:');
for (const e of edges) {
  const { source, target, label, confidence } = e.data;
  console.log(`  ${nodes[source] ?? source} --[${label}]--> ${nodes[target] ?? target}  (conf=${confidence})`);
}
