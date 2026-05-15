from functools import wraps

from django.core.exceptions import PermissionDenied

from clients.models import Client


def is_admin_user(user):
    return user.is_authenticated and user.role in {user.ROLE_OWNER, user.ROLE_ADMIN}


def is_employee_user(user):
    return user.is_authenticated and user.role == user.ROLE_EMPLOYEE


def is_client_user(user):
    return user.is_authenticated and user.role == user.ROLE_CLIENT


def get_employee_profile(user):
    return getattr(user, "employee_profile", None)


def get_client_profile(user):
    return getattr(user, "client_profile", None)


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

    if is_client_user(user):
        return queryset

    employee = get_employee_profile(user)
    if not employee:
        return queryset.none()
    return queryset.filter(pk=employee.pk)


def scope_bookings_queryset(queryset, user):
    if is_admin_user(user):
        return queryset

    client = get_client_profile(user)
    if client:
        return queryset.filter(client=client)

    employee = get_employee_profile(user)
    if not employee:
        return queryset.none()
    return queryset.filter(employee=employee)


def scope_clients_queryset(queryset, user):
    if is_admin_user(user):
        return queryset

    client = get_client_profile(user)
    if client:
        return queryset.filter(pk=client.pk)

    employee = get_employee_profile(user)
    if not employee:
        return queryset.none()
    return queryset.filter(bookings__employee=employee).distinct()


def can_access_employee(user, employee):
    if is_admin_user(user):
        return True
    if is_client_user(user):
        return bool(employee)
    current_employee = get_employee_profile(user)
    return bool(current_employee and employee and current_employee.pk == employee.pk)


def can_access_booking(user, booking):
    client = get_client_profile(user)
    if client:
        return booking.client_id == client.pk
    return can_access_employee(user, booking.employee)


def can_access_client(user, client):
    if is_admin_user(user):
        return True

    current_client = get_client_profile(user)
    if current_client:
        return client and current_client.pk == client.pk

    employee = get_employee_profile(user)
    if not employee:
        return False

    return Client.objects.filter(pk=client.pk, bookings__employee=employee).exists()
