import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_secrets.sh"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    return tmp_path


def test_clean_tree_passes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "note.txt").write_text("没有密钥\n", encoding="utf-8")
    _git(repo, "add", "note.txt")
    result = subprocess.run(["bash", str(SCRIPT)], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_api_key_style_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    secret = "sk-" + ("a" * 24)
    (repo / "leak.py").write_text(f'token = "{secret}"\n', encoding="utf-8")
    _git(repo, "add", "leak.py")
    result = subprocess.run(["bash", str(SCRIPT)], cwd=repo, capture_output=True, text=True)
    assert result.returncode != 0
    assert "API Key" in result.stderr


def test_cookie_style_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    header = "Cook" + "ie: " + ("b" * 12)
    (repo / "leak.txt").write_text(header + "\n", encoding="utf-8")
    _git(repo, "add", "leak.txt")
    result = subprocess.run(["bash", str(SCRIPT)], cwd=repo, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Cookie" in result.stderr


def test_tracked_data_directory_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    data = repo / "data"
    data.mkdir()
    (data / "secret.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-f", "data/secret.txt")
    result = subprocess.run(["bash", str(SCRIPT)], cwd=repo, capture_output=True, text=True)
    assert result.returncode != 0
    assert "data/" in result.stderr
