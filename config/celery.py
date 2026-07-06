import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

try:
    from celery import Celery
    app = Celery('apis')
    app.config_from_object('django.conf:settings', namespace='CELERY')
    app.autodiscover_tasks()

    @app.task(bind=True)
    def debug_task(self):
        print(f'Request: {self.request!r}')
except ImportError:
    # Celery not installed (e.g., during local dev migrations)
    app = None
