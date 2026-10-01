"""Create sessions in specific states directly, for API-level tests."""
import uuid

from app import db
from app.models import Session, SessionStream, utcnow
from app.services import vdo


async def make_session(owner, status="live", visibility="private", streams=("screen",)):
    async with db.sessionmaker()() as s:
        sess = Session(status=status, owner_id=owner.id, visibility=visibility, vdo_room=vdo.new_room(),
                       vdo_password=vdo.new_password(), customer_name="Cathy", customer_company="Acme",
                       share_type=streams[0] if streams else "screen", customer_ip="198.51.100.9",
                       started_at=utcnow() if status == "live" else None)
        s.add(sess)
        await s.flush()
        for i, kind in enumerate(streams):
            s.add(SessionStream(session_id=sess.id, vdo_stream_id=vdo.new_stream_id(),
                                label="Camera" if kind == "camera" else f"Screen {i + 1}", share_type=kind,
                                status="live" if status == "live" else "starting"))
        await s.commit()
        await s.refresh(sess, ["streams"])
        return sess
