from django.urls import path
from .views import home, client_rankings_partial

app_name = 'dashboard'

urlpatterns = [
    path('', home, name='home'),
    path('api/client-rankings/', client_rankings_partial, name='client_rankings_partial'),
]
