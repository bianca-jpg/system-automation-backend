from __future__ import annotations

import logging
from datetime import UTC, datetime

from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.application.schemas import (
    AuthResponse,
    AuthUserResponse,
    MicrosoftSsoRequest,
    RefreshTokenRequest,
    SignOutResponse,
    TokenBlock,
)
from app.modules.auth.domain.email import normalizar_email
from app.modules.auth.domain.exceptions import (
    AutoExclusaoNaoPermitidaError,
    ContaSsoNaoAutorizadaError,
    MicrosoftSsoNaoConfiguradoError,
    PapeisInvalidosError,
    RevogacaoIndisponivelError,
    SessaoInvalidaError,
    UsuarioNaoEncontradoError,
)
from app.modules.auth.domain.roles import automationRole, role_title
from app.modules.auth.infrastructure import repositorio_usuario
from app.modules.auth.infrastructure.microsoft_sso import validar_id_token_microsoft
from app.modules.auth.infrastructure.models import AuthUser
from app.modules.auth.infrastructure.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from app.shared.config.settings import get_settings
from app.shared.infrastructure.redis_client import get_redis
from app.shared.security import CurrentUser

logger = logging.getLogger(__name__)

# Chave do corte de sign-out no Redis, por usuário (não por jti — decisão
# travada 2: revoga todas as sessões do usuário, não uma sessão específica).
_SIGNED_OUT_SINCE_KEY_TEMPLATE = "auth:signed_out_since:{user_id}"
# TTL espelha os 7 dias de vida do refresh token em create_refresh_token —
# passado esse prazo nenhum token anterior ao corte pode mais existir.
_SIGNED_OUT_TTL_SECONDS = 7 * 86_400
# Chave do jti vigente por usuário, para detecção de reuso na rotação do
# refresh token. É alias do TTL de sign-out (não um literal novo) porque é a
# mesma vida de 7 dias do refresh token — não faz sentido guardar o jti por
# mais tempo do que o próprio token pode existir.
_REFRESH_JTI_KEY_TEMPLATE = "auth:refresh_jti:{user_id}"
_REFRESH_JTI_TTL_SECONDS = _SIGNED_OUT_TTL_SECONDS


def _chave_sign_out(user_id: int | str) -> str:
    return _SIGNED_OUT_SINCE_KEY_TEMPLATE.format(user_id=user_id)


def _chave_refresh_jti(user_id: int | str) -> str:
    # str.format renderiza 42 e "42" igual, então a chave escrita a partir de
    # current_user.id (int) e a partir de payload["sub"] (str) coincidem —
    # load-bearing no ramo de reuso abaixo, que grava o corte de sign-out com
    # payload["sub"] e precisa bater com a chave que sign_out grava com
    # current_user.id.
    return _REFRESH_JTI_KEY_TEMPLATE.format(user_id=user_id)


def _user_response(user: AuthUser) -> AuthUserResponse:
    roles = list(user.roles or [])
    return AuthUserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role_title=role_title(roles),
        roles=roles,
        groups=roles,
    )


def _auth_success(user: AuthUser, *, auth_time: int | None = None) -> AuthResponse:
    roles = list(user.roles or [])
    access_token, expires_in = create_access_token(
        user_id=user.id, email=user.email, roles=roles
    )
    # auth_time ausente = autenticação nova (sign-in/SSO/confirm), então o
    # refresh token nasce com auth_time=agora. Em refresh_token, o chamador
    # repassa o auth_time do token anterior para preservar o teto absoluto.
    refresh_token = create_refresh_token(
        user_id=user.id, email=user.email, roles=roles, auth_time=auth_time
    )
    return AuthResponse(
        user=_user_response(user),
        token=TokenBlock(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        ),
    )


