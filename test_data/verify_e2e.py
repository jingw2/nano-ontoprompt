#!/usr/bin/env python3
"""全流程 E2E 验证：供应链8文件 → Pipeline → Curated → Ontology"""
import requests, time
from pathlib import Path

BASE = 'http://localhost:8000'
DATA = Path(__file__).parent / '供应链'
s = requests.Session()

r = s.post(f'{BASE}/api/v1/auth/login', json={'username':'admin','password':'admin123'})
s.headers.update({'Authorization': f'Bearer {r.json()["data"]["access_token"]}'})
ts = int(time.time())

print('='*60)
print('  E2E 全流程验证：供应链 → Pipeline → Ontology')
print('='*60)

# 1. Upload
print('\n── 1. 上传 8 文件 ──')
fm = {}
for f in sorted(DATA.glob('*')):
    with open(f, 'rb') as fh:
        r = s.post(f'{BASE}/api/v2/datasets/upload', files={'file':(f.name, fh, 'application/octet-stream')})
    if r.ok:
        d = r.json()
        did = d.get('data',d).get('id') if isinstance(d,dict) else None
        fm[f.name] = did
        print(f'  ✅ {f.name:35s} → {did[:8] if did else "?"}')
    time.sleep(0.1)

# 2. Pipeline + Run
print('\n── 2. Pipeline ──')
curated = {}
for fname in fm:
    ext = fname.rsplit('.',1)[-1].lower()
    route_cfg = 'unstructured' if ext in ('docx','pptx','pdf','md') else ('semi_structured' if ext=='json' else 'structured')
    name = fname.rsplit('.',1)[0]
    r = s.post(f'{BASE}/api/v2/pipelines', json={
        'name': f'E2E-{name}-{ts}', 'domain': '供应链',
        'definition':{
            'nodes':[
                {'id':'conn','type':'connector','label':'数据源','position':{'x':50,'y':200},
                 'config':{'source_type':'file','files':[{'name':fname}]}},
                {'id':'stor','type':'storage','label':'存储器','position':{'x':250,'y':200}},
                {'id':'xfm','type':'transform','label':'转换器','position':{'x':450,'y':200},
                 'config':{'path':route_cfg}},
                {'id':'out','type':'output','label':'输出','position':{'x':680,'y':200},
                 'config':{'dataset_type':'curated_dataset','primary_key':['id']}},
            ],
            'edges':[{'id':'e1','source':'conn','target':'stor'},{'id':'e2','source':'stor','target':'xfm'},{'id':'e3','source':'xfm','target':'out'}],
        },
    })
    if r.ok:
        pid = r.json()['id']
        r2 = s.post(f'{BASE}/api/v2/pipelines/{pid}/run-sync')
        if r2.ok:
            st = r2.json().get('stats',{})
            cid = st.get('curated_dataset_id')
            if cid: curated[fname] = cid
            print(f'  {"✅" if st.get("rows_in",0)>0 else "❌"} {fname:35s} rows_in={st.get("rows_in",0)}')
    time.sleep(0.15)

# 3. Approve
print('\n── 3. 审批 ──')
for cid in curated.values():
    s.post(f'{BASE}/api/v2/curated/{cid}/review?action=approve')
print(f'  ✅ {len(curated)} 个')

# 4. Ontology
print('\n── 4. Ontology ──')
r = s.post(f'{BASE}/api/v1/ontologies', json={
    'name': f'供应链知识图谱-全流程-{ts}', 'domain': '供应链',
    'description': 'E2E 测试', 'build_mode': 'pipeline_mapping',
})
d = r.json()
oid = d.get('data',d).get('id') if isinstance(d,dict) else None
print(f'  ✅ Ontology: {oid[:8] if oid else "FAIL"}')

entity_map = {
    'inventory_transactions.csv': 'InventoryTransaction',
    'logistics_performance.csv': 'LogisticsRecord',
    'supplier_database.xlsx': 'Supplier',
    'supplier_orders.json': 'PurchaseOrder',
}

