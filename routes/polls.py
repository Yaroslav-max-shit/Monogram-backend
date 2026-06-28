from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
import json
from database import get_db
from models import User, Poll, PollVote, Message, Membership
from .auth import get_current_user

router = APIRouter(prefix="/polls", tags=["polls"])

@router.post("/create")
def create_poll(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    chat_id = data.get("chat_id")
    question = data.get("question")
    options = data.get("options", [])
    is_anonymous = data.get("is_anonymous", True)
    if not chat_id or not question or len(options) < 2:
        raise HTTPException(status_code=400, detail="Нужны chat_id, question и минимум 2 варианта")
    membership = db.query(Membership).filter(Membership.user_id == current_user.id, Membership.chat_id == chat_id).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Нет доступа")
    poll = Poll(chat_id=chat_id, question=question, options=json.dumps(options), is_anonymous=is_anonymous)
    db.add(poll)
    db.flush()
    msg = Message(
        content=json.dumps({"type": "poll", "poll_id": poll.id, "question": question, "options": options}),
        sender_id=current_user.id,
        chat_id=chat_id
    )
    db.add(msg)
    db.commit()
    return {"poll_id": poll.id, "message_id": msg.id}

@router.post("/vote")
def vote_poll(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    poll_id = data.get("poll_id")
    option_index = data.get("option_index")
    if poll_id is None or option_index is None:
        raise HTTPException(status_code=400, detail="poll_id и option_index обязательны")
    poll = db.query(Poll).filter(Poll.id == poll_id).first()
    if not poll:
        raise HTTPException(status_code=404, detail="Опрос не найден")
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == poll.chat_id
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Нет доступа к опросу")
    existing = db.query(PollVote).filter(PollVote.poll_id == poll_id, PollVote.user_id == current_user.id).first()
    if existing:
        existing.option_index = option_index
    else:
        db.add(PollVote(poll_id=poll_id, user_id=current_user.id, option_index=option_index))
    db.commit()
    return {"status": "voted"}

@router.post("/close/{poll_id}")
def close_poll(
    poll_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    poll = db.query(Poll).filter(Poll.id == poll_id).first()
    if not poll:
        raise HTTPException(status_code=404, detail="Опрос не найден")
    membership = db.query(Membership).filter(Membership.user_id == current_user.id, Membership.chat_id == poll.chat_id, Membership.role.in_(["admin", "owner"])).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Нет прав")
    poll.is_closed = True
    db.commit()
    return {"status": "closed"}

@router.get("/{poll_id}/voters")
def get_poll_voters(poll_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    poll = db.query(Poll).filter(Poll.id == poll_id).first()
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == poll.chat_id,
        Membership.role.in_(["admin", "owner"])
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Only admins can view voters")
    votes = db.query(PollVote).filter(PollVote.poll_id == poll_id).all()
    voters = []
    for v in votes:
        user = db.query(User).filter(User.id == v.user_id).first()
        if user:
            voters.append({
                "user_id": user.id,
                "username": user.username,
                "option_index": v.option_index,
            })
    return {"voters": voters}

@router.get("/results/{poll_id}")
def get_poll_results(
    poll_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    poll = db.query(Poll).filter(Poll.id == poll_id).first()
    if not poll:
        raise HTTPException(status_code=404, detail="Опрос не найден")
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == poll.chat_id
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Нет доступа к опросу")
    votes = db.query(PollVote).filter(PollVote.poll_id == poll_id).all()
    results = {}
    for v in votes:
        results[v.option_index] = results.get(v.option_index, 0) + 1
    return {
        "question": poll.question,
        "options": json.loads(poll.options),
        "results": results,
        "is_anonymous": poll.is_anonymous,
        "is_closed": poll.is_closed,
        "total_votes": len(votes)
    }
