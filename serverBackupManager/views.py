from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
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
    timeout_minutes = request.POST.get("timeout_minutes", "").strip()
    components = request.POST.getlist("backup_components")

    try:
        timeout_value = services.validate_backup_timeout_minutes(timeout_minutes)
        validated_components = services.validate_backup_components(
            components,
            default=services.load_ui_settings()["backup_default_components"],
        )
        services.start_backup_job(mode, timeout_value, validated_components)
        timeout_label = "limitsiz" if timeout_value == 0 else f"{timeout_value} dakika"
        components_label = services.summarize_backup_components(validated_components, compact=True)
        messages.success(
            request,
            f"Yedekleme işi başlatıldı. Mod: {mode} | Kapsam: {components_label} | Süre sınırı: {timeout_label}",
        )
    except services.ServiceError as exc:
        messages.error(request, str(exc))

    return redirect("serverBackupManager:index")


@admin_required
@require_POST
def save_schedule(request: HttpRequest) -> HttpResponse:
    schedule_enabled = request.POST.get("schedule_enabled") == "on"
    schedule_hour = request.POST.get("schedule_hour", "").strip()
    schedule_minute = request.POST.get("schedule_minute", "").strip()
    schedule_mode = request.POST.get("schedule_mode", "auto").strip()
    schedule_components = request.POST.getlist("schedule_components")
    schedule_weekdays = request.POST.getlist("schedule_weekdays")

    try:
        settings = services.update_backup_schedule(
            enabled=schedule_enabled,
            hour=schedule_hour,
            minute=schedule_minute,
            mode=schedule_mode,
            components=schedule_components,
            weekdays=schedule_weekdays,
        )
        messages.success(
            request,
            f"Zamanlama kaydedildi: {services.summarize_backup_schedule(settings)}",
        )
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
def dashboard_api(request: HttpRequest) -> JsonResponse:
    return JsonResponse(services.dashboard_state())


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
def job_log_download(request: HttpRequest, job_id: str) -> HttpResponse:
    try:
        services.get_job(job_id)
        log_path = services.get_job_log_path(job_id)
    except services.ServiceError as exc:
        return JsonResponse({"error": str(exc)}, status=404)

    response = FileResponse(log_path.open("rb"), content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{job_id}.log"'
    return response


@admin_required
@require_GET
def backups_api(request: HttpRequest) -> JsonResponse:
    try:
        backups = services.list_remote_backups()
    except services.ServiceError as exc:
        return JsonResponse({"error": str(exc)}, status=500)
    return JsonResponse({"backups": backups})
