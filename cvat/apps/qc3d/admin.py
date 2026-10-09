# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from django.contrib import admin

from .models import QcMembership


@admin.register(QcMembership)
class QcMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "created_date")
    list_filter = ("role",)
    search_fields = ("user__username",)
    autocomplete_fields = ("user",)
