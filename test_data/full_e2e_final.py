#!/usr/bin/env python3
"""全流程 E2E：供应链8文件 → 1个Connector(含全部文件) → Pipeline → Ontology"""
import requests, time, json
from pathlib import Path

BASE = 'http://localhost:8000'
DATA = Path(__file__).parent / '供应链'
s = requests.Session()

def log(msg): print(f'  {msg}')

# Login
r = s.post(f'{BASE}/api/v1/auth/login', json={'username':'admin','password':'admin123'})
s.headers.update({'Authorization': f'Bearer {r.json()["data"]["access_token"]}'})
ts = int(time.time())

print('='*60)
print('  E2E 全流程：供应链8文件 → 1个Pipeline → Ontology')
print('='*60)

# ═══ 1. Upload all 8 files ═══
print('\n── 1. 上传全部8文件 ──')
fm = {}
for f in sorted(DATA.glob('*')):
    with open(f, 'rb') as fh:
        r = s.post(f'{BASE}/api/v2/datasets/upload', files={'file':(f.name, fh, 'application/octet-stream')})
    if r.ok:
        did = r.json().get('data',{}).get('id') if isinstance(r.json(),dict) else None
        fm[f.name] = {'did': did, 'size': f.stat().st_size}
        log(f'✅ {f.name:35s} → {did[:8]}')
    time.sleep(0.1)

# ═══ 2. Create MASTER pipeline (1 Connector with ALL files) ═══
print('\n── 2. 主 Pipeline (1 Connector 含全部8文件) ──')
master = s.post(f'{BASE}/api/v2/pipelines', json={
    'name': f'供应链全格式-主Pipeline-{ts}', 'domain': '供应链',
    'definition':{
        'nodes':[
            {'id':'conn','type':'connector','label':'数据源(全部8文件)','position':{'x':50,'y':240},
             'config':{'source_type':'file','files':[{'name':n,'size':i['size']} for n,i in fm.items()]}},
            {'id':'stor','type':'storage','label':'存储器','position':{'x':280,'y':240}},
            {'id':'xfm','type':'transform','label':'转换器(自动路由)','position':{'x':510,'y':240},
             'config':{'path':'auto'}},
            {'id':'out','type':'output','label':'输出(全部数据集)','position':{'x':740,'y':240},
             'config':{'dataset_type':'curated_dataset','primary_key':['id']}},
        ],
        'edges':[{'id':'e1','source':'conn','target':'stor'},{'id':'e2','source':'stor','target':'xfm'},{'id':'e3','source':'xfm','target':'out'}],
    },
})
master_pid = r.json()['id'] if r.ok else None
log(f'主Pipeline: {master_pid[:8] if master_pid else "FAIL"}')

# ═══ 3. Run individual pipelines per file (当前架构限制) ═══
print('\n── 3. 运行每条 Pipeline ──')
routes = {'csv':'structured','xlsx':'structured','json':'semi_structured',
          'md':'unstructured','docx':'unstructured','pptx':'unstructured','pdf':'unstructured'}
curated = {}
for fn, info in fm.items():
    ext = fn.rsplit('.',1)[-1].lower()
    rc = routes.get(ext, 'structured')
    r = s.post(f'{BASE}/api/v2/pipelines', json={
        'name': f'{fn.split(".")[0]}-{ts}', 'domain': '供应链',
        'definition':{
            'nodes':[
                {'id':'c','type':'connector','label':'数据源','position':{'x':50,'y':200},'config':{'source_type':'file','files':[{'name':fn}]}},
                {'id':'s','type':'storage','label':'存储器','position':{'x':250,'y':200}},
                {'id':'x','type':'transform','label':'转换器','position':{'x':450,'y':200},'config':{'path':rc}},
                {'id':'o','type':'output','label':'输出','position':{'x':680,'y':200},'config':{'dataset_type':'curated_dataset','primary_key':['id']}},
            ],
            'edges':[{'id':'e1','source':'c','target':'s'},{'id':'e2','source':'s','target':'x'},{'id':'e3','source':'x','target':'o'}],
        },
    })
    if r.ok:
        pid = r.json()['id']
        r2 = s.post(f'{BASE}/api/v2/pipelines/{pid}/run-sync')
        if r2.ok:
            st = r2.json().get('stats',{})
            cid = st.get('curated_dataset_id')
            if cid: curated[fn] = cid
            ok = st.get('rows_in',0) > 0
            log(f'{"✅" if ok else "❌"} {fn:35s} rows={st.get("rows_in",0)}')
    time.sleep(0.1)

