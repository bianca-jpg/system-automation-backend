class AuthDomainError(Exception):
    """Base das exceções de domínio do módulo auth."""


class SessaoInvalidaError(AuthDomainError):
    """Refresh token ou session token que falhou ao decodificar/validar."""


class UsuarioNaoEncontradoError(AuthDomainError):
    pass


class AutoExclusaoNaoPermitidaError(AuthDomainError):
    """Admin tentou excluir a própria conta pelo endpoint de exclusão de acesso."""


class PapeisInvalidosError(AuthDomainError):
    def __init__(self, papeis_invalidos: list[str]):
        self.papeis_invalidos = papeis_invalidos
        super().__init__(f"Papéis inválidos: {papeis_invalidos}")


class RevogacaoIndisponivelError(AuthDomainError):
    """O corte de sign-out não pôde ser gravado no Redis, então a revogação não aconteceu."""


class TokenMicrosoftInvalidoError(AuthDomainError):
    """ID token da Microsoft com assinatura, emissor, audiência ou validade inválidos."""


class ContaSsoNaoAutorizadaError(AuthDomainError):
    """O ID token da Microsoft não trouxe um e-mail utilizável (claim ausente)."""


class MicrosoftSsoNaoConfiguradoError(AuthDomainError):
    """MICROSOFT_TENANT_ID/MICROSOFT_CLIENT_ID não configurados neste ambiente."""
