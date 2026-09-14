# domain layer (regras puras, sem I/O) do módulo auth.
# roles.py permanece na raiz do módulo (alto fan-out externo: shared/security,
# parametros/routes.py, seed.py) — já é domínio puro isolado, só não foi
# movido para não ampliar o raio de mudança desta etapa.
