# nano-ontoprompt 二次开发指引

## 项目概述

nano-ontoprompt 是一个基于 LLM 的轻量级本体构建平台，可从非结构化文档（PDF、Word、Excel 等）中自动提取结构化知识图谱。平台支持实体、逻辑规则、动作的提取，并提供可视化的知识图谱展示、导出和编辑功能。

---

## 技术架构

### 技术栈

| 层级 | 技术选型 |
|------|---------|
| 前端 | React 18, TypeScript, Vite, Tailwind CSS, react-i18next, TanStack Query, Cytoscape.js |
| 后端 | FastAPI, SQLAlchemy 2.0, SQLite / PostgreSQL |
| 任务队列 | Celery + Redis |
| LLM 客户端 | OpenAI SDK, Anthropic SDK |
| 知识图谱可视化 | Cytoscape.js |
| 导出格式 | JSON, YAML, CSV, Turtle (RDF), HTML |

### 项目目录结构

```
nano-ontoprompt/
├── backend/
│   ├── app/
│   │   ├── models/           # SQLAlchemy ORM 模型
│   │   │   ├── ontology.py   # OntologyProject 本体项目
│   │   │   ├── entity.py     # Entity 实体
│   │   │   ├── relation.py   # Relation 关系边
│   │   │   ├── logic.py      # LogicRule 逻辑规则
│   │   │   ├── action.py     # Action 动作
│   │   │   ├── file.py       # UploadedFile 上传文件
│   │   │   ├── prompt.py     # Prompt 提取提示词
│   │   │   ├── model_config.py # ModelConfig LLM 模型配置
│   │   │   ├── user.py       # User 用户
│   │   │   ├── extraction_task.py # ExtractionTask 提取任务
│   │   │   └── rules_config.py   # RulesConfig 置信度规则
│   │   ├── routers/          # FastAPI 路由 (API 端点)
│   │   │   ├── auth.py       # 认证相关
│   │   │   ├── users.py      # 用户管理
│   │   │   ├── overview.py   # 概览统计
│   │   │   ├── ontologies.py # 本体项目 CRUD
│   │   │   ├── files.py      # 文件上传
│   │   │   ├── entities.py   # 实体管理
│   │   │   ├── logic.py      # 逻辑规则管理
│   │   │   ├── actions.py    # 动作管理
│   │   │   ├── extraction.py # LLM 提取任务
│   │   │   ├── graph.py      # 知识图谱数据
│   │   │   ├── export.py     # 导出功能
│   │   │   ├── prompts.py    # 提示词管理
│   │   │   ├── models.py     # 模型配置
│   │   │   └── settings.py   # 系统设置
│   │   ├── services/         # 业务逻辑服务
│   │   │   ├── llm_service.py      # LLM 调用封装
│   │   │   ├── export_service.py   # 导出服务 (JSON/YAML/CSV/TTL/HTML)
│   │   │   ├── document_service.py # 文档转换 (PDF/DOCX/XLSX → Markdown)
│   │   │   ├── auth_service.py      # 认证服务
│   │   │   └── encryption_service.py # API Key 加密
│   │   ├── tasks/           # Celery 异步任务
│   │   │   └── extraction.py # LLM 提取主流程
│   │   ├── engine/           # 引擎核心
│   │   │   └── post_harness/
│   │   │       └── validator.py # P0 验证器
│   │   ├── schemas/          # Pydantic 请求/响应模型
│   │   ├── config.py         # 配置管理
│   │   ├── database.py       # 数据库连接
│   │   ├── deps.py           # 依赖注入 (get_db, get_current_user)
│   │   └── main.py           # FastAPI 应用入口
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── api/              # Axios API 客户端
│       │   ├── client.ts      # 基础请求配置
│       │   ├── auth.ts       # 认证 API
│       │   └── ontologies.ts # 本体相关 API
│       ├── components/       # 公共组件
│       │   ├── Layout.tsx    # 主布局
│       │   ├── ConfidenceBar.tsx # 置信度条
│       │   ├── StatusBadge.tsx   # 状态徽章
│       │   └── ConfirmDialog.tsx # 确认对话框
│       ├── pages/            # 页面组件
│       │   ├── login/        # 登录页
│       │   ├── register/     # 注册页
│       │   ├── overview/     # 概览仪表盘
│       │   ├── ontologies/   # 本体列表和详情
│       │   │   ├── list/     # 本体列表页
│       │   │   └── detail/   # 本体详情页
│       │   │       ├── entity/    # 实体详情
│       │   │       ├── logic/     # 逻辑规则详情
│       │   │       ├── action/    # 动作详情
│       │   │       └── tabs/      # InfoTab, EntitiesTab, FilesTab, GraphTab, LogicTab, ActionsTab
│       │   ├── prompts/      # 提示词管理
│       │   ├── models/      # 模型配置
│       │   └── settings/     # 系统设置
│       ├── stores/           # Zustand 状态管理
│       │   ├── authStore.ts  # 认证状态
│       │   └── uiStore.ts    # UI 状态 (语言等)
│       ├── i18n/             # 国际化
│       │   ├── en.json       # 英文
│       │   └── zh.json       # 中文
│       ├── types/            # TypeScript 类型定义
│       ├── utils/            # 工具函数
│       │   └── extractionRules.ts # 提取规则配置
│       ├── App.tsx           # 路由配置
│       └── main.tsx          # React 入口
├── docker-compose.yml
└── .env.example
```

