"""
Império SaaS - entrypoint

Mantém compatibilidade com deploys que usam:
  uvicorn loja_mvp:app

Toda a aplicação agora vive em imperio_saas.main
"""
from imperio_saas.main import app  # noqa: F401
