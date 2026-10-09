package qc3d

import rego.v1

import data.utils


default allow := false

allow if {
    utils.is_admin
}

# Data visibility is enforced by the task/job filters inside the view,
# so any authenticated CVAT user may open the QC 3D summary.
allow if {
    input.scope == "view:summary"
    utils.has_perm(utils.WORKER)
}
