class ParametrosDomainError(Exception):
    """Base das exceções de domínio do módulo parametros."""


class ParametroJaExisteError(ParametrosDomainError):
    def __init__(self, chave: str):
        self.chave = chave
        super().__init__(f"Parâmetro '{chave}' já existe.")


class ParametroNaoEncontradoError(ParametrosDomainError):
    pass


class ChangeRequestNaoEncontradoError(ParametrosDomainError):
    pass


class ChangeRequestJaRevisadoError(ParametrosDomainError):
    def __init__(self, status_atual: str):
        self.status_atual = status_atual
        super().__init__(f"Solicitação já está '{status_atual}'.")


class ValorDeParametroInvalidoError(ParametrosDomainError):
    def __init__(self, chave: str, motivo: str):
        self.chave = chave
        self.motivo = motivo
        super().__init__(f"Valor inválido para o parâmetro '{chave}': {motivo}")
