import os
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
import json

class Settings(BaseSettings):
    # Application Settings
    app_name: str = Field(default="Candor Foods IMS", alias="APP_NAME")
    app_version: str = Field(default="1.0.0", alias="APP_VERSION")
    ENVIRONMENT: str = Field(default="development", alias="ENVIRONMENT")
    debug: bool = Field(default=True, alias="DEBUG")
    
    # Server Configuration
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    reload: bool = Field(default=True, alias="RELOAD")
    
    # Database Configuration
    DB_HOST: str = Field(default="localhost", alias="DB_HOST")
    DB_PORT: int = Field(default=5432, alias="DB_PORT")
    DB_NAME: str = Field(default="test_db", alias="DB_NAME")
    DB_USER: str = Field(default="test_user", alias="DB_USER")
    DB_PASSWORD: str = Field(default="test_password", alias="DB_PASSWORD")
    database_url: str = Field(default="sqlite:///./candor_foods_ims.db", alias="DATABASE_URL")
    
    # JWT Authentication
    JWT_SECRET: str = Field(default="your-super-secret-jwt-key-change-this-in-production", alias="JWT_SECRET")
    JWT_ALGORITHM: str = Field(default="HS256", alias="JWT_ALGORITHM")
    JWT_EXPIRATION_HOURS: int = Field(default=30, alias="JWT_EXPIRATION_HOURS")
    jwt_access_token_expire_minutes: int = Field(default=30, alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
    jwt_refresh_token_expire_days: int = Field(default=7, alias="JWT_REFRESH_TOKEN_EXPIRE_DAYS")
    
    # Password Hashing
    bcrypt_rounds: int = Field(default=12, alias="BCRYPT_ROUNDS")
    
    # OpenFGA Configuration
    openfga_api_url: str = Field(default="http://localhost:8080", alias="OPENFGA_API_URL")
    openfga_store_id: str = Field(default="", alias="OPENFGA_STORE_ID")
    openfga_model_id: Optional[str] = Field(default=None, alias="OPENFGA_MODEL_ID")
    openfga_enabled: bool = Field(default=True, alias="OPENFGA_ENABLED")

    # AI/ML Services Configuration
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    claude_api_key: Optional[str] = Field(default=None, alias="CLAUDE_API_KEY")
    
    # AWS S3 Configuration
    aws_access_key_id: Optional[str] = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: Optional[str] = Field(default=None, alias="AWS_SECRET_ACCESS_KEY")
    aws_kms_key_id: Optional[str] = Field(default=None, alias="AWS_KMS_KEY_ID")
    aws_s3_bucket: Optional[str] = Field(default=None, alias="AWS_S3_BUCKET")
    
    # Email Configuration
    email_enabled: bool = Field(default=False, alias="EMAIL_ENABLED")
    smtp_host: str = Field(default="smtp.gmail.com", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: Optional[str] = Field(default=None, alias="SMTP_USER")
    smtp_password: Optional[str] = Field(default=None, alias="SMTP_PASSWORD")
    from_email: Optional[str] = Field(default=None, alias="FROM_EMAIL")
    aws_region: str = Field(default="ap-south-1", alias="AWS_REGION")
    aws_s3_bucket_name: str = Field(default="complaint-module-images", alias="AWS_S3_BUCKET_NAME")
    
    # CORS Configuration
    API_CORS_ORIGINS: Optional[str] = Field(default=None, alias="API_CORS_ORIGINS")
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8080", "http://localhost:8081", "*"])
    cors_allow_credentials: bool = Field(default=True, alias="CORS_ALLOW_CREDENTIALS")
    
    # File Upload Configuration
    upload_dir: str = Field(default="./uploads", alias="UPLOAD_DIR")
    max_file_size: int = Field(default=10485760, alias="MAX_FILE_SIZE")  # 10MB
    allowed_extensions: List[str] = Field(default=[".jpg", ".jpeg", ".png", ".pdf", ".xlsx", ".csv"], alias="ALLOWED_EXTENSIONS")
    
    # Logging Configuration
    LOG_LEVEL: str = Field(default="INFO", alias="LOG_LEVEL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="%(asctime)s - %(name)s - %(levelname)s - %(message)s", alias="LOG_FORMAT")
    log_file: str = Field(default="./logs/app.log", alias="LOG_FILE")
    
    # Redis Configuration
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    redis_enabled: bool = Field(default=False, alias="REDIS_ENABLED")
    
    # Email Configuration
    smtp_server: str = Field(default="smtp.gmail.com", alias="SMTP_SERVER")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_username: str = Field(default="", alias="SMTP_USERNAME")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    email_from: str = Field(default="noreply@candorfoods.com", alias="EMAIL_FROM")
    email_enabled: bool = Field(default=False, alias="EMAIL_ENABLED")
    
    # Company Configuration
    default_company_code: str = Field(default="CANDOR", alias="DEFAULT_COMPANY_CODE")
    default_company_name: str = Field(default="Candor Foods", alias="DEFAULT_COMPANY_NAME")
    multi_company_enabled: bool = Field(default=True, alias="MULTI_COMPANY_ENABLED")

    # WhatsApp Business API Configuration (Meta Cloud API)
    whatsapp_api_url: str = Field(default="https://graph.facebook.com/v21.0", alias="WHATSAPP_API_URL")
    whatsapp_access_token: Optional[str] = Field(default=None, alias="WHATSAPP_ACCESS_TOKEN")
    whatsapp_phone_number_id: Optional[str] = Field(default=None, alias="WHATSAPP_PHONE_NUMBER_ID")
    whatsapp_verify_token: str = Field(default="candor_visitor_webhook", alias="WHATSAPP_VERIFY_TOKEN")
    whatsapp_enabled: bool = Field(default=False, alias="WHATSAPP_ENABLED")
    
    # Frontend/Dashboard URL Configuration
    frontend_url: str = Field(default="https://q80bvqq1-3000.inc1.devtunnels.ms", alias="FRONTEND_URL")
    dashboard_url: Optional[str] = Field(default="https://q80bvqq1-3000.inc1.devtunnels.ms/dashboard", alias="DASHBOARD_URL")
    
    # Inventory Configuration
    qr_code_prefix: str = Field(default="CF", alias="QR_CODE_PREFIX")
    barcode_prefix: str = Field(default="880", alias="BARCODE_PREFIX")
    auto_generate_sku: bool = Field(default=True, alias="AUTO_GENERATE_SKU")
    sku_prefix: str = Field(default="CF", alias="SKU_PREFIX")
    
    # Pagination
    default_page_size: int = Field(default=20, alias="DEFAULT_PAGE_SIZE")
    max_page_size: int = Field(default=100, alias="MAX_PAGE_SIZE")
    
    # Rate Limiting
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_requests: int = Field(default=100, alias="RATE_LIMIT_REQUESTS")
    rate_limit_window: int = Field(default=60, alias="RATE_LIMIT_WINDOW")
    
    # Security
    secure_cookies: bool = Field(default=False, alias="SECURE_COOKIES")
    https_only: bool = Field(default=False, alias="HTTPS_ONLY")
    session_timeout: int = Field(default=3600, alias="SESSION_TIMEOUT")
    
    # Development Settings
    mock_data_enabled: bool = Field(default=True, alias="MOCK_DATA_ENABLED")
    seed_database: bool = Field(default=True, alias="SEED_DATABASE")
    auto_migrate: bool = Field(default=True, alias="AUTO_MIGRATE")
    
    @field_validator('cors_origins', mode='before')
    @classmethod
    def parse_cors_origins(cls, v, info):
        # Check if API_CORS_ORIGINS is set (from environment)
        api_cors = info.data.get('API_CORS_ORIGINS')
        if api_cors:
            if isinstance(api_cors, str):
                # Handle wildcard for all origins
                if api_cors.strip() == "*":
                    return ["*"]
                try:
                    return json.loads(api_cors)
                except:
                    return [origin.strip() for origin in api_cors.split(',')]
            return api_cors
        
        # Otherwise, use the provided cors_origins value
        if isinstance(v, str):
            # Handle wildcard for all origins
            if v.strip() == "*":
                return ["*"]
            try:
                return json.loads(v)
            except:
                return [origin.strip() for origin in v.split(',')]
        return v
    
    @field_validator('allowed_extensions', mode='before')
    @classmethod
    def parse_allowed_extensions(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except:
                return v.split(',')
        return v
    
    @property
    def DATABASE_URL(self) -> str:
        if self.database_url.startswith("postgresql://") or self.database_url.startswith("postgresql+psycopg2://"):
            return self.database_url
        return (
            f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )
    
    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"
    
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"
    
    @property
    def database_echo(self) -> bool:
        return self.debug and self.is_development

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"

# Global settings instance
settings = Settings()

# OpenFGA specific configuration
class OpenFGAConfig:
    def __init__(self):
        self.api_url = settings.openfga_api_url
        self.store_id = settings.openfga_store_id
        self.model_id = settings.openfga_model_id
        self.enabled = settings.openfga_enabled
    
    @property
    def is_configured(self) -> bool:
        return bool(self.api_url and self.store_id)
    
    def validate_configuration(self):
        if self.enabled and not self.is_configured:
            raise ValueError(
                "OpenFGA is enabled but not properly configured. "
                "Please set OPENFGA_API_URL and OPENFGA_STORE_ID in environment variables."
            )

openfga_config = OpenFGAConfig()