async def sign_in_microsoft(
    session: AsyncSession, body: MicrosoftSsoRequest
) -> AuthResponse:
    settings = get_settings()
    if not settings.microsoft_tenant_id or not settings.microsoft_client_id:
        raise MicrosoftSsoNaoConfiguradoError()

    payload = await validar_id_token_microsoft(
        body.id_token,
        tenant_id=settings.microsoft_tenant_id,
        client_id=settings.microsoft_client_id,
    )
    email_bruto = payload.get("email") or payload.get("preferred_username")
    if not email_bruto:
        raise ContaSsoNaoAutorizadaError()

    email = normalizar_email(str(email_bruto))
    # Nome do diretório corporativo. O claim `name` é opcional no OIDC, então
    # ausência/vazio/tipo inesperado viram None em vez de gravar "None" como
    # texto — nesse caso o frontend segue no fallback do e-mail.
    nome_bruto = payload.get("name")
    display_name = (
        str(nome_bruto).strip()[:255]
        if isinstance(nome_bruto, str) and nome_bruto.strip()
        else None
    )
    user = await repositorio_usuario.buscar_por_email(session, email)
    if user is None:
        # Primeiro login de qualquer conta @project: provisiona automaticamente
        # com o papel mínimo. Quem precisar de mais acesso recebe depois, via
        # PUT /users/{id}/roles (endpoint de admin já existente). O controle
        # de "só gente da project" já é feito pelo próprio Entra ID (app
        # single-tenant) — este endpoint não precisa reforçar isso de novo.
        user = AuthUser(
            email=email,
            display_name=display_name,
            roles=[automationRole.BASICO],
            confirmed_at=datetime.now(UTC),
            auth_provider="microsoft",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    elif display_name is not None and user.display_name != display_name:
        # O diretório é a fonte de verdade do nome: mudança no AD (casamento,
        # correção de grafia) se propaga no acesso seguinte, sem intervenção.
        # Só grava quando há nome novo E ele mudou — um claim ausente não pode
        # apagar o nome que já está guardado, e escrita à toa em todo login
        # sujaria `updated_at` sem motivo.
        user.display_name = display_name
        await session.commit()
        await session.refresh(user)

    return _auth_success(user)


async def obter_perfil_atual(
    session: AsyncSession, *, user_id: int
) -> AuthUserResponse:
    """Perfil da própria conta autenticada.

    Lê do banco em vez de montar a resposta a partir do access token: papel e
    nome podem ter mudado depois da emissão (promoção por um admin, correção de
    nome no AD), e o perfil é justamente a tela onde ver dado velho incomoda.
    """
    user = await repositorio_usuario.buscar_por_id(session, user_id)
    if user is None:
        # Token válido de conta que não existe mais — o access token sobrevive
        # à exclusão até expirar.
        raise UsuarioNaoEncontradoError()
    return _user_response(user)


async def _jti_vigente(user_id: str) -> str | None:
    # Fail-open: None significa "não há jti guardado", o que leva a aceitar
    # o refresh — queda de Redis não pode virar apagão de login, mesma
    # postura da leitura do corte de sign-out acima.
    try:
        return await get_redis().get(_chave_refresh_jti(user_id))
    except (RedisError, OSError, TimeoutError) as exc:
        logger.warning(
            "Jti vigente indisponível ao validar refresh: %s", type(exc).__name__
        )
        return None


async def _registrar_jti_vigente(user_id: str, jti: str) -> None:
    # Best-effort: falhar em gravar não pode derrubar uma renovação legítima
    # que já foi autorizada.
    try:
        await get_redis().set(
            _chave_refresh_jti(user_id), jti, ex=_REFRESH_JTI_TTL_SECONDS
        )
    except (RedisError, OSError, TimeoutError) as exc:
        logger.warning(
            "Falha ao registrar jti vigente do refresh: %s", type(exc).__name__
        )
        return


async def _revogar_sessao_por_reuso(user_id: str) -> None:
    # Aqui NÃO se levanta RevogacaoIndisponivelError (como sign_out faz)
    # porque /token/refresh só mapeia SessaoInvalidaError e
    # UsuarioNaoEncontradoError — qualquer outra exceção viraria 500; e a
    # rejeição do token reapresentado acontece de todo modo, com ou sem o
    # corte gravado.
    try:
        await get_redis().set(
            _chave_sign_out(user_id),
            str(int(datetime.now(UTC).timestamp())),
            ex=_SIGNED_OUT_TTL_SECONDS,
        )
    except (RedisError, OSError, TimeoutError):
        logger.exception("Falha ao revogar sessão por reuso de refresh token")


async def refresh_token(
    session: AsyncSession, body: RefreshTokenRequest
) -> AuthResponse:
    try:
        payload = decode_refresh_token(body.refresh_token)
    except Exception as exc:
        raise SessaoInvalidaError() from exc

    # Tokens antigos, emitidos antes desta fase, não têm "iat";
    # payload.get("iat", 0) os trata como anteriores a qualquer corte, ou
    # seja, um sign-out os invalida — direção segura e sem ação de migração.
    try:
        cutoff = await get_redis().get(_chave_sign_out(payload["sub"]))
    except (RedisError, OSError, TimeoutError) as exc:
        # Fail-open: queda de Redis não pode virar apagão de login, mesma
        # postura da decisão travada 3 para sign-in. Nunca logar sub/e-mail.
        logger.warning(
            "Corte de sign-out indisponível ao validar refresh: %s",
            type(exc).__name__,
        )
        cutoff = None

    if cutoff is not None and int(payload.get("iat", 0)) < int(cutoff):
        # iat tem resolução de 1 segundo; a comparação é "<" (não "<=") de
        # propósito, para que um sign-in imediatamente após o sign-out não
        # seja invalidado por si mesmo.
        raise SessaoInvalidaError()

    # Teto absoluto de sessão: refresh token teria "exp" de 7 dias renovado a
    # cada uso (janela deslizante, sem fim). auth_time é o instante do login
    # original e não se move a cada renovação — passado o teto, a sessão só
    # volta com um login novo de verdade (Microsoft SSO incluso). Tokens
    # antigos sem "auth_time" caem no "iat" deste próprio token como
    # aproximação segura (não há como recuperar o login original deles).
    auth_time = int(payload.get("auth_time", payload.get("iat", 0)))
    now_ts = int(datetime.now(UTC).timestamp())
    if now_ts - auth_time > get_settings().auth_absolute_session_seconds:
        raise SessaoInvalidaError()

    user_id = int(payload["sub"])
    user = await repositorio_usuario.buscar_por_id(session, user_id)
    if user is None:
        raise UsuarioNaoEncontradoError()

    # Detecção de reuso ("rotation with reuse detection"): a chave de jti é
    # POR USUÁRIO, espelhando a decisão travada 2 do sign-out (revoga todas
    # as sessões do usuário, não uma sessão específica). Consequência aceita:
    # duas sessões simultâneas do mesmo usuário (dois navegadores/
    # dispositivos) que renovem alternadamente são lidas como reuso, e as
    # duas caem — o usuário refaz login. Desenho pedido, não bug: o sign-out
    # aqui já é por usuário, então a postura é consistente.
    # jti_apresentado is None é aceito de propósito, para não invalidar
    # token emitido antes deste deploy — forjar um token sem jti exigiria o
    # JWT_SECRET, que já é o segredo raiz de toda a autenticação, então não
    # há bypass novo.
    jti_apresentado = payload.get("jti")
    jti_guardado = await _jti_vigente(payload["sub"])
    if (
        jti_guardado is not None
        and jti_apresentado is not None
        and jti_apresentado != jti_guardado
    ):
        await _revogar_sessao_por_reuso(payload["sub"])
        logger.warning("Reuso de refresh token detectado; sessão revogada")
        raise SessaoInvalidaError()

    resposta = _auth_success(user, auth_time=auth_time)
    # create_refresh_token não devolve o jti e _auth_success é compartilhado
    # com sign-in/SSO/confirm — mudar a assinatura de qualquer um dos dois
    # sairia do escopo e afetaria fluxos que esta função não deve tocar;
    # decodificar o token recém-emitido é o jeito mais barato de obter o jti
    # sem mexer em fluxo alheio.
    novo_refresh_token = resposta.token.refresh_token
    assert novo_refresh_token is not None  # _auth_success sempre emite refresh_token
    await _registrar_jti_vigente(
        payload["sub"], decode_refresh_token(novo_refresh_token)["jti"]
    )
    return resposta


async def sign_out(current_user: CurrentUser) -> SignOutResponse:
    # Fail-closed: responder 200 sem ter revogado nada mentiria para o
    # usuário sobre a segurança da sessão.
    try:
        await get_redis().set(
            _chave_sign_out(current_user.id),
            str(int(datetime.now(UTC).timestamp())),
            ex=_SIGNED_OUT_TTL_SECONDS,
        )
    except (RedisError, OSError, TimeoutError) as exc:
        logger.exception("Falha ao registrar corte de sign-out")
        raise RevogacaoIndisponivelError() from exc
    return SignOutResponse()


async def list_users_page(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    search: str,
    sort: str,
    order: str,
) -> tuple[list[AuthUser], int]:
    return await repositorio_usuario.listar_pagina(
        session,
        page=page,
        page_size=page_size,
        search=search,
        sort=sort,
        order=order,
    )


async def set_user_roles(
    session: AsyncSession, *, user_id: int, roles: list[str]
) -> AuthUser:
    from app.modules.auth.domain.roles import ALL_automation_ROLES, normalize_roles

    invalid = [r for r in roles if r not in ALL_automation_ROLES]
    if invalid:
        raise PapeisInvalidosError(invalid)

    normalized = normalize_roles(roles)
    user = await repositorio_usuario.buscar_por_id(session, user_id)
    if user is None:
        raise UsuarioNaoEncontradoError()

    return await repositorio_usuario.atualizar_papeis(session, user, normalized)


async def delete_user(session: AsyncSession, *, admin_id: int, user_id: int) -> None:
    if admin_id == user_id:
        raise AutoExclusaoNaoPermitidaError()

    user = await repositorio_usuario.buscar_por_id(session, user_id)
    if user is None:
        raise UsuarioNaoEncontradoError()

    await repositorio_usuario.excluir(session, user)
