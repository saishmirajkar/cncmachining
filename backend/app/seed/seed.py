"""Idempotent catalogue seed: ``python -m app.seed.seed [--admin-email ... --admin-password ...]``."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import Base, SessionLocal, get_engine
from app.models import (
    ControllerManufacturer,
    ControllerModel,
    ControllerVersion,
    MachineConfiguration,
    MachineManufacturer,
    MachineModel,
    Material,
    MaterialGrade,
    Postprocessor,
    PostprocessorVersion,
    Tool,
    ToolAssembly,
    ToolLibrary,
    User,
)
from app.services.post.registry import bundled, config_hash

DATA = Path(__file__).parent / "data"


def _get_or_create(db: Session, model, where: dict, **values):
    obj = db.scalars(select(model).filter_by(**where)).first()
    if obj is None:
        obj = model(**where, **values)
        db.add(obj)
        db.flush()
    return obj


def seed_catalogue(db: Session) -> None:
    cat = yaml.safe_load((DATA / "catalogue.yaml").read_text())
    for m in cat["machine_manufacturers"]:
        _get_or_create(db, MachineManufacturer, {"name": m["name"]}, country=m.get("country", ""),
                       website=m.get("website", ""), notes=m.get("notes", ""))
    for m in cat["controller_manufacturers"]:
        _get_or_create(db, ControllerManufacturer, {"name": m["name"]}, website=m.get("website", ""))
    for cm in cat["controller_models"]:
        man = db.scalars(select(ControllerManufacturer).filter_by(name=cm["manufacturer"])).one()
        model = _get_or_create(db, ControllerModel, {"manufacturer_id": man.id, "name": cm["name"]},
                               family=cm.get("family", ""), dialect_family=cm.get("dialect_family", ""))
        for v in cm.get("versions", []):
            _get_or_create(db, ControllerVersion, {"controller_model_id": model.id, "version": v["version"]},
                           notes=v.get("notes", ""))
    # postprocessors from the bundled registry
    configs = {c.key: c for c in bundled().values()}
    for p in cat["postprocessors"]:
        cfg = configs[p["key"]]
        pp = _get_or_create(db, Postprocessor, {"key": cfg.key}, name=p["name"],
                            controller_family=p["controller_family"], description=cfg.description,
                            license=p.get("license", ""))
        data = cfg.model_dump(mode="json")
        compat = [db.scalars(select(ControllerModel).filter_by(name=n)).one().id for n in p["compatible_controllers"]]
        existing = db.scalars(select(PostprocessorVersion).filter_by(postprocessor_id=pp.id, version=cfg.version)).first()
        if existing is None:
            db.add(PostprocessorVersion(
                postprocessor_id=pp.id, version=cfg.version, config=data, config_sha256=config_hash(data),
                status=p["status"], documentation_refs=cfg.documentation_refs,
                known_limitations=cfg.known_limitations, compatible_controller_model_ids=[str(c) for c in compat]))
        elif existing.config_sha256 != config_hash(data):
            raise RuntimeError(
                f"Bundled config {cfg.key}@{cfg.version} differs from the stored immutable version; "
                "bump the version number instead of editing a released configuration.")
    db.flush()
    for mm in cat["machine_models"]:
        man = db.scalars(select(MachineManufacturer).filter_by(name=mm["manufacturer"])).one()
        vals = {k: v for k, v in mm.items() if k not in ("manufacturer", "model")}
        _get_or_create(db, MachineModel, {"manufacturer_id": man.id, "model": mm["model"]}, **vals)
    for mc in cat["machine_configurations"]:
        if db.scalars(select(MachineConfiguration).filter_by(name=mc["name"], organization_id=None)).first():
            continue
        model = db.scalars(select(MachineModel).filter_by(model=mc["machine_model"])).one()
        cmodel = db.scalars(select(ControllerModel).filter_by(name=mc["controller"]["model"])).one()
        cver = db.scalars(select(ControllerVersion).filter_by(controller_model_id=cmodel.id,
                                                             version=mc["controller"]["version"])).one()
        pp = db.scalars(select(Postprocessor).filter_by(key=mc["postprocessor"]["key"])).one()
        ppv = db.scalars(select(PostprocessorVersion).filter_by(postprocessor_id=pp.id,
                                                               version=mc["postprocessor"]["version"])).one()
        vals = {k: v for k, v in mc.items() if k not in ("machine_model", "controller", "postprocessor")}
        db.add(MachineConfiguration(machine_model_id=model.id, controller_version_id=cver.id,
                                    postprocessor_version_id=ppv.id, organization_id=None, **vals))


def seed_materials(db: Session) -> None:
    data = yaml.safe_load((DATA / "materials.yaml").read_text())
    for m in data["materials"]:
        mat = _get_or_create(db, Material, {"name": m["name"]}, family=m["family"], iso_group=m["iso_group"])
        for g in m["grades"]:
            vals = {k: v for k, v in g.items() if k not in ("designation", "notes")}
            _get_or_create(db, MaterialGrade, {"designation": g["designation"]}, material_id=mat.id,
                           cutting_data_source=data["source"], cutting_data_confidence="generic_estimate", **vals)


def create_example_tool_library(db: Session, organization_id) -> ToolLibrary:
    data = yaml.safe_load((DATA / "example_tools.yaml").read_text())
    lib = ToolLibrary(organization_id=organization_id, name=data["name"], description=data["description"])
    db.add(lib)
    db.flush()
    for t in data["tools"]:
        asm = t.pop("assembly", None)
        tool = Tool(organization_id=organization_id, library_id=lib.id,
                    limitations="Example values: verify against the physical tool.", **t)
        db.add(tool)
        db.flush()
        if asm:
            db.add(ToolAssembly(tool_id=tool.id, **asm))
    return lib


def seed_all(db: Session) -> None:
    seed_catalogue(db)
    seed_materials(db)
    db.commit()


def main() -> None:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser()
    ap.add_argument("--create-tables", action="store_true", help="create tables without Alembic (dev only)")
    ap.add_argument("--admin-email")
    ap.add_argument("--admin-password")
    a = ap.parse_args()
    if a.create_tables:
        import app.models  # noqa: F401

        Base.metadata.create_all(get_engine())
    db = SessionLocal()
    try:
        seed_all(db)
        if a.admin_email:
            from app.core.security import hash_password

            u = db.scalars(select(User).filter_by(email=a.admin_email.lower())).first()
            if u is None:
                if not a.admin_password:
                    raise SystemExit("--admin-password required to create the admin user")
                u = User(email=a.admin_email.lower(), password_hash=hash_password(a.admin_password),
                         full_name="Platform administrator")
                db.add(u)
            u.is_platform_admin = True
            db.commit()
        from app.seed.verify_posts import verify_bundled_postprocessors

        for line in verify_bundled_postprocessors(db):
            print(line)
        db.commit()
        print("seed complete")
    finally:
        db.close()


if __name__ == "__main__":  # pragma: no cover
    main()
