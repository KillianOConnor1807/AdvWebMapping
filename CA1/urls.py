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