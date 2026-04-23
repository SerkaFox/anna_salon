from functools import wraps

from django.core.exceptions import PermissionDenied

from clients.models import Client


def is_admin_user(user):
    return user.is_authenticated and user.role in {user.ROLE_OWNER, user.ROLE_ADMIN}


def is_employee_user(user):
    return user.is_authenticated and user.role == user.ROLE_EMPLOYEE


def get_employee_profile(user):
    return getattr(user, "employee_profile", None)


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not is_admin_user(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


def employee_profile_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not get_employee_profile(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


def scope_employees_queryset(queryset, user):
    if is_admin_user(user):
        return queryset

    employee = get_employee_profile(user)
    if not employee:
        return queryset.none()
    return queryset.filter(pk=employee.pk)


def scope_bookings_queryset(queryset, user):
    if is_admin_user(user):
        return queryset

    employee = get_employee_profile(user)
    if not employee:
        return queryset.none()
    return queryset.filter(employee=employee)


def scope_clients_queryset(queryset, user):
    if is_admin_user(user):
        return queryset

    employee = get_employee_profile(user)
    if not employee:
        return queryset.none()
    return queryset.filter(bookings__employee=employee).distinct()


def can_access_employee(user, employee):
    if is_admin_user(user):
        return True
    current_employee = get_employee_profile(user)
    return bool(current_employee and employee and current_employee.pk == employee.pk)


def can_access_booking(user, booking):
    return can_access_employee(user, booking.employee)


def can_access_client(user, client):
    if is_admin_user(user):
        return True

    employee = get_employee_profile(user)
    if not employee:
        return False

    return Client.objects.filter(pk=client.pk, bookings__employee=employee).exists()
