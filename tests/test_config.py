import threading

import pytest
import yaml

from app import config


def test_pending_gravity_state_is_persistent(monkeypatch, tmp_path):
    pending_file = tmp_path / "pending-gravity.yml"
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PENDING_GRAVITY_FILE", pending_file)

    assert config.load_pending_gravity() == set()
    config.mark_gravity_pending("pihole2")
    config.mark_gravity_pending("pihole1")
    assert config.load_pending_gravity() == {"pihole1", "pihole2"}

    config.clear_gravity_pending("pihole1")
    assert config.load_pending_gravity() == {"pihole2"}
    assert pending_file.stat().st_mode & 0o777 == 0o600


def test_instance_and_secret_files_are_private_on_first_write(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "INSTANCES_FILE", tmp_path / "instances.yml")
    monkeypatch.setattr(config, "SECRETS_FALLBACK_FILE", tmp_path / "secrets.yml")

    config.save_instances([config.Instance("pihole", "http://192.168.1.2")])
    config.CredentialStore._save_fallback({"pihole": "secret"})

    assert config.INSTANCES_FILE.stat().st_mode & 0o777 == 0o600
    assert config.SECRETS_FALLBACK_FILE.stat().st_mode & 0o777 == 0o600


def test_atomic_write_failure_preserves_existing_file_and_removes_temp(tmp_path):
    destination = tmp_path / "instances.yml"
    destination.write_text("existing\n")

    def fail(fh):
        fh.write("replacement\n")
        raise RuntimeError("render failed")

    with pytest.raises(RuntimeError, match="render failed"):
        config._atomic_write(destination, fail)

    assert destination.read_text() == "existing\n"
    assert list(tmp_path.glob(".instances.yml.*.tmp")) == []


def test_concurrent_instance_writes_always_leave_valid_yaml(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "INSTANCES_FILE", tmp_path / "instances.yml")
    failures = []

    def write(name):
        for _ in range(50):
            config.save_instances([config.Instance(name, "http://192.168.1.2")])
            try:
                yaml.safe_load(config.INSTANCES_FILE.read_text())
            except yaml.YAMLError as exc:
                failures.append(exc)

    threads = [threading.Thread(target=write, args=(name,)) for name in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert yaml.safe_load(config.INSTANCES_FILE.read_text())[0]["name"] in {"one", "two"}


def test_load_instances_tolerates_noncanonical_urls(monkeypatch, tmp_path):
    instances_file = tmp_path / "instances.yml"
    instances_file.write_text(
        yaml.safe_dump(
            [
                {"name": "path", "base_url": "http://192.168.1.2/admin"},
                {"name": "spaces", "base_url": " http://pi.hole "},
                {"name": "valid", "base_url": "http://192.168.1.3/"},
            ]
        )
    )
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "INSTANCES_FILE", instances_file)

    instances = config.load_instances()

    assert [instance.base_url for instance in instances] == [
        "http://192.168.1.2/admin",
        "http://pi.hole",
        "http://192.168.1.3",
    ]
