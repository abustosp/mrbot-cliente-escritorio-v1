from __future__ import annotations

import importlib.util
from pathlib import Path
import re
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-zips-on-tag.yml"
SCANNER = ROOT / ".github" / "scripts" / "scan_release_secrets.py"
WORKFLOW_TEXT = WORKFLOW.read_text(encoding="utf-8")


def _load_scanner():
    spec = importlib.util.spec_from_file_location("release_secret_scanner", SCANNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_package(package: Path, env_text: str | None = None) -> None:
    (package / "mrbot").write_bytes(b"release payload")
    if env_text is not None:
        (package / ".env").write_text(env_text, encoding="utf-8")


def _expect_scan_error(scanner, package: Path, allow_env_template: bool) -> None:
    try:
        scanner.scan(package, allow_env_template=allow_env_template)
    except scanner.ScanError:
        return
    raise AssertionError("El escáner debía rechazar el paquete")


def test_workflow_triggers_existing_date_and_explicit_rollback_tags():
    assert "- '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'" in WORKFLOW_TEXT
    assert "- '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9]'" in WORKFLOW_TEXT
    assert "- 'v1-rollback-v2.*'" in WORKFLOW_TEXT


def test_tag_validation_is_anchored_and_sets_rollback_rc_prerelease():
    assert "[[ \"$TAG\" =~ ^[0-9]{8}(_[0-9]{6})?$ ]]" in WORKFLOW_TEXT
    assert "[[ \"$TAG\" =~ ^v1-rollback-v2\\.[0-9]+\\.[0-9]+(-rc\\.[0-9]+)?$ ]]" in WORKFLOW_TEXT
    assert "RELEASE_KIND=\"rollback\"" in WORKFLOW_TEXT
    assert "PRERELEASE=\"true\"" in WORKFLOW_TEXT
    assert "REF_TYPE=\"${GITHUB_REF_TYPE:-}\"" in WORKFLOW_TEXT
    assert "set -euo pipefail" in WORKFLOW_TEXT

    rollback_tag = re.compile(r"^v1-rollback-v2\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$")
    assert rollback_tag.fullmatch("v1-rollback-v2.0.0")
    assert rollback_tag.fullmatch("v1-rollback-v2.12.34-rc.5")
    assert not rollback_tag.fullmatch("v1-rollback-v2.12")
    assert not rollback_tag.fullmatch("v1-rollback-v2.12.34-rc")
    assert not rollback_tag.fullmatch("v1-rollback-v2.12.34-rc.0.extra")


def test_workflow_publishes_zip_and_sha256_for_both_platforms():
    assert "sha256sum \"$ZIP_NAME\"" in WORKFLOW_TEXT
    assert "Get-FileHash -LiteralPath $destZip -Algorithm SHA256" in WORKFLOW_TEXT
    assert "mrbot-refactored.${{ env.APP_VERSION }}.Linux.zip.sha256" in WORKFLOW_TEXT
    assert "mrbot-refactored.${{ env.APP_VERSION }}.Windows.zip.sha256" in WORKFLOW_TEXT
    assert "release-assets/**/*.sha256" in WORKFLOW_TEXT
    assert "prerelease: ${{ needs.validate-tag.outputs.prerelease }}" in WORKFLOW_TEXT


def test_workflow_ships_only_the_env_template_generated_by_the_build():
    # La plantilla sale de Ejecutable/.env (generado por los build scripts desde .env.example).
    assert 'cp -a "Ejecutable/.env" "$PACKAGE_DIR/.env"' in WORKFLOW_TEXT
    assert 'Copy-Item ".\\Ejecutable\\.env"' in WORKFLOW_TEXT
    # El .env real del repo no se versiona y el workflow no debe copiarlo.
    assert 'cp -a ".env"' not in WORKFLOW_TEXT
    assert WORKFLOW_TEXT.count("scan_release_secrets.py") == 2
    assert WORKFLOW_TEXT.count("--allow-env-template") == 2
    assert "$LASTEXITCODE -ne 0" in WORKFLOW_TEXT


def test_scanner_rejects_env_names_before_content_access():
    scanner = _load_scanner()
    assert scanner.is_env_file(Path(".env"))
    assert scanner.is_env_file(Path(".env.production"))
    assert scanner.is_env_file(Path("settings.env"))
    assert not scanner.is_env_file(Path("settings.ini"))
    assert "if is_env_file(path):" in SCANNER.read_text(encoding="utf-8")
    assert "path.read_bytes()" in SCANNER.read_text(encoding="utf-8")


def test_scanner_fails_closed_on_secret_like_content():
    scanner = _load_scanner()
    with TemporaryDirectory() as package_dir:
        package = Path(package_dir)
        (package / "safe-name.bin").write_bytes(b"AKIA1234567890ABCDEF")

        _expect_scan_error(scanner, package, allow_env_template=False)


def test_scanner_accepts_non_secret_package_content():
    scanner = _load_scanner()
    with TemporaryDirectory() as package_dir:
        package = Path(package_dir)
        (package / "safe-name.bin").write_bytes(b"release payload")

        assert scanner.scan(package) == 1


def test_scanner_rejects_env_by_default_and_allows_it_only_with_the_flag():
    scanner = _load_scanner()
    with TemporaryDirectory() as package_dir:
        package = Path(package_dir)
        _write_package(package, "URL=https://api-bots.mrbot.com.ar\nAPI_KEY=tu_api_key_aqui\n")

        _expect_scan_error(scanner, package, allow_env_template=False)
        assert scanner.scan(package, allow_env_template=True) == 2


def test_scanner_only_allows_a_root_level_dot_env_with_the_flag():
    scanner = _load_scanner()
    for relative_path in (".env.production", "sub/.env", "sub/.env.example"):
        with TemporaryDirectory() as package_dir:
            package = Path(package_dir)
            _write_package(package, "API_KEY=\n")
            target = package / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("API_KEY=\n", encoding="utf-8")

            _expect_scan_error(scanner, package, allow_env_template=True)


def test_scanner_still_flags_secret_like_content_inside_the_allowed_template():
    scanner = _load_scanner()
    with TemporaryDirectory() as package_dir:
        package = Path(package_dir)
        _write_package(package, "API_KEY=1234567890abcdef1234567890abcdef\n")

        _expect_scan_error(scanner, package, allow_env_template=True)


def test_scanner_rejects_malformed_or_empty_allowed_templates():
    scanner = _load_scanner()
    for bad_template in ("# solo comentarios\n", "\n", "esto no es una variable\n"):
        with TemporaryDirectory() as package_dir:
            package = Path(package_dir)
            _write_package(package, bad_template)

            _expect_scan_error(scanner, package, allow_env_template=True)


def test_scanner_cli_wires_the_allow_env_template_flag():
    scanner = _load_scanner()
    with TemporaryDirectory() as package_dir:
        package = Path(package_dir)
        _write_package(package, "URL=https://api-bots.mrbot.com.ar\n")

        assert scanner.main(["--allow-env-template", str(package)]) == 0
        assert scanner.main([str(package)]) == 1
