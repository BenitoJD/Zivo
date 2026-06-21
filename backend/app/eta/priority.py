from sqlalchemy import case

from app.models import Job, JobPriority


def priority_sort_key():
    return case(
        (Job.priority == JobPriority.HIGH, 3),
        (Job.priority == JobPriority.MEDIUM, 2),
        (Job.priority == JobPriority.LOW, 1),
        else_=0,
    )
