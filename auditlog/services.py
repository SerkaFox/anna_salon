from .models import AuditEvent


def log_event(*, actor=None, section, action, message, instance=None, metadata=None):
    AuditEvent.objects.create(
        actor=actor,
        section=section,
        action=action,
        target_model=instance._meta.label_lower if instance is not None else "",
        target_id=str(instance.pk) if instance is not None and instance.pk is not None else "",
        target_repr=str(instance) if instance is not None else "",
        message=message,
        metadata=metadata or {},
    )