---

## 核心数据模型

### Entity (实体)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | String | 主键 UUID |
| ontology_id | String | 所属本体 ID |
| name_cn | String | 中文名称 |
| name_en | String | 英文名称 |
| type | String | 实体类型 (Organization, Product, Material 等) |
| description | Text | 描述 |
| properties | JSON | 属性字典 |
| confidence | Float | 置信度 0~1 |
| version | String | 版本号 |

### LogicRule (逻辑规则)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | String | 主键 UUID |
| ontology_id | String | 所属本体 ID |
| name_cn | String | 规则中文名 |
| name_en | String | 规则英文名 |
| description | Text | 规则描述 |
| formula | Text | IF-THEN 公式 |
| linked_entities | Text (JSON) | 关联实体名称列表 |
| confidence | Float | 置信度 0~1 |

### Action (动作)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | String | 主键 UUID |
| ontology_id | String | 所属本体 ID |
| name_cn | String | 动作中文名 |
| name_en | String | 动作英文名 |
| description | Text | 动作描述 |
| execution_rule | Text | 触发条件和执行逻辑 |
| function_code | Text | Python 函数代码 |
| linked_entities | JSON | 关联实体 ID 列表 |
| linked_logic_ids | JSON | 关联逻辑规则 ID 列表 |
| confidence | Float | 置信度 0~1 |

### Relation (关系)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | String | 主键 UUID |
| ontology_id | String | 所属本体 ID |
| source_entity | String | 源实体 ID |
| target_entity | String | 目标实体 ID |
| type | String | 关系类型 (IS-A, PART-OF, supply 等) |
| properties | JSON | 关系属性 |
| confidence | Float | 置信度 0~1 |

---

## API 接口一览

所有 API 均以 `/api/v1/` 为前缀，认证方式为 Bearer Token (JWT)。

### 认证相关

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /auth/login | 登录 |
| POST | /auth/register | 注册 |
| GET | /auth/me | 获取当前用户 |

### 本体项目

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /ontologies | 列表 (分页、搜索) |
| POST | /ontologies | 创建 |
| GET | /ontologies/{id} | 详情 |
| PUT | /ontologies/{id} | 更新 |
| DELETE | /ontologies/{id} | 删除 |

### 实体

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /ontologies/{id}/entities | 列表 |
| POST | /ontologies/{id}/entities | 创建 |
| PUT | /ontologies/{id}/entities/{eid} | 更新 |
| DELETE | /ontologies/{id}/entities/{eid} | 删除 |

### 逻辑规则

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /ontologies/{id}/logic | 列表 |
| POST | /ontologies/{id}/logic | 创建 |
| PUT | /ontologies/{id}/logic/{lid} | 更新 |
| DELETE | /ontologies/{id}/logic/{lid} | 删除 |

### 动作

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /ontologies/{id}/actions | 列表 |
| POST | /ontologies/{id}/actions | 创建 |
| PUT | /ontologies/{id}/actions/{aid} | 更新 |
| DELETE | /ontologies/{id}/actions/{aid} | 删除 |

### 知识图谱

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /ontologies/{id}/graph | 获取图数据 (nodes + edges) |
| POST | /ontologies/{id}/graph/relations | 创建关系 |
| DELETE | /ontologies/{id}/graph/relations/{rid} | 删除关系 |

### 文件

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /ontologies/{id}/files | 列表 |
| POST | /ontologies/{id}/files | 上传 (multipart/form-data) |
| DELETE | /ontologies/{id}/files/{fid} | 删除 |

### LLM 提取

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /ontologies/{id}/execute | 启动提取任务 |
| GET | /ontologies/{id}/execute/status?task_id=xxx | 查询任务状态 |

