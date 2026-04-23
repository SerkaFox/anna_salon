from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render

from accounts.permissions import admin_required

from .forms import ServiceForm
from .models import Service


@login_required
@admin_required
def service_list(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()

    services = Service.objects.prefetch_related("allowed_zones").all()

    if query:
        services = services.filter(
            Q(name__icontains=query) |
            Q(description__icontains=query)
        )

    if status == "active":
        services = services.filter(is_active=True)
    elif status == "inactive":
        services = services.filter(is_active=False)

    context = {
        "services": services,
        "query": query,
        "status": status,
        "services_count": services.count(),
    }
    return render(request, "services_app/service_list.html", context)


@login_required
@admin_required
def service_create(request):
    if request.method == "POST":
        form = ServiceForm(request.POST)
        if form.is_valid():
            service = form.save()
            messages.success(request, f"Servicio creado: {service.name}")
            return redirect("services_app:list")
    else:
        form = ServiceForm()

    context = {
        "form": form,
        "is_edit": False,
    }
    return render(request, "services_app/service_form.html", context)


@login_required
@admin_required
def service_update(request, pk):
    service = get_object_or_404(Service, pk=pk)

    if request.method == "POST":
        form = ServiceForm(request.POST, instance=service)
        if form.is_valid():
            service = form.save()
            messages.success(request, f"Servicio actualizado: {service.name}")
            return redirect("services_app:list")
    else:
        form = ServiceForm(instance=service)

    context = {
        "form": form,
        "service": service,
        "is_edit": True,
    }
    return render(request, "services_app/service_form.html", context)


@login_required
@admin_required
def service_delete(request, pk):
    service = get_object_or_404(Service, pk=pk)

    if request.method == "POST":
        service_name = service.name
        try:
            service.delete()
            messages.success(request, f"Servicio eliminado: {service_name}")
        except ProtectedError:
            messages.error(
                request,
                "No se puede eliminar este servicio porque tiene reservas u otros datos relacionados."
            )
        return redirect("services_app:list")

    return render(request, "services_app/service_confirm_delete.html", {"service": service})
