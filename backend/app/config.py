from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    environment: str = "development"  # development | production
    database_url: str = "sqlite:///./ontoprompt.db"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-secret-key"
    encryption_key: str = ""
    first_admin_user: str = "admin"
    first_admin_password: str = "admin123"
    uploads_dir: str = "./uploads"
    access_token_expire_minutes: int = 1440  # 24h

    # 上传限制
    max_upload_mb: int = 200
    allowed_upload_extensions: str = "csv,xlsx,xls,json,xml,pdf,docx,doc,pptx,ppt,md,txt"

    # v2 — Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "ontoprompt123"

    # v2 — MinIO
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_use_ssl: bool = False

    # v2 — ChromaDB
    chroma_host: str = "localhost"
    chroma_port: int = 8001

    model_config = {"env_file": ".env"}

settings = Settings()

# 生产环境禁止使用默认凭据 — 启动即失败, 避免带默认密钥上线
if settings.environment == "production":
    _insecure = []
    if settings.secret_key == "dev-secret-key":
        _insecure.append("SECRET_KEY")
    if settings.first_admin_password == "admin123":
        _insecure.append("FIRST_ADMIN_PASSWORD")
    if settings.minio_access_key == "minioadmin" or settings.minio_secret_key == "minioadmin":
        _insecure.append("MINIO_ACCESS_KEY/MINIO_SECRET_KEY")
    if not settings.encryption_key:
        _insecure.append("ENCRYPTION_KEY")
    if _insecure:
        raise RuntimeError(
            f"ENVIRONMENT=production 但以下配置仍为默认值, 必须通过环境变量注入: {', '.join(_insecure)}"
        )
