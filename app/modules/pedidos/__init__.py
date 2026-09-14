"""Bounded context Pedidos: adequação de grades, ordens de reserva e leitura.

Sem re-exports: os consumidores importam os submódulos diretamente
(``app.modules.pedidos.infrastructure.http.routes``, ``...service``, ``...application`` etc.).
Manter este pacote vazio evita que o worker Celery arraste FastAPI e a stack
HTTP só para chamar o serviço.
"""
