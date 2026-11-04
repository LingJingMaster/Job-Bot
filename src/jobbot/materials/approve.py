"""批准门。存在 unsupported 或 needs_review 时抛 ApprovalBlocked。"""

from __future__ import annotations

from jobbot.contracts import ApprovalBlocked, Material
from jobbot.db.repository import update_by_id
from jobbot.materials.store import connect_db, load_material, load_profile_snapshot
from jobbot.materials.verify import claims_block_message, reload_claims, verify_claims


def approve(material_id: str) -> Material:
    material = load_material(material_id)
    if material.status != "approved":
        profile = load_profile_snapshot(material.profile_snapshot_id)
        verify_claims(material, profile)
        material = load_material(material_id)
    blocked = claims_block_message(reload_claims(material_id))
    if blocked:
        raise ApprovalBlocked(blocked, saved=True, next_step="修改正文并重新校验，直到没有 unsupported 和 needs_review。")
    if material.status != "approved":
        with connect_db() as connection:
            update_by_id(connection, "material", material_id, {"status": "approved"})
        material = load_material(material_id)
    assert material.status == "approved"
    return material
