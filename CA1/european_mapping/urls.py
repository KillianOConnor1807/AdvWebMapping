# european_mapping/urls.py
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api-auth/', include('rest_framework.urls')),  # login/logout for the browsable API
    path('accounts/', include('django.contrib.auth.urls')),  # site login/logout
    path('api/', include('api.urls')),
    path('', include('dashboard.urls')),
]

# Error pages: JSON for /api/ paths, a friendly HTML page elsewhere (see european_mapping/views.py)
handler400 = 'european_mapping.views.bad_request'
handler403 = 'european_mapping.views.permission_denied'
handler404 = 'european_mapping.views.not_found'
handler500 = 'european_mapping.views.server_error'
