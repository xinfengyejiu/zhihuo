from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置,支持 .env 与环境变量覆盖。"""

    secret_key: str = "dev-insecure-secret-key-change-me"
    # 阅读器 JWT 独立密钥;未配置时用 secret_key + 固定后缀,与创作台 token 隔离
    reader_secret_key: str = ""
    database_url: str = "sqlite:///./xiaohuo.db"
    access_token_expire_minutes: int = 60 * 24 * 7
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:3001,http://127.0.0.1:3001,"
        "http://192.168.12.113:3000,http://192.168.12.113:3001"
    )
    # 局域网访问时放宽同源(localhost / 私网 IP :任意端口)
    cors_origin_regex: str = (
        r"https?://("
        r"localhost|127\.0\.0\.1|"
        r"192\.168\.\d{1,3}\.\d{1,3}|"
        r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}"
        r")(:\d+)?"
    )
    # 可选:安装 GitHub Skills / 私有库时提高配额
    github_token: str = ""
    # 文件夹型 Skills 落盘根目录(相对 backend 工作目录或绝对路径)
    skills_root: str = "./data/skills"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
