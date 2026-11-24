from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DB
from app.schemas.common import Membership, MeOut, MeUpdate

router = APIRouter(prefix="/me", tags=["me"])


def _load_me(conn) -> MeOut:
    user = conn.execute(
        text("""SELECT id, email, full_name, phone, locale, is_super_admin
                  FROM public.users WHERE id = auth.uid()""")
    ).mappings().one()
    rows = conn.execute(
        text("""SELECT m.firm_id, f.name AS firm_name, m.role
                  FROM public.firm_members m JOIN public.firms f ON f.id = m.firm_id
                 WHERE m.user_id = auth.uid() AND m.status = 'active'
                   AND f.status = 'active' AND f.deleted_at IS NULL
                 ORDER BY f.name""")
    ).mappings().all()
    return MeOut(**user, memberships=[Membership(**r) for r in rows])


@router.get("", response_model=MeOut, summary="Current user profile and firm memberships")
def get_me(conn: DB) -> MeOut:
    conn.execute(text("UPDATE public.users SET last_seen_at = now() WHERE id = auth.uid()"))
    return _load_me(conn)


@router.patch("", response_model=MeOut, summary="Update own profile")
def update_me(body: MeUpdate, conn: DB) -> MeOut:
    changes = body.model_dump(exclude_unset=True)
    if changes:
        assignments = ", ".join(f"{k} = :{k}" for k in changes)  # keys are whitelisted by the schema
        conn.execute(text(f"UPDATE public.users SET {assignments} WHERE id = auth.uid()"), changes)
    return _load_me(conn)
