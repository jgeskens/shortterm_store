from django.urls import path

from . import views


urlpatterns = [
    path('<item_guid>/generate-upload-params/', views.generate_upload_params),
    path('<item_guid>/upload/', views.upload),
    path('<item_guid>/upload/<upload_guid>/download/', views.upload_download, name='upload_download'),
    path('<item_guid>/upload/<upload_guid>/delete/', views.upload_delete),
    path('<item_shortcut>/', views.item_detail),
    path('', views.item_detail)
]
