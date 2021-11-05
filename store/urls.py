from django.urls import path

from . import views


urlpatterns = [
    path('<item_shortcut>/', views.item_detail),
    path('', views.item_detail)
]