### 导出

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /ontologies/{id}/export?format=json | 导出 (json/yaml/csv/ttl/html) |

### 提示词

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /prompts | 列表 |
| POST | /prompts | 创建 |
| GET | /prompts/{id} | 详情 |
| PUT | /prompts/{id} | 更新 |
| DELETE | /prompts/{id} | 删除 |

### 模型配置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /models | 列表 |
| POST | /models | 创建 |
| PUT | /models/{id} | 更新 |
| DELETE | /models/{id} | 删除 |
| POST | /models/{id}/test | 测试连接 |

---

## LLM 提取流程详解

提取任务由 Celery 异步执行，流程如下：

```
1. 加载文件 → 合并转换为 Markdown
2. 调用 LLM (Pass 1) → extract_ontology()
3. 置信度校准 → _calibrate_confidence()
4. P0 验证 → PostHarnessValidator
5. (可选) Pass 2 → infer_relations() 补全关系
6. 去重合并 → upsert entities/logic/actions/relations
7. 保存到数据库
```

### Pass 1: extract_ontology()

调用 LLM 从文档中提取本体信息，返回结构：

```json
{
  "entities": [...],
  "relations": [...],
  "logic_rules": [...],
  "actions": [...]
}
```

### 置信度校准

根据客观完整性信号调整 LLM 返回的置信度分数：
- 实体无属性 → -0.10
- 实体无描述 → -0.05
- 实体在关系图中 → +0.05
- 逻辑规则无 linked_entities → -0.10
- 逻辑规则无 formula → -0.05
- 动作无 function_code 或代码过短 → -0.20
- function_code 语法错误 → -0.15

### Pass 2: infer_relations()

当关系稀疏（< 40% 实体有边）或孤立实体过多（> 30%）时，触发二次关系推断。

### P0 验证器

验证提取结果的结构完整性和引用完整性：
- **FATAL**: entities 缺失/为空/非数组
- **ERROR**: 实体引用断裂、重复项、类型非法、function_code 语法错误
- **WARNING**: 缺少描述、缺少属性、缺少 linked_entities
- **INFO**: 字段为空字符串

---

## 添加新功能的开发指南

### 1. 添加新的数据模型

**步骤 1**: 在 `backend/app/models/` 创建新模型文件，例如 `new_model.py`：

```python
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Text, Float
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class NewModel(Base):
    __tablename__ = "new_models"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
```

**步骤 2**: 在 `backend/app/models/__init__.py` 中导入。

**步骤 3**: 创建对应的 Pydantic Schema（`backend/app/schemas/new_model.py`）：

```python
from pydantic import BaseModel
from typing import Optional

class NewModelCreate(BaseModel):
    name: str
    description: Optional[str] = None

class NewModelOut(BaseModel):
    id: str
    ontology_id: str
    name: str
    description: Optional[str]
    confidence: float
    class Config:
        from_attributes = True
```

**步骤 4**: 创建 API 路由（`backend/app/routers/new_models.py`）：

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.new_model import NewModel
from app.schemas.new_model import NewModelCreate, NewModelOut

router = APIRouter()

