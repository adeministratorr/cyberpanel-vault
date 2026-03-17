from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from . import services


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(request: HttpRequest, *args, **kwargs):
        user = getattr(request, "user", None)
        is_admin = bool(
            user
            and getattr(user, "is_authenticated", False)
            and (getattr(user, "is_superuser", False) or getattr(user, "is_staff", False))
        )
        if is_admin:
            return view_func(request, *args, **kwargs)

        expects_json = request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", "")
        if expects_json:
            return JsonResponse({"error": "Bu uç noktayı kullanmak için yönetici oturumu gerekir."}, status=403)

        return redirect_to_login(request.get_full_path(), getattr(settings, "LOGIN_URL", "/login/"))

    return wrapped


@admin_required
@require_GET
def index(request: HttpRequest) -> HttpResponse:
    return render(request, "serverBackupManager/index.html", services.dashboard_context())


@admin_required
@require_POST
def run_backup(request: HttpRequest) -> HttpResponse:
    mode = request.POST.get("mode", "auto").strip() or "auto"

    try:
        services.start_backup_job(mode)
        messages.success(request, f"Yedekleme işi başlatıldı. Mod: {mode}")
    except services.ServiceError as exc:
        messages.error(request, str(exc))

    return redirect("serverBackupManager:index")


@admin_required
@require_POST
def run_restore(request: HttpRequest) -> HttpResponse:
    target_file = request.POST.get("target_file", "").strip()
    confirm_host = request.POST.get("confirm_host", "").strip()
    skip_db = request.POST.get("skip_db") == "on"
    skip_files = request.POST.get("skip_files") == "on"
    skip_configs = request.POST.get("skip_configs") == "on"
    skip_services = request.POST.get("skip_services") == "on"

    if not target_file:
        messages.error(request, "Geri yükleme için bir yedek seçin.")
        return redirect("serverBackupManager:index")

    try:
        services.start_restore_job(
            target_file=target_file,
            confirm_host=confirm_host,
            skip_db=skip_db,
            skip_files=skip_files,
            skip_configs=skip_configs,
            skip_services=skip_services,
        )
        messages.success(request, f"Geri yükleme işi başlatıldı: {target_file}")
    except services.ServiceError as exc:
        messages.error(request, str(exc))

    return redirect("serverBackupManager:index")


@admin_required
@require_GET
def jobs_api(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"jobs": services.list_jobs()})


@admin_required
@require_GET
def job_detail_api(request: HttpRequest, job_id: str) -> JsonResponse:
    try:
        job = services.get_job(job_id)
    except services.ServiceError as exc:
        return JsonResponse({"error": str(exc)}, status=404)
    return JsonResponse(job)


@admin_required
@require_GET
def job_log_api(request: HttpRequest, job_id: str) -> JsonResponse:
    try:
        job = services.get_job(job_id)
    except services.ServiceError as exc:
        return JsonResponse({"error": str(exc)}, status=404)
    return JsonResponse({"job": job, "log": services.read_job_log(job_id)})


@admin_required
@require_GET
def backups_api(request: HttpRequest) -> JsonResponse:
    try:
        backups = services.list_remote_backups()
    except services.ServiceError as exc:
        return JsonResponse({"error": str(exc)}, status=500)
    return JsonResponse({"backups": backups})
