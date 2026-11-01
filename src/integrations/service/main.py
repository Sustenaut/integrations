"""What uvicorn runs: integrations.service.main:app"""

from .app import create_app

app = create_app()
