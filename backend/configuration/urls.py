from django.urls import path

from . import views

urlpatterns = [
    path("config/", views.ConfigView.as_view()),
    path("config/openair/", views.OpenAirView.as_view()),
    path("config/telegram-test/", views.TelegramTestView.as_view()),
    path("config/telegram-chats/", views.TelegramChatsView.as_view()),
    path("config/maintenance/", views.MaintenanceView.as_view()),
    path("system/", views.SystemInfoView.as_view()),
    path("system/version/", views.VersionView.as_view()),
]
