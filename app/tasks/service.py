from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.auth.models import Role, User, UserRole
from app.auth.permissions import Roles
from app.core.clock import institute_today
from app.core.config import settings
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError
from app.employees.models import Employee, JobRole
from app.notifications.models import Notification, NotificationCategory
from app.tasks.models import (
    OPEN_STATUSES,
    REJECT_LABELS,
    FlagOutcome,
    FlagStatus,
    RejectReason,
    Task,
    TaskComment,
    TaskFlag,
    TaskPriority,
    TaskStatus,
)

STAFF_ROLES = (Roles.ADMIN, Roles.TEACHER, Roles.LIBRARIAN, Roles.ACCOUNTS_OFFICER, Roles.HOSTEL_WARDEN, Roles.SECURITY_GUARD, Roles.EXAM_CONTROLLER, Roles.STAFF)
PORTAL = {Roles.ADMIN: "admin", Roles.TEACHER: "faculty", Roles.LIBRARIAN: "library", Roles.ACCOUNTS_OFFICER: "accounts", Roles.SECURITY_GUARD: "gate", Roles.EXAM_CONTROLLER: "exam", Roles.HOSTEL_WARDEN: "warden", Roles.STAFF: "staff"}
PORTAL_ORDER = (Roles.ADMIN, Roles.TEACHER, Roles.LIBRARIAN, Roles.ACCOUNTS_OFFICER, Roles.SECURITY_GUARD, Roles.EXAM_CONTROLLER, Roles.HOSTEL_WARDEN, Roles.STAFF)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=ZoneInfo(settings.institute_timezone))


def reference(t: Task) -> str:
    return f"TASK-{t.serial_no:05d}"


