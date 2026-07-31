from celery import shared_task


@shared_task(ignore_result=False)
def ping() -> str:
    return "pong"
