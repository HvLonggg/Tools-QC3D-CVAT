# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from django.contrib import admin

from .models import QcGuideline, QcMembership


@admin.register(QcMembership)
class QcMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "created_date")
    list_filter = ("role",)
    search_fields = ("user__username",)
    autocomplete_fields = ("user",)


@admin.register(QcGuideline)
class QcGuidelineAdmin(admin.ModelAdmin):
    list_display = ("name", "revision", "is_default", "updated_by", "updated_date")
    search_fields = ("name",)
    readonly_fields = ("revision", "created_by", "updated_by", "created_date", "updated_date")
