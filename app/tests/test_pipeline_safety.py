from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_ci_executa_todas_migrations_em_banco_descartavel_exclusivo():
    workflow = (_BACKEND_ROOT / ".github" / "workflows" / "pipeline.yml").read_text(
        encoding="utf-8"
    )

    scratch_name = "automation_migration_test_ci_test"
    assert f"CREATE DATABASE {scratch_name}" in workflow
    assert f"127.0.0.1:5432/{scratch_name}" in workflow
    assert "app/tests/test_migration_*.py -q" in workflow
    assert "MIGRATION_TEST_DATABASE_URL:" in workflow


def test_todos_os_testes_destrutivos_exigem_prefixo_scratch_comum():
    migration_tests = sorted(
        (_BACKEND_ROOT / "app" / "tests").glob("test_migration_*.py")
    )
    assert migration_tests

    for test_path in migration_tests:
        source = test_path.read_text(encoding="utf-8")
        assert '_URL_ENV = "MIGRATION_TEST_DATABASE_URL"' in source
        assert '_DATABASE_PREFIX = "automation_migration_test"' in source


def test_dockerfile_nao_confia_em_forwarded_ips_de_qualquer_peer():
    dockerfile = (_BACKEND_ROOT / ".docker" / "Dockerfile").read_text(encoding="utf-8")

    assert "forwarded-allow-ips=*" not in dockerfile
    assert "UVICORN_FORWARDED_ALLOW_IPS" in dockerfile
    assert "30.1.1.0/24,30.1.11.0/24,30.1.21.0/24" in dockerfile
