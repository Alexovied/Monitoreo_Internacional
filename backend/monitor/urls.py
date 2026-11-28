from django.urls import path

from . import views

urlpatterns = [
    path("incidents/", views.IncidentListView.as_view()),
    path("incidents/simulate/", views.SimulateView.as_view()),
    path("incidents/<int:pk>/", views.IncidentDetailView.as_view()),
    path("incidents/<int:pk>/photo/", views.IncidentPhotoView.as_view()),
    path("sightings/check/", views.SightingCheckView.as_view()),
    path("sightings/", views.SightingView.as_view()),
]
