# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from rest_framework import routers

from .views import Qc3dViewSet

router = routers.DefaultRouter(trailing_slash=False)
router.register("qc3d", Qc3dViewSet, basename="qc3d")

urlpatterns = router.urls
