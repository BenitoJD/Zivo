from sqlalchemy import String, case, cast

from app.models import Job, JobPriority


def priority_sort_key():
    priority = cast(Job.priority, String)
    return case(
        (priority == JobPriority.HIGH.value, 3),
        (priority == JobPriority.MEDIUM.value, 2),
        (priority == JobPriority.LOW.value, 1),
        else_=0,
    )
