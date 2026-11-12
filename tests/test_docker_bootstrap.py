import io
import zipfile
import pytest
from docker.bootstrap import archive_path, password
from scripts.import_data import SCHEMA


def source_archive():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name in SCHEMA:
            archive.writestr(f"data/{name}.csv", "id\n1\n")
    return output.getvalue()


def test_supplied_archive_takes_precedence_without_network(tmp_path, monkeypatch):
    supplied, cache = tmp_path / "source.zip", tmp_path / "cache.zip"
    supplied.write_bytes(source_archive())
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: pytest.fail("Must use provided archive"))
    assert archive_path(supplied, cache) == supplied
    assert not cache.exists()


def test_download_is_validated_and_reused(tmp_path, monkeypatch):
    monkeypatch.setenv("BI_DATA_URL", "https://example.test/data.zip")
    calls = []

    def download(url, timeout):
        calls.append((url, timeout))
        return io.BytesIO(source_archive())

    monkeypatch.setattr("urllib.request.urlopen", download)
    supplied, cache = tmp_path / "absent.zip", tmp_path / "cache" / "source.zip"
    assert archive_path(supplied, cache) == cache
    assert archive_path(supplied, cache) == cache
    assert len(calls) == 1
    assert not cache.with_suffix(".download").exists()


@pytest.mark.parametrize("content", [b"<html>Not a ZIP</html>", b"PK\x03\x04"])
def test_failed_download_leaves_no_partial_cache(tmp_path, monkeypatch, content):
    monkeypatch.setenv("BI_DATA_URL", "https://example.test/data.zip")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: io.BytesIO(content))
    cache = tmp_path / "cache.zip"
    with pytest.raises(zipfile.BadZipFile):
        archive_path(tmp_path / "absent.zip", cache)
    assert not cache.exists() and not cache.with_suffix(".download").exists()


def test_archive_must_contain_all_eight_tables(tmp_path, monkeypatch):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("data/regions.csv", "id\n1\n")
    monkeypatch.setenv("BI_DATA_URL", "https://example.test/data.zip")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: io.BytesIO(output.getvalue()))
    cache = tmp_path / "cache.zip"
    with pytest.raises(ValueError, match="eight"):
        archive_path(tmp_path / "absent.zip", cache)
    assert not cache.exists() and not cache.with_suffix(".download").exists()


def test_remote_archive_requires_https(tmp_path, monkeypatch):
    monkeypatch.setenv("BI_DATA_URL", "http://example.test/data.zip")
    with pytest.raises(ValueError, match="HTTPS"):
        archive_path(tmp_path / "absent.zip", tmp_path / "cache.zip")


@pytest.mark.parametrize("value", ["", "CHANGE_ME", "too-short"])
def test_password_error_never_contains_the_value(monkeypatch, value):
    monkeypatch.setenv("BI_DB_ADMIN_PASSWORD", value)
    with pytest.raises(ValueError) as error:
        password("BI_DB_ADMIN_PASSWORD")
    assert not value or value not in str(error.value)


def test_generated_password_is_accepted(monkeypatch):
    monkeypatch.setenv("BI_DB_ADMIN_PASSWORD", "a" * 48)
    assert password("BI_DB_ADMIN_PASSWORD") == "a" * 48
