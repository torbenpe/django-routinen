from django.urls import path

from . import views

app_name = "routinen"
urlpatterns = [
    path("", views.tile, name="tile"),
    path("save/", views.save, name="save"),
    path("<slug:routine_id>/done/", views.done, name="done"),
    path("<slug:routine_id>/delete/", views.delete, name="delete"),
]
