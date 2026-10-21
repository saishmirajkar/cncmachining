"""Software-level postprocessor verification against reviewed expected-output fixtures.

For each bundled postprocessor the reference IR (``services/post/fixtures/reference_ir.json``) is rendered with a
fixed context and compared byte-for-byte with ``services/post/fixtures/expected/<key>@<version><ext>``.

* A ``documented`` version whose output matches is promoted to ``tested`` (level ``tested``, method
  ``fixture_tests``). This is a software-level result: it shows the engine reproduces the reviewed expected
  output; it is *not* a machine verification.
* ``draft`` versions are never promoted by this tool: their expected output has not been reviewed against
  controller documentation. A verification row records the result for traceability.

Regenerate fixtures after an intentional change (then review the diff!):
``python -m app.seed.verify_posts --regenerate``
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Postprocessor, PostprocessorVerification, PostprocessorVersion
from app.models.base import VERIFICATION_RANK, VerificationStatus
from app.services.cam.ir import ToolpathIR
from app.services.post.engine import PostContext, render
from app.services.post.registry import bundled, config_hash
from app.services.post.schema import PostConfig

FIX = Path(__file__).resolve().parents[1] / "services" / "post" / "fixtures"

FIXTURE_MACHINE_BLOCKS = {
    # Fixture-only machine blocks used to exercise placeholder expansion. NOT real machine data.
    "linuxcnc-mill-3axis": {},
    "fanuc-0i-mf-iso": {"coolant": {"flood": "M8", "off": "M9"}, "declared_mcodes": ["M8", "M9"]},
    "sinumerik-828d-basic": {"coolant": {"flood": "M8", "off": "M9"}, "declared_mcodes": ["M8", "M9"]},
}


def render_fixture(cfg: PostConfig) -> str:
    ir = ToolpathIR.model_validate(json.loads((FIX / "reference_ir.json").read_text()))
    ctx = PostContext(
        program_number=1234, program_name="REF FIXTURE",
        generated_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        machine_blocks=FIXTURE_MACHINE_BLOCKS.get(cfg.key, {}),
        tools=[{"number": 1, "name": "D10 FLAT", "diameter": 10}, {"number": 5, "name": "D6.6 DRILL", "diameter": 6.6}],
    )
    return render(ir, cfg, ctx).text


def expected_path(cfg: PostConfig) -> Path:
    return FIX / "expected" / f"{cfg.key}@{cfg.version}{cfg.file_extension}"


def verify_bundled_postprocessors(db: Session) -> list[str]:
    report = []
    for cfg in bundled().values():
        pp = db.scalars(select(Postprocessor).filter_by(key=cfg.key)).first()
        if pp is None:
            continue
        ver = db.scalars(select(PostprocessorVersion).filter_by(postprocessor_id=pp.id, version=cfg.version)).first()
        if ver is None or ver.config_sha256 != config_hash(cfg.model_dump(mode="json")):
            report.append(f"{cfg.key}@{cfg.version}: stored config differs from bundled config; skipped")
            continue
        exp = expected_path(cfg)
        actual = render_fixture(cfg)
        passed = exp.exists() and exp.read_text() == actual
        diff = "" if passed or not exp.exists() else "".join(difflib.unified_diff(
            exp.read_text().splitlines(True), actual.splitlines(True), "expected", "actual"))[:4000]
        already = db.scalars(select(PostprocessorVerification).filter_by(
            postprocessor_version_id=ver.id, method="fixture_tests", result="passed")).first()
        promote = passed and ver.status == VerificationStatus.documented.value
        if not already or not passed:
            db.add(PostprocessorVerification(
                postprocessor_version_id=ver.id, level="tested" if promote else ver.status,
                method="fixture_tests", result="passed" if passed else "failed",
                evidence={"fixture": exp.name, "config_sha256": ver.config_sha256, "diff": diff},
                reviewer_name="automated fixture suite",
                notes=("Software-level comparison with the reviewed expected output. Not a machine verification."
                       if ver.status != "draft" else
                       "Draft postprocessor: expected output has not been reviewed against controller "
                       "documentation, so the status is not promoted.")))
        if promote:
            ver.status = VerificationStatus.tested.value
        report.append(f"{cfg.key}@{cfg.version}: fixture {'PASSED' if passed else 'FAILED'}; status {ver.status}")
    return report


def main() -> None:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser()
    ap.add_argument("--regenerate", action="store_true")
    a = ap.parse_args()
    if a.regenerate:
        for cfg in bundled().values():
            p = expected_path(cfg)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(render_fixture(cfg))
            print("wrote", p)
        return
    from app.db.session import SessionLocal

    db = SessionLocal()
    for line in verify_bundled_postprocessors(db):
        print(line)
    db.commit()


__all__ = ["verify_bundled_postprocessors", "VERIFICATION_RANK"]

if __name__ == "__main__":  # pragma: no cover
    main()
