from django.conf.urls import include, re_path

from clist import views

app_name = 'clist'

urlpatterns = [
    re_path(r'^$', views.main, name='main'),
    re_path(r'^send-event-notification/$', views.send_event_notification, name='send_event_notification'),
    re_path(r'^resources/$', views.resources, name='resources'),
    re_path(r'^resources/top/$', views.resources_top, name='resources_top'),
    re_path(r'^resources/dumpdata/$', views.resources_dumpdata, name='resources_dumpdata'),
    re_path(r'^resource/(.*)/$', views.resource, name='resource'),
    re_path(r'^get/events/$', views.get_events),
    re_path(r'^problems/$', views.problems, name='problems'),
    re_path(r'^api/', include('clist.api.urls', namespace='api')),
]