@router.get("")
def list_new_models(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    items = db.query(NewModel).filter(NewModel.ontology_id == ontology_id).all()
    return {"data": [NewModelOut.model_validate(i).model_dump() for i in items]}

@router.post("")
def create_new_model(ontology_id: str, body: NewModelCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    # 实现创建逻辑
    pass
```

**步骤 5**: 在 `backend/app/main.py` 中注册路由：

```python
from app.routers import new_models
app.include_router(new_models.router, prefix="/api/v1/ontologies/{ontology_id}/new_models", tags=["new_models"])
```

**步骤 6**: 前端 API 客户端（`frontend/src/api/new_models.ts`）：

```typescript
import { client } from './client'

export const newModelApi = {
  list: (ontologyId: string) => client.get(`/ontologies/${ontologyId}/new_models`),
  create: (ontologyId: string, data: any) => client.post(`/ontologies/${ontologyId}/new_models`, data),
}
```

### 2. 扩展 LLM 提取流程

#### 添加新的提取输出类型

修改 `backend/app/services/llm_service.py` 中的 `extract_ontology()` 函数，扩展返回结构：

```python
def extract_ontology(text: str, prompt_content: str, model_config: dict, model_name: str, retry_count: int = 3) -> dict:
    # ... existing code ...
    result = _parse_response(raw)
    result["new_field"] = process_new_field(text, model_config, model_name)  # 添加新字段
    return result
```

#### 自定义验证规则

修改 `backend/app/engine/post_harness/validator.py` 中的 `PostHarnessValidator` 类：

```python
def _new_check(self, data: dict, report: ValidationReport):
    new_items = data.get("new_field", [])
    for item in new_items:
        if not item.get("required_field"):
            report.add(Severity.WARNING, "MISSING_REQUIRED", "new_field 缺少必填字段")

def validate(self, data: dict, allowed_types: Optional[set] = None) -> ValidationReport:
    report = ValidationReport()
    # ... existing checks ...
    self._new_check(data, report)  # 添加新检查
    return report
```

### 3. 添加新的导出格式

修改 `backend/app/services/export_service.py`：

```python
def export_new_format(db: Session, ontology_id: str) -> str:
    data = _collect_data(db, ontology_id)
    # 实现新的导出逻辑
    return serialized_output

# 在 router 中添加
@router.get("/export")
def export_ontology(format: str = "json", ...):
    if format == "new_format":
        return export_new_format(db, ontology_id)
    # ...
```

### 4. 添加新的关系类型

关系类型在多个位置定义，需要统一修改：

1. `backend/app/services/llm_service.py` - `infer_relations()` 函数中的关系类型列表
2. `backend/app/routers/prompts.py` - `BUILTIN_PROMPTS` 中的关系类型说明
3. `backend/app/engine/post_harness/validator.py` - `DEFAULT_ALLOWED_TYPES`（如需限制）

### 5. 添加新的实体类型

实体类型同样需要多出修改：

1. `backend/app/routers/prompts.py` - `BUILTIN_PROMPTS` 中的实体类型说明
2. `backend/app/engine/post_harness/validator.py` - `DEFAULT_ALLOWED_TYPES`

### 6. 添加新的 API Provider

如需支持新的 LLM Provider（如 Google Gemini），修改 `backend/app/services/llm_service.py`：

```python
def _call_llm(provider: str, api_key: str, api_base: str | None, model: str, messages: list) -> str:
    if provider == "anthropic":
        # Anthropic 实现
        pass
    elif provider == "gemini":
        # 添加 Google Gemini 支持
        import google.generativeai as gemini
        client = gemini.Client(api_key=api_key)
        # ...
    else:
        # OpenAI 默认实现
        pass
```

---

## 前端开发指南

### 组件结构

前端使用 React 18 + TypeScript，采用以下架构：

- **路由**: React Router v6，配置在 `App.tsx`
- **状态管理**: Zustand (`stores/authStore.ts`, `stores/uiStore.ts`)
- **数据请求**: TanStack Query + Axios
- **国际化**: react-i18next
- **UI 样式**: Tailwind CSS + Lucide Icons

### 添加新页面

**步骤 1**: 在 `frontend/src/pages/` 下创建页面目录和组件。

**步骤 2**: 在 `App.tsx` 中添加路由：

```tsx
import NewPage from '@/pages/new/NewPage'

<Route path="/new" element={<ProtectedRoute><NewPage /></ProtectedRoute>} />
```

**步骤 3**: 在侧边栏 `Layout.tsx` 中添加导航项：

```tsx
const navItems = [
  // ... existing items
  { to: '/new', icon: NewIcon, label: t('nav.new') },
]
```

**步骤 4**: 在 `frontend/src/i18n/zh.json` 和 `en.json` 中添加翻译。

### API 调用模式

```typescript
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { newModelApi } from '@/api/new_models'

// 查询
const { data, isLoading } = useQuery({
  queryKey: ['new_models', ontologyId],
  queryFn: () => newModelApi.list(ontologyId).then(r => r.data.data),
})

// 变更
const mutation = useMutation({
  mutationFn: (data: any) => newModelApi.create(ontologyId, data),
  onSuccess: () => {
    queryClient.invalidateQueries({ queryKey: ['new_models', ontologyId] })
  },
})
```

---

## 配置说明

### 环境变量 (.env)

| 变量 | 默认值 | 说明 |
|------|--------|------|
| DATABASE_URL | sqlite:///./ontoprompt.db | 数据库连接 (SQLite 或 PostgreSQL) |
| REDIS_URL | redis://localhost:6379/0 | Celery Redis 后端 |
| SECRET_KEY | dev-secret-key | JWT 签名密钥 |
| ENCRYPTION_KEY | (空) | API Key 加密密钥 (可选) |
| FIRST_ADMIN_USER | admin | 初始管理员用户名 |
| FIRST_ADMIN_PASSWORD | changeme123 | 初始管理员密码 |
| UPLOADS_DIR | ./uploads | 文件上传目录 |
| ACCESS_TOKEN_EXPIRE_MINUTES | 1440 | Token 过期时间 (分钟) |

### 置信度规则配置

置信度阈值可在数据库 `rules_config` 表中配置：

| rule_key | 默认值 | 说明 |
|----------|--------|------|
| confidence_entity_min | 0.5 | 实体最低置信度 |
| confidence_logic_min | 0.6 | 逻辑规则最低置信度 |
| confidence_action_min | 0.6 | 动作最低置信度 |
| confidence_relation_min | 0.5 | 关系最低置信度 |
| confidence_high_threshold | 0.9 | 高置信度阈值 |
| confidence_medium_threshold | 0.7 | 中置信度阈值 |
| confidence_low_threshold | 0.5 | 低置信度阈值 |
| confidence_display_dashed_below | 0.7 | 低于此值显示虚线边 |

---

## 测试

### 后端测试

```bash
cd backend
pytest tests/ -v
```

### 前端 E2E 测试

前端使用 Playwright 进行 E2E 测试：

```bash
cd frontend
npx playwright test
```

---

## Docker 部署

```bash
# 开发环境
cp .env.example .env
docker compose up --build

# 生产环境需修改:
# - SECRET_KEY 为强密码
# - ENCRYPTION_KEY 用于加密 API Key
# - DATABASE_URL 切换为 PostgreSQL
# - 配置反向代理 (Nginx)
```

---

## 常见开发场景

### 场景 1: 修改现有实体的字段

1. 修改 `backend/app/models/entity.py` 添加新字段
2. 修改 `backend/app/schemas/entity.py` 添加 Pydantic 字段
3. 如需迁移 Alembic: `alembic revision --autogenerate -m "add field"`
4. 前端 `EntityDetailPage.tsx` 添加对应表单项

### 场景 2: 修改 LLM Prompt 模板

编辑 `backend/app/routers/prompts.py` 中的 `BUILTIN_PROMPTS` 列表，或在 UI 中创建新的 Prompt。

### 场景 3: 添加文件格式支持

修改 `backend/app/services/document_service.py` 中的 `convert_to_markdown()` 函数，添加新的格式解析逻辑。

### 场景 4: 修改知识图谱可视化

- 图数据获取: `backend/app/routers/graph.py`
- 可视化组件: `frontend/src/pages/ontologies/detail/tabs/GraphTab.tsx`
- 使用 Cytoscape.js 进行渲染

### 场景 5: 添加权限控制

现有权限层级：
- `get_current_user`: 所有认证用户
- `require_admin`: 仅管理员

如需更细粒度的权限控制，可在 `backend/app/deps.py` 中添加新的依赖函数。

---

## 内置提示词模板

项目内置以下提示词模板（定义在 `backend/app/routers/prompts.py`）：

| 模板名称 | 领域 | 支持的实体类型 |
|---------|------|---------------|
| 通用本体提取 | 其他 | Organization, Product, Material, Category, Document, Process, Facility, Concept |
| 供应链本体提取 | 供应链 | Supplier, Product, Material, Warehouse, Document, Category, Process |
| 医疗本体提取 | 医疗 | Disease, Drug, Symptom, Treatment, Facility, Category, Process |

每个模板定义了：
- 实体类型及其说明
- 关系类型（IS-A, PART-OF, INSTANCE-OF, supply, stores, processes, treats, causes, 关联）
- 输出格式要求（JSON）
- 字段约束（properties, linked_entities, function_code 等）

---

## 关键文件索引

| 功能 | 后端文件 | 前端文件 |
|------|---------|---------|
| 认证 | `app/services/auth_service.py`, `app/routers/auth.py` | `stores/authStore.ts`, `api/auth.ts` |
| 本体 CRUD | `app/routers/ontologies.py`, `app/models/ontology.py` | `pages/ontologies/*` |
| LLM 提取 | `app/tasks/extraction.py`, `app/services/llm_service.py` | `pages/ontologies/detail/tabs/InfoTab.tsx` |
| 文档转换 | `app/services/document_service.py` | `pages/ontologies/detail/tabs/FilesTab.tsx` |
| 图数据 | `app/routers/graph.py` | `pages/ontologies/detail/tabs/GraphTab.tsx` |
| 导出 | `app/services/export_service.py`, `app/routers/export.py` | `InfoTab.tsx` 导出按钮 |
| 验证 | `app/engine/post_harness/validator.py` | `InfoTab.tsx` Quality Report |
| 提示词管理 | `app/routers/prompts.py` | `pages/prompts/*` |
| 模型配置 | `app/routers/models.py` | `pages/models/ModelsPage.tsx` |
