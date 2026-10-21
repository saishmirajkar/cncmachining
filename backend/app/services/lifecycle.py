"""Job/program status lifecycle and release gates.

Generation success never implies production readiness. A program revision reaches
``ready_for_controlled_export`` only when every gate in ``release_gates`` passes, including an explicit
engineering review by a user with the engineer role (or higher) who did not author the revision.
"""

from __future__ import annotations

from app.models.base import VERIFICATION_RANK, JobStatus, VerificationStatus

ALLOWED = {
    JobStatus.ready_for_cam: {JobStatus.cam_processing, JobStatus.cancelled},
    JobStatus.cam_processing: {JobStatus.toolpaths_generated, JobStatus.cam_failed, JobStatus.cancelled},
    JobStatus.toolpaths_generated: {JobStatus.postprocessing},
    JobStatus.postprocessing: {JobStatus.validation_required, JobStatus.cam_failed},
    JobStatus.validation_required: {JobStatus.validation_failed, JobStatus.engineering_review_required},
    JobStatus.validation_failed: {JobStatus.validation_required},
    JobStatus.engineering_review_required: {JobStatus.ready_for_controlled_export, JobStatus.validation_failed,
                                            JobStatus.validation_required},
    JobStatus.ready_for_controlled_export: {JobStatus.validation_required},
    JobStatus.cam_failed: {JobStatus.ready_for_cam},
    JobStatus.cancelled: set(),
}


class TransitionError(ValueError):
    pass


def transition(current: str, new: JobStatus) -> str:
    cur = JobStatus(current)
    if new not in ALLOWED.get(cur, set()):
        raise TransitionError(f"Invalid status transition {cur.value} -> {new.value}")
    return new.value


REQUIRED_ACKNOWLEDGEMENTS = [
    "software_checks_only",  # I understand checks are software-level and do not prove the machine will run it
    "setup_verified",  # stock, datum/work offset and workholding match the physical setup
    "tool_data_verified",  # tool numbers, lengths/offsets and holders match the machine
    "first_article_prove_out",  # the first run will be a controlled prove-out (single block, reduced rapids, ...)
]


def release_gates(*, report: dict, postprocessor_status: str, machine_config_status: str,
                  controller_version: str, review: dict | None, revision_author_id: str | None) -> list[dict]:
    gates = []

    def gate(code: str, ok: bool, message: str):
        gates.append({"code": code, "passed": bool(ok), "message": message})

    gate("VALIDATION_NO_ERRORS", report["summary"]["errors"] == 0,
         f"Validation report has {report['summary']['errors']} error(s)")
    gate("POSTPROCESSOR_TESTED",
         VERIFICATION_RANK[VerificationStatus(postprocessor_status)] >= VERIFICATION_RANK[VerificationStatus.tested],
         f"Postprocessor status is '{postprocessor_status}' (needs tested or machine_verified)")
    gate("MACHINE_CONFIG_NOT_DEPRECATED", machine_config_status != VerificationStatus.deprecated.value,
         f"Machine configuration status is '{machine_config_status}'")
    gate("CONTROLLER_VERSION_RECORDED", "unspecified" not in controller_version.lower(),
         f"Controller version: '{controller_version}'")
    if review is None:
        gate("ENGINEERING_REVIEW", False, "No engineering review approval recorded")
    else:
        missing = [a for a in REQUIRED_ACKNOWLEDGEMENTS if a not in review.get("acknowledgements", [])]
        gate("ENGINEERING_REVIEW", review["decision"] == "approved" and not missing,
             "Engineering review approved" if not missing else f"Missing acknowledgements: {missing}")
        gate("FOUR_EYES", revision_author_id is None or str(review["reviewer_id"]) != str(revision_author_id),
             "Reviewer differs from the author of the revision")
    return gates
