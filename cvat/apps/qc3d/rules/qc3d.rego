package qc3d

import rego.v1

import data.utils


default allow := false

allow if {
    utils.is_admin
}

# Job visibility is enforced by the CVAT job filters inside the views,
# and QC roles (lead / reviewer / qa) are checked there for run and decision.
allow if {
    input.scope in {"view:summary", "view:report", "run:qc", "decide:issue", "fix:issue"}
    utils.has_perm(utils.WORKER)
}
