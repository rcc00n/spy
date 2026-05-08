from datetime import timedelta
from pathlib import Path

import dj_database_url
import environ
from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key


BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
)
environ.Env.read_env(BASE_DIR / ".env")


def env_float(name: str, default: float) -> float:
    return float(env.str(name, default=str(default)))


def env_path(name: str, default: str) -> Path:
    path = Path(env.str(name, default=default))
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


DEBUG = env.bool("DEBUG", default=False)

SECRET_KEY = env.str("SECRET_KEY", default="")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = get_random_secret_key()
    else:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG=False.")

ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "0.0.0.0", "testserver"],
)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.monitoring",
    "apps.dashboard",
    "apps.research",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASE_URL = env.str("DATABASE_URL", default="")
if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=env.int("DB_CONN_MAX_AGE", default=60),
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env.str("TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard:index"
LOGOUT_REDIRECT_URL = "login"

SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=not DEBUG)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=not DEBUG)
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

REDIS_URL = env.str("REDIS_URL", default="redis://redis:6379/0")
CELERY_BROKER_URL = env.str("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env.str("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = env.int("CELERY_TASK_TIME_LIMIT", default=1800)
CELERY_WORKER_PREFETCH_MULTIPLIER = env.int(
    "CELERY_WORKER_PREFETCH_MULTIPLIER",
    default=1,
)
CELERY_TASK_ANNOTATIONS = {
    "apps.research.tasks.run_research_job": {
        "time_limit": env.int("RESEARCH_TASK_TIME_LIMIT_SECONDS", default=21600),
        "soft_time_limit": env.int(
            "RESEARCH_TASK_SOFT_TIME_LIMIT_SECONDS",
            default=21000,
        ),
    },
}
CELERY_BEAT_SCHEDULE = {
    "resume-facebook-collection": {
        "task": "apps.research.facebook_tasks.dispatch_facebook_scans",
        "schedule": timedelta(seconds=30),
    },
    "check-monitored-accounts": {
        "task": "apps.monitoring.tasks.check_active_accounts",
        "schedule": timedelta(
            seconds=env.int("MONITORING_BEAT_SECONDS", default=300)
        ),
    }
}

RESEARCH_ENGINE_URL = env.str("RESEARCH_ENGINE_URL", default="")
RESEARCH_ENGINE_TOKEN = env.str("RESEARCH_ENGINE_TOKEN", default="")
RESEARCH_ENGINE_TIMEOUT_SECONDS = env_float("RESEARCH_ENGINE_TIMEOUT_SECONDS", 30.0)
RESEARCH_ENGINE_POLL_SECONDS = env_float("RESEARCH_ENGINE_POLL_SECONDS", 5.0)
RESEARCH_ENGINE_MAX_POLLS = env.int("RESEARCH_ENGINE_MAX_POLLS", default=2160)
RESEARCH_PORTAL_PUBLIC_BASE_URL = env.str("RESEARCH_PORTAL_PUBLIC_BASE_URL", default="")
RESEARCH_SOCIAL_TOOL_BASE_URL = env.str("RESEARCH_SOCIAL_TOOL_BASE_URL", default="")
RUNPOD_API_KEY = env.str("RUNPOD_API_KEY", default="")
RUNPOD_POD_ID = env.str("RUNPOD_POD_ID", default="")
RUNPOD_ENGINE_PORT = env.int("RUNPOD_ENGINE_PORT", default=8000)
RUNPOD_AUTOSTART_ENABLED = env.bool("RUNPOD_AUTOSTART_ENABLED", default=False)
RUNPOD_AUTOSTOP_AFTER_JOB = env.bool("RUNPOD_AUTOSTOP_AFTER_JOB", default=False)
RUNPOD_API_TIMEOUT_SECONDS = env_float("RUNPOD_API_TIMEOUT_SECONDS", 30.0)
RUNPOD_START_TIMEOUT_SECONDS = env_float("RUNPOD_START_TIMEOUT_SECONDS", 900.0)
RUNPOD_HEALTH_POLL_SECONDS = env_float("RUNPOD_HEALTH_POLL_SECONDS", 10.0)
RUNPOD_HEALTH_TIMEOUT_SECONDS = env_float("RUNPOD_HEALTH_TIMEOUT_SECONDS", 10.0)
RUNPOD_ENGINE_HEALTH_PATH = env.str("RUNPOD_ENGINE_HEALTH_PATH", default="/health")

TELEGRAM_BOT_TOKEN = env.str("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_SEND_CHECK_RUN_SUMMARY = env.bool(
    "TELEGRAM_SEND_CHECK_RUN_SUMMARY",
    default=True,
)

PLAYWRIGHT_TIMEOUT_MS = env.int("PLAYWRIGHT_TIMEOUT_MS", default=30000)
MONITORING_MIN_DELAY_SECONDS = env_float("MONITORING_MIN_DELAY_SECONDS", 2.0)
MONITORING_MAX_DELAY_SECONDS = env_float("MONITORING_MAX_DELAY_SECONDS", 8.0)
MONITORING_TEXT_MAX_LENGTH = env.int("MONITORING_TEXT_MAX_LENGTH", default=20000)
MONITORING_SNAPSHOT_MAX_LENGTH = env.int(
    "MONITORING_SNAPSHOT_MAX_LENGTH",
    default=10000,
)
MONITORING_USER_AGENT = env.str(
    "MONITORING_USER_AGENT",
    default=(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
)

FACEBOOK_AUTH_ENABLED = env.bool("FACEBOOK_AUTH_ENABLED", default=False)
FACEBOOK_AUTH_STORAGE_STATE_PATH = env_path(
    "FACEBOOK_AUTH_STORAGE_STATE_PATH",
    "runtime/facebook_storage_state.json",
)
FACEBOOK_AUTH_CHECK_URL = env.str(
    "FACEBOOK_AUTH_CHECK_URL",
    default="https://www.facebook.com/me",
)
FACEBOOK_LOGIN_TIMEOUT_SECONDS = env.int("FACEBOOK_LOGIN_TIMEOUT_SECONDS", default=180)
FACEBOOK_CREDENTIAL_ENCRYPTION_KEY = env.str(
    "FACEBOOK_CREDENTIAL_ENCRYPTION_KEY",
    default="",
)
FACEBOOK_LOGIN_EMAIL = env.str("FACEBOOK_LOGIN_EMAIL", default="")
FACEBOOK_LOGIN_PASSWORD = env.str("FACEBOOK_LOGIN_PASSWORD", default="")
FACEBOOK_SESSION_MANAGER_URL = env.str("FACEBOOK_SESSION_MANAGER_URL", default="")
FACEBOOK_SESSION_MANAGER_POLL_SECONDS = env_float(
    "FACEBOOK_SESSION_MANAGER_POLL_SECONDS",
    5.0,
)
FACEBOOK_SESSION_REQUEST_TTL_SECONDS = env.int(
    "FACEBOOK_SESSION_REQUEST_TTL_SECONDS",
    default=900,
)
FACEBOOK_SESSION_BROWSER_PROFILE_DIR = env_path(
    "FACEBOOK_SESSION_BROWSER_PROFILE_DIR",
    "runtime/facebook_browser_profile",
)
FACEBOOK_SESSION_PREFILL_CREDENTIALS = env.bool(
    "FACEBOOK_SESSION_PREFILL_CREDENTIALS",
    default=False,
)
FACEBOOK_CHECK_USE_PERSISTENT_PROFILE = env.bool(
    "FACEBOOK_CHECK_USE_PERSISTENT_PROFILE",
    default=False,
)
FACEBOOK_SESSION_RUN_CHECKS_AFTER_SUCCESS = env.bool(
    "FACEBOOK_SESSION_RUN_CHECKS_AFTER_SUCCESS",
    default=True,
)
FACEBOOK_SESSION_VALIDATE_MONITORED_ACCOUNTS = env.bool(
    "FACEBOOK_SESSION_VALIDATE_MONITORED_ACCOUNTS",
    default=True,
)
FACEBOOK_SESSION_VALIDATE_POST_LIMIT = env.int(
    "FACEBOOK_SESSION_VALIDATE_POST_LIMIT",
    default=2,
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        }
    },
    "root": {
        "handlers": ["console"],
        "level": env.str("LOG_LEVEL", default="INFO"),
    },
    "loggers": {
        "httpx": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "httpcore": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
