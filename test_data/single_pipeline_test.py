#!/usr/bin/env python3
"""单 Pipeline 全流程：1个连接器上传全部8文件 → 各自运行 → Ontology"""
import requests, time
from pathlib import Path

BASE = 'http://localhost:8000'
DATA = Path(__file__).parent / '供应链'
s = requests.Session()
r = s.post(f'{BASE}/api/v1/auth/login', json={'username':'admin','password':'admin123'})
s.headers.update({'Authorization': f'Bearer {r.json()["data"]["access_token"]}'})
ts = int(time.time())

print('='*60)
print('  单 Pipeline 全流程：1个连接器含全部8文件')
print('='*60)

# 1. Upload
print('\n-- 1. 上传全部8文件 --')
all_files = []
for f in sorted(DATA.glob('*')):
    with open(f, 'rb') as fh:
        r = s.post(f'{BASE}/api/v2/datasets/upload', files={'file':(f.name, fh, 'application/octet-stream')})
    if r.ok:
        d = r.json()
        did = d.get('data',d).get('id') if isinstance(d,dict) else None
        all_files.append({'name': f.name, 'size': f.stat().st_size, 'did': did})
        print(f'  ✅ {f.name:35s} → {did[:8] if did else "?"}')
    time.sleep(0.1)

# 2. Create ONE Pipeline with all files in connector
print('\n-- 2. 创建 Pipeline (1个Connector含全部8文件) --')
routes = {'csv':'structured','xlsx':'structured','json':'semi_structured','md':'unstructured',
          'docx':'unstructured','pptx':'unstructured','pdf':'unstructured'}

r = s.post(f'{BASE}/api/v2/pipelines', json={
    'name': f'供应链全格式-{ts}', 'domain': '供应链',
    'definition':{
        'nodes':[
            {'id':'conn','type':'connector','label':'数据源(8文件)','position':{'x':50,'y':200},
             'config':{'source_type':'file','files':[{'name':f['name'],'size':f['size']} for f in all_files]}},
            {'id':'stor','type':'storage','label':'存储器','position':{'x':250,'y':200}},
            {'id':'xfm','type':'transform','label':'转换器(自动检测)','position':{'x':450,'y':200},
             'config':{'path':'auto'}},
            {'id':'out','type':'output','label':'输出(全部)','position':{'x':680,'y':200},
             'config':{'dataset_type':'curated_dataset','primary_key':['id']}},
        ],
        'edges':[{'id':'e1','source':'conn','target':'stor'},{'id':'e2','source':'stor','target':'xfm'},{'id':'e3','source':'xfm','target':'out'}],
    },
})
main_pid = r.json()['id'] if r.ok else None
print(f'  主Pipeline: {main_pid[:8] if main_pid else "FAIL"} ✅')

# 3. Run individual pipelines per file
print('\n-- 3. 每条Pipeline运行 --')
curated = {}
for f in all_files:
    ext = f['name'].rsplit('.',1)[-1].lower()
    rc = routes.get(ext, 'structured')
    r = s.post(f'{BASE}/api/v2/pipelines', json={
        'name': f'{f["name"].split(".")[0]}-{ts}', 'domain': '供应链',
        'definition':{
            'nodes':[
                {'id':'c','type':'connector','label':'数据源','position':{'x':50,'y':200},
                 'config':{'source_type':'file','files':[{'name':f['name']}]}},
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
            if cid: curated[f['name']] = cid
            print(f'  {"✅" if st.get("rows_in",0)>0 else "❌"} {f["name"]:35s} rows_in={st.get("rows_in",0)}')
    time.sleep(0.1)

# 4. Ontology
print('\n-- 4. Ontology Mapping --')
for cid in curated.values(): s.post(f'{BASE}/api/v2/curated/{cid}/review?action=approve')

r = s.post(f'{BASE}/api/v1/ontologies', json={'name':f'供应链全集-{ts}','domain':'供应链','build_mode':'pipeline_mapping'})
d = r.json()
oid = d.get('data',d).get('id') if isinstance(d,dict) else None
print(f'  Ontology: {oid[:8] if oid else "?"}')

emap = {'inventory_transactions.csv':'InventoryTransaction','logistics_performance.csv':'LogisticsRecord',
        'supplier_database.xlsx':'Supplier','supplier_orders.json':'PurchaseOrder','supply_chain_strategy.md':'Strategy',
        'procurement_policy.docx':'ProcurementPolicy','supply_chain_review.pptx':'Review','warehouse_management.pdf':'Warehouse'}

for fname, cid in curated.items():
    en = emap.get(fname, f'E_{fname[:10]}')
    r = s.post(f'{BASE}/api/v2/ontologies/{oid}/mappings', json={'curated_dataset_id':cid,'entity_class':en,'field_mapping':{'__primary_key__':'id'}})
    mid = r.json().get('mapping_id','') if r.ok else ''
    if mid: s.post(f'{BASE}/api/v2/ontologies/{oid}/mappings/{mid}/apply-from-dataset')

# Build-all
r = s.post(f'{BASE}/api/v2/ontologies/{oid}/mappings/build-all')
res = r.json()

# Links
if 'logistics_performance.csv' in curated and 'supplier_database.xlsx' in curated:
    s.post(f'{BASE}/api/v2/ontologies/{oid}/link-mappings', json={
        'src_dataset_id':curated['logistics_performance.csv'],'tgt_dataset_id':curated['supplier_database.xlsx'],
        'relation_type':'HAS_SUPPLIER','src_key':'供应商','tgt_key':'供应商名称'})
r = s.post(f'{BASE}/api/v2/ontologies/{oid}/mappings/build-all')
res2 = r.json()

# Discovery
for ep in ['logic','actions']:
    for pfx in ['/api/v1/ontologies','/api/v2/ontologies']:
        rr = s.get(f'{BASE}{pfx}/{oid}/{ep}')
        data = rr.json() if isinstance(rr.json(),list) else rr.json().get('data',[])
        for item in data: s.delete(f'{BASE}{pfx}/{oid}/{ep}/{item["id"]}')
ld = s.post(f'{BASE}/api/v2/ontologies/{oid}/logic/discover').json()
ad = s.post(f'{BASE}/api/v2/ontologies/{oid}/actions/discover').json()

# Results
print(f'\n{"="*50}')
print(f'  最终验证')
print(f'{"="*50}')
r = s.get(f'{BASE}/api/v1/ontologies/{oid}/logic')
logic = r.json().get('data',[])
print(f'  Logic: {len(logic)}')
r = s.get(f'{BASE}/api/v1/ontologies/{oid}/actions')
acts = r.json().get('data',[])
print(f'  Actions: {len(acts)}')
r = s.get(f'{BASE}/api/v2/ontologies/{oid}/graph?limit=300')
g = r.json()
print(f'  Graph: {len(g.get("nodes",[]))} nodes, {len(g.get("edges",[]))} edges')
print(f'  entities={res2.get("total_entities")} relations={res2.get("total_relations")}')
print(f'  URL: http://localhost:5173/ontologies/{oid}')
