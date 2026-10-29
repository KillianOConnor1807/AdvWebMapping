import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# --- Environment-based configuration (values come from .env via compose) ---
DEBUG = os.environ.get("DEBUG", "False") == "True"   # off unless explicitly turned on (dev compose file)
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is off")
    SECRET_KEY = "django-insecure-development-only-key"  # local development only
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")  # e.g. https://gyms.example.com behind a proxy
USE_HTTPS = os.environ.get("USE_HTTPS", "False") == "True"

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.gis',
    'django.contrib.humanize',
    'rest_framework',
    'rest_framework_gis',
    'django_filters',
    'corsheaders',
    'gyms',
    'transit',
    'districts',
    'api',
    'dashboard',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # serves collected static files under gunicorn
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'european_mapping.middleware.PermissionsPolicyMiddleware',
]

ROOT_URLCONF = 'european_mapping.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'european_mapping.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.contrib.gis.db.backends.postgis',
        'NAME': os.environ['DATABASE_NAME'],
        'USER': os.environ['DATABASE_USER'],
        'PASSWORD': os.environ['DATABASE_PASSWORD'],
        'HOST': os.environ['DATABASE_HOST'],
        'PORT': os.environ['DATABASE_PORT'],
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-ie'
TIME_ZONE = 'Europe/Dublin'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static'] if (BASE_DIR / 'static').is_dir() else []
# In Docker this points outside the code folder (see Dockerfile), so the dev bind mount can't hide it
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", BASE_DIR / "staticfiles"))
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Compressed, cache-busting file names (e.g. base.8f3a1c.css) served by WhiteNoise
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Shared cache in PostgreSQL, so API rate limits count across all gunicorn workers
# (the table is created by `createcachetable` in entrypoint.sh)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'django_cache',
    }
}

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard_index'
LOGOUT_REDIRECT_URL = 'dashboard_index'

# --- Security headers and cookies (safe over plain HTTP, so always on) ---
SECURE_CONTENT_TYPE_NOSNIFF = True                   # X-Content-Type-Options: nosniff
X_FRAME_OPTIONS = "DENY"                             # can't be embedded in other sites (clickjacking)
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True                       # JavaScript can't read the login cookie
SESSION_COOKIE_SAMESITE = "Lax"                      # not sent on cross-site requests
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 8                     # log out after 8 hours (Django's default is 2 weeks)

# --- HTTPS-only settings: turn on with USE_HTTPS=True when served over TLS (e.g. behind Nginx) ---
if USE_HTTPS:
    SESSION_COOKIE_SECURE = True                     # cookies only travel over HTTPS
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True                       # http:// -> https://
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")   # trust the proxy's HTTPS flag
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30          # browsers remember "HTTPS only" for 30 days
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

REST_FRAMEWORK = {
    # Anyone can read; only logged-in users can create/update/delete
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticatedOrReadOnly',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 100,
    # Rate limiting: too many requests get 429 Too Many Requests
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': os.environ.get('API_RATE_ANON', '100/minute'),
        'user': os.environ.get('API_RATE_USER', '300/minute'),
    },
}

# CORS is only opened up in development
CORS_ALLOW_ALL_ORIGINS = DEBUG