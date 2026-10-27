"""Git é a fonte única do código das estratégias: nada de cópia do código no banco nem volume escondendo a imagem."""
import inspect
import re
import subprocess
from pathlib import Path

import pytest

from backend.routers import strategies as strategies_router
from backend.strategies.registry import REGISTRY

ROOT = Path(__file__).resolve().parent.parent
FACTORY = ROOT / "backend" / "strategies" / "factory"


def test_every_registered_strategy_has_its_file_in_the_repo():
    missing = [sid for sid in REGISTRY if not (FACTORY / f"{sid.lower()}.py").exists()]
    assert not missing, f"estratégias registradas sem arquivo versionado: {missing}"


def test_factory_deploy_does_not_store_code_in_the_database():
    src = inspect.getsource(strategies_router.factory_deploy)
    assert "code_py" not in src, "o código vive só em arquivo; code_py é legado e não deve ser gravado"


def test_populate_script_does_not_copy_code_into_the_database():
    src = (ROOT / "scripts" / "populate_strategies_db.py").read_text(encoding="utf-8")
    assert "read_text" not in src and "code_py=None" in src


def test_compose_has_no_volume_shadowing_the_strategies_folder():
    for name in ("docker-compose.prod.yml", "compose.prod.yml", "docker-compose.yml"):
        p = ROOT / name
        if p.exists():
            assert "strategies/factory" not in p.read_text(encoding="utf-8"), \
                f"{name}: um volume em strategies/factory esconde as estratégias da imagem"


def test_deploy_script_aborts_on_container_only_strategies_and_has_no_copy_hacks():
    s = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    assert "ONLY_IN_CONTAINER" in s and "migrate_factory_to_git.sh" in s
    assert "FACTORY_BACKUP" not in s and not re.search(r"docker cp .*strategies/factory", s)


def test_shell_scripts_have_valid_syntax():
    probe = subprocess.run(["bash", "-c", "true"], capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("bash indisponível neste ambiente (ex.: WSL sem distribuição)")
    for name in ("deploy.sh", "scripts/migrate_factory_to_git.sh"):
        r = subprocess.run(["bash", "-n", str(ROOT / name)], capture_output=True, text=True)
        assert r.returncode == 0, f"{name}: {r.stderr}"
