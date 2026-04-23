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

    @property
    def is_owner_role(self):
        return self.role == self.ROLE_OWNER

    @property
    def is_admin_role(self):
        return self.role == self.ROLE_ADMIN

    @property
    def is_employee_role(self):
        return self.role == self.ROLE_EMPLOYEE

    @property
    def can_manage_staff(self):
        return self.role in {self.ROLE_OWNER, self.ROLE_ADMIN}

    def __str__(self):
        full_name = self.get_full_name().strip()
        return full_name or self.username
