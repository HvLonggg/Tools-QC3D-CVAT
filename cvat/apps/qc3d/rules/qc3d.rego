package qc3d

import rego.v1

import data.utils


default allow := false

allow if {
    utils.is_admin
}

# Job visibility is enforced by the CVAT job filters inside the views,
# and QC roles are checked there for run, decision, fix and guideline changes.
# view:quality: everyone sees their own work; only lead / QA see other people (checked in the view).
# act:workflow: the workflow step itself checks the role (and the job's annotator for submit).
allow if {
    input.scope in {
        "view:summary", "view:report", "run:qc", "decide:issue", "fix:issue",
        "view:guideline", "manage:guideline", "view:quality", "view:workflow", "act:workflow",
    }
    utils.has_perm(utils.WORKER)
}
