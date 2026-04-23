from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render

from accounts.permissions import admin_required

from .models import AuditEvent


@login_required
@admin_required
def event_list(request):
    query = (request.GET.get("q") or "").strip()
    section = (request.GET.get("section") or "").strip()
    action = (request.GET.get("action") or "").strip()

    events = AuditEvent.objects.select_related("actor")

    if query:
        events = events.filter(
            Q(message__icontains=query)
            | Q(target_repr__icontains=query)
            | Q(actor__username__icontains=query)
            | Q(actor__first_name__icontains=query)
            | Q(actor__last_name__icontains=query)
        )

    if section:
        events = events.filter(section=section)

    if action:
        events = events.filter(action=action)

    section_choices = AuditEvent.objects.order_by().values_list("section", flat=True).distinct()
    action_choices = AuditEvent.objects.order_by().values_list("action", flat=True).distinct()

    return render(
        request,
        "auditlog/event_list.html",
        {
            "active_section": "auditlog",
            "events": events[:250],
            "query": query,
            "section": section,
            "action": action,
            "section_choices": section_choices,
            "action_choices": action_choices,
        },
    )
