from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    ROLE_OWNER = 'owner'
    ROLE_ADMIN = 'admin'
    ROLE_EMPLOYEE = 'employee'

    ROLE_CHOICES = [
        (ROLE_OWNER, 'Owner'),
        (ROLE_ADMIN, 'Admin'),
        (ROLE_EMPLOYEE, 'Employee'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_EMPLOYEE)
    phone = models.CharField(max_length=30, blank=True)
    is_active_staff = models.BooleanField(default=True)

    def __str__(self):
        full_name = self.get_full_name().strip()
        return full_name or self.username