from django.urls import path
from . import pwa, views

urlpatterns = [
    path('', views.index, name='dashboard_index'),
    path('analytics/', views.analytics, name='dashboard_analytics'),
    # PWA: served from the site root so the service worker can control every page
    path('manifest.webmanifest', pwa.manifest, name='pwa_manifest'),
    path('sw.js', pwa.service_worker, name='pwa_service_worker'),
    path('offline/', pwa.offline, name='pwa_offline'),
]