if oid:
    for fname, cid in curated.items():
        etype = entity_map.get(fname, f'E_{fname.split(".")[0][:15]}')
        r = s.post(f'{BASE}/api/v2/ontologies/{oid}/mappings', json={
            'curated_dataset_id': cid, 'entity_class': etype,
            'field_mapping': {'__primary_key__': 'id'},
        })
        mid = r.json().get('mapping_id','') if r.ok else ''
        if mid:
            s.post(f'{BASE}/api/v2/ontologies/{oid}/mappings/{mid}/apply-from-dataset')
            print(f'  ✅ {etype:25s}')

    # Build-all
    r = s.post(f'{BASE}/api/v2/ontologies/{oid}/mappings/build-all')
    res = r.json()
    print(f'  build-all: entities={res.get("total_entities")} relations={res.get("total_relations")} chroma={res.get("chroma_entities_written")}')

    # Link Mappings
    print('\n── 5. Link Mappings ──')
    for src_f, tgt_f, rel, sk, tk in [
        ('logistics_performance.csv','supplier_database.xlsx','HAS_SUPPLIER','供应商','供应商名称'),
        ('inventory_transactions.csv','logistics_performance.csv','STORED_IN','物料编码','承运商'),
    ]:
        if src_f in curated and tgt_f in curated:
            s.post(f'{BASE}/api/v2/ontologies/{oid}/link-mappings', json={
                'src_dataset_id': curated[src_f], 'tgt_dataset_id': curated[tgt_f],
                'relation_type': rel, 'src_key': sk, 'tgt_key': tk,
            })
            print(f'  ✅ {rel}')

    r = s.post(f'{BASE}/api/v2/ontologies/{oid}/mappings/build-all')
    res2 = r.json()
    print(f'  build-all: entities={res2.get("total_entities")} relations={res2.get("total_relations")}')

    # Logic + Actions
    print('\n── 6. Logic & Action Discovery ──')
    for ep in ['logic','actions']:
        for pfx in ['/api/v1/ontologies','/api/v2/ontologies']:
            r = s.get(f'{BASE}{pfx}/{oid}/{ep}')
            data = r.json() if isinstance(r.json(),list) else r.json().get('data',[])
            for item in data: s.delete(f'{BASE}{pfx}/{oid}/{ep}/{item["id"]}')

    ld = s.post(f'{BASE}/api/v2/ontologies/{oid}/logic/discover').json()
    ad = s.post(f'{BASE}/api/v2/ontologies/{oid}/actions/discover').json()
    print(f'  Logic: v1={ld.get("total_v1")}  Actions: v1={ad.get("total_v1")}')

    # VERIFICATION
    print(f'\n{"="*50}')
    print('  CRITERIA')
    print(f'{"="*50}')
    r = s.get(f'{BASE}/api/v1/ontologies/{oid}/entities')
    ents = r.json() if r.ok else {}
    if isinstance(ents, dict): ents = ents.get('data',{}).get('items',[])
    print(f'  {"✅" if len(ents)>0 else "❌"} Entities: {len(ents)}')

    r = s.get(f'{BASE}/api/v1/ontologies/{oid}/logic')
    logic = r.json().get('data',[]) if r.ok else []
    print(f'  {"✅" if len(logic)>0 else "❌"} Logic: {len(logic)}')

    r = s.get(f'{BASE}/api/v1/ontologies/{oid}/actions')
    acts = r.json().get('data',[]) if r.ok else []
    print(f'  {"✅" if len(acts)>0 else "❌"} Actions: {len(acts)}')

    r = s.get(f'{BASE}/api/v2/ontologies/{oid}/graph?limit=300')
    g = r.json() if r.ok else {}
    print(f'  {"✅" if len(g.get("nodes",[]))>0 else "❌"} Graph nodes: {len(g.get("nodes",[]))}')
    print(f'  {"✅" if len(g.get("edges",[]))>0 else "❌"} Graph edges: {len(g.get("edges",[]))}')

    ok = len(ents)>0 and len(logic)>0 and len(acts)>0 and len(g.get("nodes",[]))>0
    print(f'\n  {"ALL PASSED ✅" if ok else "SOME FAILED ❌"}')
    print(f'  URL: http://localhost:5173/ontologies/{oid}')
