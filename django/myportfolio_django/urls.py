"""
URL configuration for myportfolio_django project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from config.views import home
from scraper.views import (
    PCPartViewSet,
    ConfigurationViewSet,
    ScraperStatusViewSet,
    GenerateConfigAPIView,
    ScraperStatusCompatAPIView,
    MarketPriceRangeAPIView,
    PartPriceRangesAPIView,
    GpuPerformanceLatestAPIView,
    GpuPerformanceCompareAPIView,
    CpuSelectionMaterialLatestAPIView,
    CpuSelectionMaterialCompareAPIView,
    StorageInventoryAPIView,
)

# Create router and register viewsets
router = DefaultRouter()
router.register(r'parts', PCPartViewSet, basename='part')
router.register(r'configurations', ConfigurationViewSet, basename='configuration')
router.register(r'scraper-status', ScraperStatusViewSet, basename='scraper-status')

urlpatterns = [
    path('', home, name='home'),
    path('admin/', admin.site.urls),
    path('api/generate-config/', GenerateConfigAPIView.as_view()),
    path('api/scraper-status/summary/', ScraperStatusCompatAPIView.as_view()),
    path('api/market-price-range/', MarketPriceRangeAPIView.as_view()),
    path('api/part-price-ranges/', PartPriceRangesAPIView.as_view()),
    path('api/gpu-performance/latest/', GpuPerformanceLatestAPIView.as_view()),
    path('api/gpu-performance/compare/', GpuPerformanceCompareAPIView.as_view()),
    path('api/cpu-selection-material/latest/', CpuSelectionMaterialLatestAPIView.as_view()),
    path('api/cpu-selection-material/compare/', CpuSelectionMaterialCompareAPIView.as_view()),
    path('api/storage-inventory/', StorageInventoryAPIView.as_view()),
    path('api/', include(router.urls)),
    path('api-auth/', include('rest_framework.urls')),
    path('generate-config', GenerateConfigAPIView.as_view()),
    path('scraper/status', ScraperStatusCompatAPIView.as_view()),
]