def _hours(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ people
    async def people(self, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, dict]:
        """name + job title (or 'Administrator') for each user."""
        out: dict[uuid.UUID, dict] = {}
        if not user_ids:
            return out
        for em, role in (await self.session.execute(
            select(Employee, JobRole.title).join(JobRole, JobRole.id == Employee.job_role_id).where(Employee.user_id.in_(user_ids))
        )).all():
            out[em.user_id] = {"user_id": em.user_id, "name": f"{em.first_name} {em.last_name}", "detail": role, "identifier": em.employee_code}
        missing = user_ids - set(out)
        if missing:
            for u in (await self.session.execute(select(User).where(User.id.in_(missing)))).scalars():
                out[u.id] = {"user_id": u.id, "name": "Administrator" if u.username.lower().startswith("admin") else u.username, "detail": "Administration", "identifier": u.username}
        return out

    async def _roles(self, user_id: uuid.UUID) -> list[str]:
        return list((await self.session.execute(select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id))).scalars())

    async def staff_directory(self, *, exclude: uuid.UUID | None = None, q: str | None = None) -> list[dict]:
        ids = set((await self.session.execute(
            select(User.id).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id).where(Role.code.in_(STAFF_ROLES), User.is_active.is_(True))
        )).scalars())
        ids.discard(exclude)
        people = await self.people(ids)
        rows = sorted(people.values(), key=lambda p: p["name"].lower())
        if q and q.strip():
            needle = q.strip().lower()
            rows = [p for p in rows if needle in p["name"].lower() or needle in (p["detail"] or "").lower() or needle in (p["identifier"] or "").lower()]
        return rows

    async def _is_staff(self, user_id: uuid.UUID) -> bool:
        user = await self.session.get(User, user_id)
        return bool(user and user.is_active and any(r in STAFF_ROLES for r in await self._roles(user_id)))

    async def _admin_ids(self) -> list[uuid.UUID]:
        return list(set((await self.session.execute(
            select(User.id).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id).where(Role.code == Roles.ADMIN, User.is_active.is_(True))
        )).scalars()))

    async def _link(self, user_id: uuid.UUID, task: Task) -> str | None:
        roles = await self._roles(user_id)
        for code in PORTAL_ORDER:
            if code in roles:
                return f"/dashboard/{PORTAL[code]}/tasks?open={task.id}"
        return None

    async def _tell(self, user_ids, task: Task, title: str, message: str) -> None:
        for uid in set(user_ids):
            self.session.add(Notification(user_id=uid, category=NotificationCategory.TASK, title=title[:200], message=message[:1000], link=await self._link(uid, task)))

    def _check_deadline(self, deadline: datetime) -> datetime:
        deadline = _aware(deadline)
        hours = settings.task_min_notice_hours
        if deadline < _now() + timedelta(hours=hours):
            raise BusinessRuleViolationError(f"The deadline must be at least {hours} hours from now.")
        if deadline > _now() + timedelta(days=366):
            raise BusinessRuleViolationError("That deadline is more than a year away.")
        return deadline

    async def get(self, task_id: uuid.UUID) -> Task:
        task = await self.session.get(Task, task_id)
        if task is None:
            raise ResourceNotFoundError("Task not found.")
        return task

    async def _visible(self, task_id: uuid.UUID, user_id: uuid.UUID, manage: bool) -> Task:
        task = await self.get(task_id)
        if not manage and user_id not in (task.assigner_user_id, task.assignee_user_id):
            raise ResourceNotFoundError("Task not found.")           # other people's tasks simply are not there
        return task

    async def _open_flag(self, task_id: uuid.UUID) -> TaskFlag | None:
        return (await self.session.execute(select(TaskFlag).where(TaskFlag.task_id == task_id, TaskFlag.status == FlagStatus.OPEN))).scalars().first()

    # ------------------------------------------------------------------ assigning
    async def create(self, *, assigner: uuid.UUID, data: dict) -> Task:
        assignee = data["assignee_user_id"]
        if assignee == assigner:
            raise BusinessRuleViolationError("You cannot assign a task to yourself. Choose another member of staff.")
        if not await self._is_staff(assignee):
            raise BusinessRuleViolationError("That person is not a member of staff who can receive tasks.")
        title, description = data["title"].strip(), data["description"].strip()
        if len(title) < 3 or len(description) < 3:
            raise BusinessRuleViolationError("Please fill in the title and the description.")
        deadline = self._check_deadline(data["deadline"])
        follow = data.get("follow_up_of_id")
        if follow is not None:
            prev = await self.get(follow)
            if prev.assigner_user_id != assigner or prev.status != TaskStatus.REJECTED:
                raise BusinessRuleViolationError("You can only follow up a task of yours that was rejected.")
        task = Task(title=title, description=description, priority=data.get("priority") or TaskPriority.NORMAL, assigner_user_id=assigner, assignee_user_id=assignee,
                    status=TaskStatus.PENDING, deadline=deadline, original_deadline=deadline, follow_up_of_id=follow)
        self.session.add(task)
        await self.session.flush()
        await self.session.refresh(task)
        people = await self.people({assigner, assignee})
        due = deadline.astimezone(ZoneInfo(settings.institute_timezone)).strftime("%a %d %b, %I:%M %p")
        await self._tell([assignee], task, f"New task from {people[assigner]['name']}", f"{reference(task)}: {title} \u2014 due {due}. Please accept or reject it.")
        await self.audit.log(actor_user_id=assigner, action="task.assigned", entity_type="task", entity_id=task.id)
        return task

    async def create_bulk(self, *, assigner: uuid.UUID, data: dict) -> dict:
        """One copy of the task for each chosen person (each accepts / rejects / completes their own). Yourself and anyone
        who cannot receive tasks are left out and listed; nothing is created if nobody is left."""
        self._check_deadline(data["deadline"])
        ids = list(dict.fromkeys(data["assignee_user_ids"]))
        names = await self.people(set(ids))
        skipped, todo = [], []
        for uid in ids:
            who = names.get(uid, {}).get("name") or "Unknown person"
            if uid == assigner:
                skipped.append(f"{who} (that is you)")
            elif not await self._is_staff(uid):
                skipped.append(f"{who} (cannot receive tasks)")
            else:
                todo.append(uid)
        if not todo:
            raise BusinessRuleViolationError("Nobody you chose can receive this task: " + "; ".join(skipped) + ".")
        created = []
        for uid in todo:
            task = await self.create(assigner=assigner, data={**data, "assignee_user_id": uid, "follow_up_of_id": None})
            created.append({"id": task.id, "reference": reference(task), "assignee": names[uid]["name"]})
        await self.audit.log(actor_user_id=assigner, action="task.assigned_bulk", entity_type="task", entity_id=None,
                             metadata={"count": len(created), "title": data["title"].strip()[:200], "skipped": skipped})
        return {"created": created, "skipped": skipped}

    async def update(self, task_id: uuid.UUID, *, user_id: uuid.UUID, changes: dict) -> Task:
        task = await self.get(task_id)
        if task.assigner_user_id != user_id:
            raise AuthorizationError("Only the person who assigned a task can change it.")
        if task.status not in OPEN_STATUSES:
            raise BusinessRuleViolationError("This task is finished, so it can no longer be changed.")
        changed = []
        if task.status == TaskStatus.ACCEPTED:
            extras = [k for k in ("title", "description", "priority") if changes.get(k) is not None]
            if extras:
                raise BusinessRuleViolationError("Once a task has been accepted only its deadline can be changed. Add a comment for anything else.")
        for key in ("title", "description"):
            if changes.get(key) is not None and changes[key].strip() != getattr(task, key):
                setattr(task, key, changes[key].strip()); changed.append(key)
        if changes.get("priority") is not None and changes["priority"] != task.priority:
            task.priority = changes["priority"]; changed.append("priority")
        if changes.get("deadline") is not None:
            new = self._check_deadline(changes["deadline"])
            if new != task.deadline:
                if new > task.deadline:
                    task.extension_count += 1
                task.deadline = new; changed.append("deadline")
        if changed:
            await self._tell([task.assignee_user_id], task, "Task updated", f"{reference(task)}: {', '.join(changed)} changed.")
            await self.audit.log(actor_user_id=user_id, action="task.updated", entity_type="task", entity_id=task.id)
        return task

    async def cancel(self, task_id: uuid.UUID, *, user_id: uuid.UUID, manage: bool, reason: str) -> Task:
        task = await self.get(task_id)
        if task.assigner_user_id != user_id and not manage:
            raise AuthorizationError("Only the person who assigned a task (or the administration) can cancel it.")
        self._cancel(task, reason)
        await self._tell([task.assignee_user_id] if task.assignee_user_id != user_id else [], task, "Task cancelled", f"{reference(task)}: {task.title} \u2014 {reason.strip()}")
        await self.audit.log(actor_user_id=user_id, action="task.cancelled", entity_type="task", entity_id=task.id)
        return task

    @staticmethod
    def _cancel(task: Task, reason: str) -> None:
        if task.status not in OPEN_STATUSES:
            raise BusinessRuleViolationError("Only a task that is still open can be cancelled.")
        task.status, task.cancelled_at, task.cancel_reason = TaskStatus.CANCELLED, _now(), reason.strip()

    # ------------------------------------------------------------------ the receiver
    async def respond(self, task_id: uuid.UUID, *, user_id: uuid.UUID, accept: bool, reason: RejectReason | None, explanation: str | None) -> Task:
        task = await self.get(task_id)
        if task.assignee_user_id != user_id:
            raise AuthorizationError("This task was not assigned to you.")
        if task.status != TaskStatus.PENDING:
            raise BusinessRuleViolationError("You have already answered this task.")
        now = _now()
        task.responded_at = now
        who = (await self.people({user_id}))[user_id]["name"]
        if accept:
            task.status, task.accepted_at = TaskStatus.ACCEPTED, now
            await self._tell([task.assigner_user_id], task, "Task accepted", f"{who} accepted {reference(task)}: {task.title}.")
        else:
            task.status, task.reject_reason, task.reject_explanation = TaskStatus.REJECTED, reason, (explanation or "").strip()
            await self._tell([task.assigner_user_id], task, "Task rejected", f"{who} rejected {reference(task)}: {REJECT_LABELS[reason]}. {task.reject_explanation}")
            # ...and it is put in front of the administration automatically (as well as being recorded), so every rejection is seen.
            self.session.add(TaskFlag(task_id=task.id, flagged_by=user_id, status=FlagStatus.OPEN, is_automatic=True,
                                      reason=f"Rejected \u2014 {REJECT_LABELS[reason]}. {task.reject_explanation}"))
            admins = [a for a in await self._admin_ids() if a not in (task.assigner_user_id, user_id)]
            await self._tell(admins, task, f"Rejected task needs review: {reference(task)}", f"{who} rejected \u201c{task.title}\u201d ({REJECT_LABELS[reason]}): {task.reject_explanation}")
        await self.audit.log(actor_user_id=user_id, action="task.accepted" if accept else "task.rejected", entity_type="task", entity_id=task.id)
        return task

    async def complete(self, task_id: uuid.UUID, *, user_id: uuid.UUID, note: str | None) -> Task:
        task = await self.get(task_id)
        if task.assignee_user_id != user_id:
            raise AuthorizationError("This task was not assigned to you.")
        if task.status != TaskStatus.ACCEPTED:
            raise BusinessRuleViolationError("Accept the task first." if task.status == TaskStatus.PENDING else "This task is not open.")
        # The time the receiver reports it done is what counts for "on time" -- however long the assigner takes to confirm.
        task.status, task.completed_at, task.completion_note, task.sent_back_note = TaskStatus.AWAITING_CONFIRMATION, _now(), (note or "").strip() or None, None
        who = (await self.people({user_id}))[user_id]["name"]
        late = task.completed_at > task.deadline
        await self._tell([task.assigner_user_id], task, "Task completed \u2014 please confirm" + (" (late)" if late else ""),
                         f"{who} says {reference(task)} is done: {task.title}." + (f" Note: {task.completion_note}" if task.completion_note else "") + " Confirm it, or send it back.")
        await self.audit.log(actor_user_id=user_id, action="task.reported_done", entity_type="task", entity_id=task.id)
        return task

    async def confirm(self, task_id: uuid.UUID, *, user_id: uuid.UUID, manage: bool) -> Task:
        task = await self.get(task_id)
        if task.assigner_user_id != user_id and not manage:
            raise AuthorizationError("Only the person who assigned this task (or the administration) can confirm it.")
        if task.status != TaskStatus.AWAITING_CONFIRMATION:
            raise BusinessRuleViolationError("This task is not waiting for confirmation.")
        task.status, task.confirmed_at, task.confirmed_by = TaskStatus.COMPLETED, _now(), user_id
        await self._tell([u for u in (task.assignee_user_id,) if u != user_id], task, "Task confirmed", f"{reference(task)}: {task.title} \u2014 confirmed as done.")
        await self.audit.log(actor_user_id=user_id, action="task.confirmed", entity_type="task", entity_id=task.id)
        return task

    async def send_back(self, task_id: uuid.UUID, *, user_id: uuid.UUID, manage: bool, note: str, new_deadline: datetime | None) -> Task:
        """Not good enough yet: back to the receiver with a reason (and, optionally, more time)."""
        task = await self.get(task_id)
        if task.assigner_user_id != user_id and not manage:
            raise AuthorizationError("Only the person who assigned this task (or the administration) can send it back.")
        if task.status != TaskStatus.AWAITING_CONFIRMATION:
            raise BusinessRuleViolationError("This task is not waiting for confirmation.")
        if new_deadline is not None:
            new = self._check_deadline(new_deadline)
            if new > task.deadline:
                task.extension_count += 1
            task.deadline = new
        task.status, task.completed_at, task.completion_note = TaskStatus.ACCEPTED, None, None
        task.rework_count, task.sent_back_note = task.rework_count + 1, note.strip()
        self.session.add(TaskComment(task_id=task.id, author_user_id=user_id, body=f"Sent back: {note.strip()}"))
        await self._tell([task.assignee_user_id], task, "Task sent back", f"{reference(task)}: {task.title} \u2014 {note.strip()}")
        await self.audit.log(actor_user_id=user_id, action="task.sent_back", entity_type="task", entity_id=task.id)
        return task

    # ------------------------------------------------------------------ the conversation, flags and the administration
    async def comment(self, task_id: uuid.UUID, *, user_id: uuid.UUID, manage: bool, body: str) -> Task:
        task = await self._visible(task_id, user_id, manage)
        body = body.strip()
        if not body:
            raise BusinessRuleViolationError("Write a message first.")
        self.session.add(TaskComment(task_id=task.id, author_user_id=user_id, body=body))
        await self.session.flush()
        who = (await self.people({user_id}))[user_id]["name"]
        await self._tell([u for u in (task.assigner_user_id, task.assignee_user_id) if u != user_id], task, f"Message on {reference(task)}", f"{who}: {body}")
        return task

    async def flag(self, task_id: uuid.UUID, *, user_id: uuid.UUID, reason: str) -> TaskFlag:
        task = await self.get(task_id)
        if user_id not in (task.assigner_user_id, task.assignee_user_id):
            raise AuthorizationError("Only the person who assigned this task or the one who received it can flag it.")
        if await self._open_flag(task.id) is not None:
            raise BusinessRuleViolationError("This task is already flagged and waiting for the administration.")
        flag = TaskFlag(task_id=task.id, flagged_by=user_id, reason=reason.strip(), status=FlagStatus.OPEN)
        self.session.add(flag)
        await self.session.flush()
        who = (await self.people({user_id}))[user_id]["name"]
        other = task.assignee_user_id if user_id == task.assigner_user_id else task.assigner_user_id
        await self._tell(await self._admin_ids(), task, f"Task flagged: {reference(task)}", f"{who} asked for a review of \u201c{task.title}\u201d: {reason.strip()}")
        await self._tell([other], task, f"{reference(task)} was flagged for review", f"{who} asked the administration to look at \u201c{task.title}\u201d.")
        await self.audit.log(actor_user_id=user_id, action="task.flagged", entity_type="task", entity_id=task.id)
        return flag

    async def resolve_flag(self, task_id: uuid.UUID, *, admin_id: uuid.UUID, outcome: FlagOutcome, note: str, new_deadline: datetime | None) -> Task:
        task = await self.get(task_id)
        flag = await self._open_flag(task.id)
        if flag is None:
            raise BusinessRuleViolationError("There is nothing flagged on this task.")
        if task.status == TaskStatus.REJECTED:
            if outcome not in (FlagOutcome.NO_ACTION, FlagOutcome.REJECTION_OVERRULED):
                raise BusinessRuleViolationError("A rejected task can be left as it is, or the rejection can be overruled so the task stands.")
        elif outcome == FlagOutcome.REJECTION_OVERRULED:
            raise BusinessRuleViolationError("Only a rejected task can have its rejection overruled.")
        message = {FlagOutcome.NO_ACTION: "No change was made", FlagOutcome.TASK_CANCELLED: "The task was cancelled", FlagOutcome.DEADLINE_EXTENDED: "The deadline was changed",
                   FlagOutcome.REJECTION_OVERRULED: "The rejection was overruled: the task stands"}[outcome]
        if outcome == FlagOutcome.TASK_CANCELLED:
            self._cancel(task, f"Cancelled by the administration: {note.strip()}")
        elif outcome == FlagOutcome.DEADLINE_EXTENDED:
            if task.status not in OPEN_STATUSES:
                raise BusinessRuleViolationError("Only the deadline of an open task can be changed.")
            new = self._check_deadline(new_deadline)
            if new > task.deadline:
                task.extension_count += 1
            task.deadline = new
        elif outcome == FlagOutcome.REJECTION_OVERRULED:
            follow = (await self.session.execute(select(func.count()).select_from(Task).where(Task.follow_up_of_id == task.id))).scalar_one()
            if follow:
                raise BusinessRuleViolationError("A follow-up task was already created from this one, so the rejection cannot be overruled.")
            if new_deadline is not None:
                new = self._check_deadline(new_deadline)
                if new > task.deadline:
                    task.extension_count += 1
                task.deadline = new
            elif task.deadline < _now() + timedelta(hours=settings.task_min_notice_hours):
                raise BusinessRuleViolationError(f"The original deadline is too close or already past. Choose a new deadline at least {settings.task_min_notice_hours} hours from now.")
            # The task stands. The rejection stays on the record (reason and all); it was NOT accepted voluntarily, so accepted_at stays empty.
            task.status, task.rejection_overruled_at = TaskStatus.ACCEPTED, _now()
        flag.status, flag.outcome, flag.resolution_note, flag.resolved_by, flag.resolved_at = FlagStatus.RESOLVED, outcome, note.strip(), admin_id, _now()
        await self._tell({task.assigner_user_id, task.assignee_user_id} - {admin_id}, task, f"Review finished: {reference(task)}", f"{message}. {note.strip()}")
        await self.audit.log(actor_user_id=admin_id, action=f"task.flag_{outcome.value.lower()}", entity_type="task", entity_id=task.id)
        return task

    # ------------------------------------------------------------------ reading
    def abilities(self, t: Task, user_id: uuid.UUID, manage: bool, has_open_flag: bool) -> dict:
        assignee, assigner = t.assignee_user_id == user_id, t.assigner_user_id == user_id
        open_ = t.status in OPEN_STATUSES
        return {
            "accept": assignee and t.status == TaskStatus.PENDING, "reject": assignee and t.status == TaskStatus.PENDING,
            "complete": assignee and t.status == TaskStatus.ACCEPTED, "cancel": (assigner or manage) and open_,
            "confirm": (assigner or manage) and t.status == TaskStatus.AWAITING_CONFIRMATION, "send_back": (assigner or manage) and t.status == TaskStatus.AWAITING_CONFIRMATION,
            "edit": ("ALL" if t.status == TaskStatus.PENDING else "DEADLINE") if assigner and open_ else None,
            "flag": (assignee or assigner) and not has_open_flag, "comment": assignee or assigner or manage,
            "assign_again": assigner and t.status == TaskStatus.REJECTED and not t.rejection_overruled_at, "resolve_flag": manage and has_open_flag,
        }

    async def _shape(self, tasks: list[Task], *, user_id: uuid.UUID, manage: bool, detail: bool) -> list[dict]:
        if not tasks:
            return []
        ids = [t.id for t in tasks]
        people = await self.people({t.assigner_user_id for t in tasks} | {t.assignee_user_id for t in tasks})
        open_flags = {f.task_id for f in (await self.session.execute(select(TaskFlag).where(TaskFlag.task_id.in_(ids), TaskFlag.status == FlagStatus.OPEN))).scalars()}
        counts = {tid: n for tid, n in (await self.session.execute(select(TaskComment.task_id, func.count()).where(TaskComment.task_id.in_(ids)).group_by(TaskComment.task_id))).all()}
        follow = {t.id: t for t in (await self.session.execute(select(Task).where(Task.id.in_([t.follow_up_of_id for t in tasks if t.follow_up_of_id])))).scalars()} if any(t.follow_up_of_id for t in tasks) else {}
        comments: dict[uuid.UUID, list[TaskComment]] = {}
        flags: dict[uuid.UUID, list[TaskFlag]] = {}
        if detail:
            for c in (await self.session.execute(select(TaskComment).where(TaskComment.task_id.in_(ids)).order_by(TaskComment.created_at))).scalars():
                comments.setdefault(c.task_id, []).append(c)
            for f in (await self.session.execute(select(TaskFlag).where(TaskFlag.task_id.in_(ids)).order_by(TaskFlag.created_at))).scalars():
                flags.setdefault(f.task_id, []).append(f)
            people.update(await self.people({x for lst in comments.values() for x in [c.author_user_id for c in lst] if x} | {x for lst in flags.values() for f in lst for x in (f.flagged_by, f.resolved_by) if x} - set(people)))
        ref = lambda uid: {k: people[uid][k] for k in ("user_id", "name", "detail")} if uid in people else {"user_id": uid, "name": "Unknown", "detail": None}     # noqa: E731
        now = _now()
        out = []
        for t in tasks:
            is_open = t.status in OPEN_STATUSES
            timeliness = late_by = None
            if t.status in (TaskStatus.COMPLETED, TaskStatus.AWAITING_CONFIRMATION) and t.completed_at:
                timeliness = "ON_TIME" if t.completed_at <= t.deadline else "LATE"
                late_by = round(_hours(t.deadline, t.completed_at), 1) if timeliness == "LATE" else None
            role = "ASSIGNEE" if t.assignee_user_id == user_id else "ASSIGNER" if t.assigner_user_id == user_id else "ADMIN"
            out.append({
                "id": t.id, "reference": reference(t), "title": t.title, "description": t.description, "priority": t.priority, "status": t.status,
                "assigner": ref(t.assigner_user_id), "assignee": ref(t.assignee_user_id), "deadline": t.deadline, "original_deadline": t.original_deadline,
                "extension_count": t.extension_count, "created_at": t.created_at, "responded_at": t.responded_at, "accepted_at": t.accepted_at,
                "reject_reason": t.reject_reason, "reject_reason_label": REJECT_LABELS.get(t.reject_reason) if t.reject_reason else None, "reject_explanation": t.reject_explanation,
                "completed_at": t.completed_at, "completion_note": t.completion_note, "confirmed_at": t.confirmed_at, "rework_count": t.rework_count,
                "sent_back_note": t.sent_back_note, "rejection_overruled_at": t.rejection_overruled_at, "cancelled_at": t.cancelled_at, "cancel_reason": t.cancel_reason,
                "is_overdue": is_open and now > t.deadline, "timeliness": timeliness, "late_by_hours": late_by, "is_flagged": t.id in open_flags,
                "comment_count": counts.get(t.id, 0), "follow_up_of": {"id": follow[t.follow_up_of_id].id, "reference": reference(follow[t.follow_up_of_id])} if t.follow_up_of_id in follow else None,
                "my_role": role, "can": self.abilities(t, user_id, manage, t.id in open_flags),
                "comments": [{"id": c.id, "author_user_id": c.author_user_id, "author_name": people.get(c.author_user_id, {"name": "Someone"})["name"], "body": c.body, "created_at": c.created_at} for c in comments.get(t.id, [])],
                "flags": [{"id": f.id, "flagged_by_name": people.get(f.flagged_by, {"name": "Someone"})["name"], "flagged_by_user_id": f.flagged_by, "reason": f.reason, "is_automatic": f.is_automatic, "status": f.status, "outcome": f.outcome,
                           "resolution_note": f.resolution_note, "resolved_by_name": people.get(f.resolved_by, {"name": None})["name"] if f.resolved_by else None, "resolved_at": f.resolved_at,
                           "created_at": f.created_at} for f in flags.get(t.id, [])],
            })
        return out

    async def detail(self, task_id: uuid.UUID, *, user_id: uuid.UUID, manage: bool) -> dict:
        task = await self._visible(task_id, user_id, manage)
        return (await self._shape([task], user_id=user_id, manage=manage, detail=True))[0]

    async def inbox(self, user_id: uuid.UUID, manage: bool) -> list[dict]:
        rows = list((await self.session.execute(select(Task).where(Task.assignee_user_id == user_id).order_by(Task.created_at.desc()).limit(500))).scalars())
        return self._order(await self._shape(rows, user_id=user_id, manage=manage, detail=False), inbox=True)

    async def sent(self, user_id: uuid.UUID, manage: bool) -> list[dict]:
        rows = list((await self.session.execute(select(Task).where(Task.assigner_user_id == user_id).order_by(Task.created_at.desc()).limit(500))).scalars())
        return self._order(await self._shape(rows, user_id=user_id, manage=manage, detail=False), inbox=False)

    @staticmethod
    def _order(rows: list[dict], *, inbox: bool) -> list[dict]:
        """What needs the reader first. In MY inbox: waiting for my answer, then in progress. In what I assigned: waiting for MY confirmation first."""
        order = ([TaskStatus.PENDING, TaskStatus.ACCEPTED, TaskStatus.AWAITING_CONFIRMATION] if inbox else [TaskStatus.AWAITING_CONFIRMATION, TaskStatus.PENDING, TaskStatus.ACCEPTED])
        rank = lambda r: order.index(r["status"]) if r["status"] in order else len(order)         # noqa: E731
        rows.sort(key=lambda r: (rank(r), r["deadline"].timestamp() if rank(r) < len(order) else -r["created_at"].timestamp()))
        return rows

    async def list_all(self, *, user_id: uuid.UUID, status: TaskStatus | None, assignee: uuid.UUID | None, assigner: uuid.UUID | None, flagged: bool, overdue: bool,
                       q: str | None, days: int, limit: int, offset: int) -> list[dict]:
        stmt = select(Task).order_by(Task.created_at.desc())
        if status:
            stmt = stmt.where(Task.status == status)
        if assignee:
            stmt = stmt.where(Task.assignee_user_id == assignee)
        if assigner:
            stmt = stmt.where(Task.assigner_user_id == assigner)
        if days > 0:
            stmt = stmt.where(Task.created_at >= _now() - timedelta(days=days))
        if overdue:
            stmt = stmt.where(Task.status.in_(OPEN_STATUSES), Task.deadline < _now())
        if flagged:
            stmt = stmt.where(Task.id.in_(select(TaskFlag.task_id).where(TaskFlag.status == FlagStatus.OPEN)))
        rows = list((await self.session.execute(stmt.limit(2000))).scalars())
        shaped = await self._shape(rows, user_id=user_id, manage=True, detail=False)
        if q and q.strip():
            n = q.strip().lower()
            shaped = [r for r in shaped if n in r["title"].lower() or n in r["reference"].lower() or n in r["assigner"]["name"].lower() or n in r["assignee"]["name"].lower()]
        return shaped[max(0, offset): max(0, offset) + max(1, min(limit, 200))]

    async def summary(self, user_id: uuid.UUID, manage: bool) -> dict:
        now = _now()
        mine = list((await self.session.execute(select(Task).where(Task.assignee_user_id == user_id, Task.status.in_(OPEN_STATUSES)))).scalars())
        needs = sum(1 for t in mine if t.status == TaskStatus.PENDING)
        to_confirm = (await self.session.execute(select(func.count()).select_from(Task).where(Task.assigner_user_id == user_id, Task.status == TaskStatus.AWAITING_CONFIRMATION))).scalar_one()
        overdue = sum(1 for t in mine if t.deadline < now)
        open_flags = (await self.session.execute(select(func.count()).select_from(TaskFlag).where(TaskFlag.status == FlagStatus.OPEN))).scalar_one() if manage else 0
        return {"needs_response": needs, "needs_confirmation": to_confirm, "overdue": overdue, "open": sum(1 for t in mine if t.status == TaskStatus.ACCEPTED), "open_flags": open_flags,
                "badge": needs + to_confirm + open_flags}

    # ------------------------------------------------------------------ progress reports
    @staticmethod
    def _receiver_stats(tasks: list[Task], now: datetime) -> dict:
        """A task counts as DONE once the receiver reports it (even if the assigner has not confirmed yet), judged by the time they reported it.
        A task that was ever rejected counts as rejected, even if the administration later overruled the rejection."""
        s = dict(received=len(tasks), accepted=0, rejected=0, completed=0, awaiting_confirmation=0, sent_back=0, completed_on_time=0, completed_late=0,
                 overdue=0, still_open=0, cancelled=0, pending_response=0)
        resp, comp = [], []
        for t in tasks:
            if t.accepted_at is not None:
                s["accepted"] += 1
            if t.reject_reason is not None:
                s["rejected"] += 1
            s["sent_back"] += t.rework_count or 0
            if t.responded_at is not None:
                resp.append(_hours(t.created_at, t.responded_at))
            if t.status == TaskStatus.CANCELLED:
                s["cancelled"] += 1
            elif t.status in (TaskStatus.COMPLETED, TaskStatus.AWAITING_CONFIRMATION):
                s["completed"] += 1
                s["awaiting_confirmation"] += t.status == TaskStatus.AWAITING_CONFIRMATION
                s["completed_on_time" if t.completed_at <= t.deadline else "completed_late"] += 1
                if t.accepted_at:
                    comp.append(_hours(t.accepted_at, t.completed_at))
            elif t.status == TaskStatus.REJECTED:
                pass
            elif t.deadline < now:
                s["overdue"] += 1
            else:
                s["pending_response" if t.status == TaskStatus.PENDING else "still_open"] += 1
        decided = s["accepted"] + s["rejected"]
        judged = s["completed"] + s["overdue"]
        s["acceptance_rate"] = round(s["accepted"] / decided * 100, 1) if decided else None
        s["on_time_rate"] = round(s["completed_on_time"] / judged * 100, 1) if judged else None
        s["avg_response_hours"] = round(sum(resp) / len(resp), 1) if resp else None
        s["avg_completion_hours"] = round(sum(comp) / len(comp), 1) if comp else None
        return s

    @staticmethod
    def _assigner_stats(tasks: list[Task], now: datetime, flagged_ids: set[uuid.UUID]) -> dict:
        return {"assigned": len(tasks), "rejected": sum(t.reject_reason is not None for t in tasks), "completed": sum(t.status == TaskStatus.COMPLETED for t in tasks),
                "awaiting_confirmation": sum(t.status == TaskStatus.AWAITING_CONFIRMATION for t in tasks),
                "overdue": sum(t.status in OPEN_STATUSES and t.deadline < now for t in tasks), "cancelled": sum(t.status == TaskStatus.CANCELLED for t in tasks),
                "flagged": sum(t.id in flagged_ids for t in tasks)}

    @staticmethod
    def _reasons(tasks: list[Task]) -> list[dict]:
        counts: dict = {}
        for t in tasks:
            if t.reject_reason:
                counts[t.reject_reason] = counts.get(t.reject_reason, 0) + 1
        return sorted(({"key": r.value, "label": REJECT_LABELS[r], "count": n} for r, n in counts.items()), key=lambda x: -x["count"])

    @staticmethod
    def _weeks(tasks: list[Task]) -> list[dict]:
        tz = ZoneInfo(settings.institute_timezone)
        today = institute_today()
        monday = today - timedelta(days=today.weekday())
        weeks = {monday - timedelta(weeks=i): [0, 0, 0] for i in range(11, -1, -1)}

        def bucket(dt):
            d = dt.astimezone(tz).date()
            return d - timedelta(days=d.weekday())

        for t in tasks:
            if (k := bucket(t.created_at)) in weeks:
                weeks[k][0] += 1
            if t.completed_at and (k := bucket(t.completed_at)) in weeks:
                weeks[k][1] += 1
            if t.reject_reason and t.responded_at and (k := bucket(t.responded_at)) in weeks:
                weeks[k][2] += 1
        return [{"week_start": wk, "assigned": v[0], "completed": v[1], "rejected": v[2]} for wk, v in weeks.items()]

    async def _window(self, days: int, *extra) -> list[Task]:
        stmt = select(Task).order_by(Task.created_at)
        if days > 0:
            stmt = stmt.where(Task.created_at >= _now() - timedelta(days=days))
        for cond in extra:
            stmt = stmt.where(cond)
        return list((await self.session.execute(stmt.limit(50000))).scalars())

    async def staff_report(self, days: int) -> dict:
        now = _now()
        tasks = await self._window(days)
        flags = list((await self.session.execute(select(TaskFlag).where(TaskFlag.task_id.in_([t.id for t in tasks])) if tasks else select(TaskFlag).where(False))).scalars())
        flagged_ids = {f.task_id for f in flags}
        by_assignee: dict = {}
        by_assigner: dict = {}
        for t in tasks:
            by_assignee.setdefault(t.assignee_user_id, []).append(t)
            by_assigner.setdefault(t.assigner_user_id, []).append(t)
        people = await self.people(set(by_assignee) | set(by_assigner))
        rows = []
        for uid in sorted(people, key=lambda u: people[u]["name"].lower()):
            rows.append({"user_id": uid, "name": people[uid]["name"], "detail": people[uid]["detail"],
                         "receiving": self._receiver_stats(by_assignee.get(uid, []), now), "assigning": self._assigner_stats(by_assigner.get(uid, []), now, flagged_ids)})
        return {"days": days, "since": (now - timedelta(days=days)) if days > 0 else None, "totals": self._receiver_stats(tasks, now),
                "flags_open": sum(f.status == FlagStatus.OPEN for f in flags), "flags_resolved": sum(f.status == FlagStatus.RESOLVED for f in flags),
                "reasons": self._reasons(tasks), "weeks": self._weeks(tasks), "rows": rows}

    async def person_report(self, user_id: uuid.UUID, days: int, *, viewer: uuid.UUID, manage: bool) -> dict:
        now = _now()
        person = (await self.people({user_id})).get(user_id)
        if person is None:
            raise ResourceNotFoundError("Person not found.")
        received = await self._window(days, Task.assignee_user_id == user_id)
        assigned = await self._window(days, Task.assigner_user_id == user_id)
        flagged_ids = {f.task_id for f in (await self.session.execute(select(TaskFlag).where(TaskFlag.task_id.in_([t.id for t in assigned]))) ).scalars()} if assigned else set()
        every = {t.id: t for t in received + assigned}
        flag_hits = {f.task_id for f in (await self.session.execute(select(TaskFlag).where(TaskFlag.task_id.in_(list(every))))).scalars()} if every else set()
        shape = lambda ts: self._shape(ts, user_id=viewer, manage=manage, detail=False)                         # noqa: E731
        rejected = [t for t in received if t.reject_reason is not None]
        late = [t for t in received if (t.status in (TaskStatus.COMPLETED, TaskStatus.AWAITING_CONFIRMATION) and t.completed_at > t.deadline) or (t.status in OPEN_STATUSES and t.deadline < now)]
        flagged = [t for t in every.values() if t.id in flag_hits]
        return {"days": days, "since": (now - timedelta(days=days)) if days > 0 else None, "person": {k: person[k] for k in ("user_id", "name", "detail")},
                "receiving": self._receiver_stats(received, now), "assigning": self._assigner_stats(assigned, now, flagged_ids), "reasons": self._reasons(received),
                "weeks": self._weeks(received), "rejected": await shape(sorted(rejected, key=lambda t: t.created_at, reverse=True)),
                "late": await shape(sorted(late, key=lambda t: t.deadline, reverse=True)), "flagged": await shape(sorted(flagged, key=lambda t: t.created_at, reverse=True))}