# ═══ 4. Approve ═══
print('\n── 4. 审批 Curated Datasets ──')
for cid in curated.values():
    s.post(f'{BASE}/api/v2/curated/{cid}/review?action=approve')
log(f'{len(curated)} 个已审批')

# ═══ 5. Ontology Mapping ═══
print('\n── 5. Ontology Pipeline Mapping ──')
emap = {
    'inventory_transactions.csv':'InventoryTransaction',
    'logistics_performance.csv':'LogisticsRecord',
    'supplier_database.xlsx':'Supplier',
    'supplier_orders.json':'PurchaseOrder',
    'supply_chain_strategy.md':'Strategy',
    'procurement_policy.docx':'ProcurementPolicy',
    'supply_chain_review.pptx':'Review',
    'warehouse_management.pdf':'Warehouse',
}
r = s.post(f'{BASE}/api/v1/ontologies', json={
    'name':f'供应链知识图谱-E2E-{ts}','domain':'供应链','build_mode':'pipeline_mapping'})
oid = r.json().get('data',{}).get('id') if isinstance(r.json(),dict) else None
log(f'Ontology: {oid[:8] if oid else "FAIL"}')

for fn, cid in curated.items():
    en = emap.get(fn, f'E_{fn[:10]}')
    r = s.post(f'{BASE}/api/v2/ontologies/{oid}/mappings', json={
        'curated_dataset_id':cid,'entity_class':en,'field_mapping':{'__primary_key__':'id'}})
    mid = r.json().get('mapping_id','') if r.ok else ''
    if mid:
        r = s.post(f'{BASE}/api/v2/ontologies/{oid}/mappings/{mid}/apply-from-dataset')
        log(f'{en:25s} entities={r.json().get("v1_entities_written",0)}')

# Build-all
r = s.post(f'{BASE}/api/v2/ontologies/{oid}/mappings/build-all')
res = r.json()
log(f'build-all: entities={res.get("total_entities")} relations={res.get("total_relations")} chroma={res.get("chroma_entities_written")}')

# Link Mappings (跨表关系)
if 'logistics_performance.csv' in curated and 'supplier_database.xlsx' in curated:
    s.post(f'{BASE}/api/v2/ontologies/{oid}/link-mappings', json={
        'src_dataset_id':curated['logistics_performance.csv'],'tgt_dataset_id':curated['supplier_database.xlsx'],
        'relation_type':'HAS_SUPPLIER','src_key':'供应商','tgt_key':'供应商名称'})
s.post(f'{BASE}/api/v2/ontologies/{oid}/mappings/build-all')
log('Link Mappings 已创建')

# Logic & Action Discovery
for ep in ['logic','actions']:
    for pfx in ['/api/v1/ontologies','/api/v2/ontologies']:
        rr = s.get(f'{BASE}{pfx}/{oid}/{ep}')
        data = rr.json() if isinstance(rr.json(),list) else rr.json().get('data',[])
        for item in data: s.delete(f'{BASE}{pfx}/{oid}/{ep}/{item["id"]}')
s.post(f'{BASE}/api/v2/ontologies/{oid}/logic/discover')
s.post(f'{BASE}/api/v2/ontologies/{oid}/actions/discover')

# ═══ VERIFICATION ═══
print(f'\n{"="*50}')
print('  SUCCESS CRITERIA')
print(f'{"="*50}')

r = s.get(f'{BASE}/api/v2/ontologies/{oid}/graph?limit=300')
g = r.json()
nodes = len(g.get('nodes',[]))
edges = len(g.get('edges',[]))

r = s.get(f'{BASE}/api/v1/ontologies/{oid}/logic')
logic = r.json().get('data',[])
r = s.get(f'{BASE}/api/v1/ontologies/{oid}/actions')
acts = r.json().get('data',[])

checks = [
    ('Pipeline 8/8 成功', len(curated) >= 8),
    ('Entities > 0', res.get('total_entities',0) > 0),
    ('Logic Rules > 0', len(logic) > 0),
    ('Actions > 0', len(acts) > 0),
    ('Graph nodes > 0', nodes > 0),
    ('Graph edges > 0 (网络状结构)', edges > 0),
]
for label, ok in checks:
    print(f'  {"✅" if ok else "❌"} {label}')

all_ok = all(ok for _, ok in checks)
print(f'\n  {"ALL PASSED ✅" if all_ok else "SOME FAILED ❌"}')
print(f'  Graph: {nodes} nodes, {edges} edges')
print(f'  Logic: {len(logic)} | Actions: {len(acts)}')
print(f'  Entities: {res.get("total_entities")} | Relations: {res.get("total_relations")}')
print(f'')
print(f'  🔗 http://localhost:5173/ontologies/{oid}')
