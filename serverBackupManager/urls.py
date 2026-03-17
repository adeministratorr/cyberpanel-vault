from django.urls import path

from . import views


app_name = "serverBackupManager"

urlpatterns = [
    path("", views.index, name="index"),
    path("api/run-backup/", views.run_backup, name="run_backup"),
    path("api/run-restore/", views.run_restore, name="run_restore"),
    path("api/dashboard/", views.dashboard_api, name="dashboard_api"),
    path("api/jobs/", views.jobs_api, name="jobs_api"),
    path("api/jobs/<str:job_id>/", views.job_detail_api, name="job_detail_api"),
    path("api/jobs/<str:job_id>/log/", views.job_log_api, name="job_log_api"),
    path("api/jobs/<str:job_id>/log/download/", views.job_log_download, name="job_log_download"),
    path("api/backups/", views.backups_api, name="backups_api"),
]
