from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.permissions import admin_required

from .forms import DepositSettingsForm, ZoneForm
from .models import SalonSettings, Zone


@login_required
@admin_required
def deposit_settings(request):
    salon_settings = SalonSettings.load()
    if request.method == "POST":
        form = DepositSettingsForm(request.POST, instance=salon_settings)
        if form.is_valid():
            form.save()
            messages.success(request, "Ajustes de prepago guardados.")
            return redirect("salon:deposit_settings")
    else:
        form = DepositSettingsForm(instance=salon_settings)
    return render(
        request,
        "salon/deposit_settings.html",
        {"form": form, "settings": salon_settings, "active_section": "deposit_settings"},
    )


@login_required
@admin_required
def zone_list(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()

    zones = Zone.objects.all()

    if query:
        zones = zones.filter(
            Q(name__icontains=query) |
            Q(zone_type__icontains=query) |
            Q(notes__icontains=query)
        )

    if status == "active":
        zones = zones.filter(is_active=True)
    elif status == "inactive":
        zones = zones.filter(is_active=False)

    context = {
        "zones": zones,
        "query": query,
        "status": status,
        "zones_count": zones.count(),
    }
    return render(request, "salon/zone_list.html", context)


@login_required
@admin_required
def zone_create(request):
    if request.method == "POST":
        form = ZoneForm(request.POST)
        if form.is_valid():
            zone = form.save()
            messages.success(request, f"Zona creada: {zone.name}")
            return redirect("salon:list")
    else:
        form = ZoneForm()

    return render(
        request,
        "salon/zone_form.html",
        {
            "form": form,
            "is_edit": False,
        },
    )


@login_required
@admin_required
def zone_update(request, pk):
    zone = get_object_or_404(Zone, pk=pk)

    if request.method == "POST":
        form = ZoneForm(request.POST, instance=zone)
        if form.is_valid():
            zone = form.save()
            messages.success(request, f"Zona actualizada: {zone.name}")
            return redirect("salon:list")
    else:
        form = ZoneForm(instance=zone)

    return render(
        request,
        "salon/zone_form.html",
        {
            "form": form,
            "zone": zone,
            "is_edit": True,
        },
    )


@login_required
@admin_required
def zone_delete(request, pk):
    zone = get_object_or_404(Zone, pk=pk)

    if request.method == "POST":
        zone_name = zone.name
        zone.delete()
        messages.success(request, f"Zona eliminada: {zone_name}")
        return redirect("salon:list")

    return render(
        request,
        "salon/zone_confirm_delete.html",
        {"zone": zone},
    )
